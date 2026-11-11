import io
import json
import hashlib
import logging
from contextlib import contextmanager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import Mock, PropertyMock, patch

import numpy
import pandas
from constance.test import override_config
from django.conf import settings
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import override_settings, tag, RequestFactory, SimpleTestCase, TestCase, TransactionTestCase

from .file_handler.checks import (
    ValidatedRows,
    _get_mat_view_queries,
    _probe_charts,
    check_for_duplicated_files,
    extract_data_from_uploaded_file_tolerant,
    iter_data_chunks,
    validate_data_in_existing_mat_views_df,
)
from .file_handler.errors import (
    FileDataCorrupted,
    NoValidRows,
    TemporaryFailure,
    DuplicatedMetadataRow,
    EqualFileAlreadyUploaded,
    InvalidFileFormat,
    MissingFieldValue,
)
from .file_handler.updates import update_achilles_results_from_df
from .models import (
    AchillesResults,
    AchillesResultsArchive,
    Country,
    DataReport,
    DataSource,
    DatabaseType,
    PendingUpload,
    UploadHistory,
)
from .tasks import upload_results_file
from .views import get_upload_task_status, upload_achilles_results

@tag("third-party-app")
@skipUnless(not settings.SINGLE_APPLICATION_MODE, "Requires multi-application mode at startup.")
@override_settings(ALLOWED_HOSTS=["*"])
class UploaderRestrictedAccess(TestCase):
    databases = "__all__"
    fixtures = ("countries", "two_data_sources")

    def test_block_if_wrong_host(self):
        wrong_host = "other.example" if settings.MAIN_APPLICATION_HOST != "other.example" else "another.example"
        response = self.client.get("/uploader/test1/", HTTP_HOST=wrong_host)
        self.assertEqual(response.status_code, 403)

    def test_not_block_if_correct_host(self):
        response = self.client.get("/uploader/test1/", HTTP_HOST=settings.MAIN_APPLICATION_HOST)
        self.assertEqual(response.status_code, 200)


@skipUnless(settings.SINGLE_APPLICATION_MODE, "Requires single-application mode at startup.")
@override_settings(ALLOWED_HOSTS=["some.domain.com"])
class UploaderNonRestrictedAccess(TestCase):
    databases = "__all__"
    fixtures = ("countries", "two_data_sources")

    def test_not_block_if_single_application(self):
        response = self.client.get("/uploader/test1/", HTTP_HOST="some.domain.com")
        self.assertEqual(response.status_code, 200)


class DataSourceCreatorTestCase(TestCase):
    databases = "__all__"
    fixtures = ("countries",)

    def create_source(self):
        return DataSource.objects.create(
            name="New source",
            acronym="new-source",
            database_type="New test database type",
            country=Country.objects.first(),
            latitude=0,
            longitude=0,
        )

    @override_config(ALLOW_NEW_DATABASE_TYPES=True)
    def test_create_datasource_with_new_type(self):
        source = self.create_source()
        self.assertTrue(DataSource.objects.filter(pk=source.pk).exists())
        self.assertTrue(DatabaseType.objects.filter(type=source.database_type).exists())

    @override_config(ALLOW_NEW_DATABASE_TYPES=False)
    def test_create_datasource_with_new_type_is_blocked(self):
        with self.assertRaises(ValueError):
            self.create_source()
        self.assertFalse(DataSource.objects.filter(acronym="new-source").exists())


@override_config(ALLOW_NEW_DATABASE_TYPES=True)
class UpdateAchillesResultsDataTestCase(TransactionTestCase):
    databases = "__all__"

    file_metadata = {
        "columns": [
            "analysis_id",
            "stratum_1",
            "stratum_2",
            "stratum_3",
            "stratum_4",
            "stratum_5",
            "count_value",
        ],
        "types": {
            "analysis_id": numpy.int64,
            "stratum_1": "string",
            "stratum_2": "string",
            "stratum_3": "string",
            "stratum_4": "string",
            "stratum_5": "string",
            "count_value": numpy.int64,
        },
    }
    file = io.StringIO(
        ",".join(file_metadata["columns"]) + "\n" "0,,,,,,1000\n" "5000,,,,,,1001\n"
    )

    fixtures = ("countries", "two_data_sources")

    def __init__(self, *args, **kwargs):
        super(UpdateAchillesResultsDataTestCase, self).__init__(*args, **kwargs)
        self._logger = logging.getLogger(UpdateAchillesResultsDataTestCase.__name__)
        self._pending_upload = PendingUpload(
            id=1,
            uploaded_file=self.file,
        )

    def setUp(self) -> None:
        self._pending_upload.data_source = DataSource.objects.get(acronym="test1")
        self.file.seek(0)
        self.valid_df = pandas.read_csv(self.file, dtype=self.file_metadata["types"])

    def test_move_records_of_only_one_db(self):
        first = self._pending_upload.data_source
        other = DataSource.objects.get(acronym="test2")
        for source in (first, other):
            self._pending_upload.data_source = source
            update_achilles_results_from_df(self._logger, self._pending_upload, self.valid_df)
            UploadHistory.objects.create(data_source=source)

        self._pending_upload.data_source = first
        update_achilles_results_from_df(
            self._logger, self._pending_upload, self.valid_df.assign(count_value=42),
        )

        self.assertEqual(
            list(AchillesResults.objects.filter(data_source=first).values_list("count_value", flat=True)),
            [42, 42],
        )
        self.assertCountEqual(
            AchillesResults.objects.filter(data_source=other).values_list("count_value", flat=True),
            [1000, 1001],
        )
        self.assertEqual(AchillesResultsArchive.objects.filter(data_source=first).count(), 2)
        self.assertFalse(AchillesResultsArchive.objects.filter(data_source=other).exists())

    def test_processing_excludes_stratum1_zero_but_keeps_metadata(self):
        csv = VALID_CSV_7.replace(b"0,,v1.0", b"0,0,v1.0", 1)
        csv += b"101,0,5,0,,,2000\n101,1,5,0,,,3000\n"
        _, metadata, rows, errors = extract_data_from_uploaded_file_tolerant(io.BytesIO(csv))
        self.addCleanup(rows.close)
        self.assertEqual(errors, [])
        self.assertEqual(metadata["r_package_version"], "v1.0")
        self.assertEqual([index for index, _row in rows.iterrows()], [1, 3])

        update_achilles_results_from_df(self._logger, self._pending_upload, rows)

        result = AchillesResults.objects.get(
            data_source=self._pending_upload.data_source, analysis_id=101,
        )
        self.assertEqual(result.stratum_1, "1")
        self.assertEqual(result.count_value, 3000)


VALID_CSV_7 = (
    "analysis_id,stratum_1,stratum_2,stratum_3,stratum_4,stratum_5,count_value\n"
    "0,,v1.0,2020-01-01,,,1000\n"
    "5000,,2020-01-01,2020-01-01,v5.3,v5.0,1001\n"
).encode("utf8")

VALID_CSV_16 = (
    "analysis_id,stratum_1,stratum_2,stratum_3,stratum_4,stratum_5,count_value,min_value,max_value,avg_value,stdev_value,median_value,p10_value,p25_value,p75_value,p90_value\n"
    "0,,v1.0,2020-01-01,,,1000,,,,,,,,,\n"
    "5000,,2020-01-01,2020-01-01,v5.3,v5.0,1001,4,4.5,2,5,5.3,3,3,4,3.44\n"
).encode("utf8")


class ExtractDataFromUploadedFileTestCase(SimpleTestCase):
    def parse(self, csv):
        specs, metadata, rows, errors = extract_data_from_uploaded_file_tolerant(io.BytesIO(csv))
        self.addCleanup(rows.close)
        return specs, metadata, rows, errors

    def test_valid_exports_preserve_columns_and_metadata(self):
        for csv, column_count in ((VALID_CSV_7, 7), (VALID_CSV_16, 16)):
            with self.subTest(columns=column_count):
                specs, metadata, rows, errors = self.parse(csv)
                self.assertEqual(len(specs["columns"]), column_count)
                self.assertEqual(len(rows), 2)
                self.assertEqual(errors, [])
                self.assertEqual(metadata["generation_date"], "2020-01-01")
                self.assertEqual(metadata["cdm_version"], "v5.3")

    def test_numeric_metadata_ids_and_historical_dates_are_accepted(self):
        csv = VALID_CSV_7.replace(b"0,,v1.0,2020-01-01", b"+0,,v1.0,02-FEV-20", 1)
        csv = csv.replace(b"5000,,2020-01-01,2020-01-01", b"005000,,20201202.0,2020-01-01", 1)
        _, metadata, rows, errors = self.parse(csv)

        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 2)
        self.assertEqual(metadata["generation_date"], "02-FEV-20")
        self.assertEqual(metadata["source_release_date"], "20201202.0")
        self.assertEqual(metadata["cdm_version"], "v5.3")

    def test_invalid_column_count(self):
        with self.assertRaises(InvalidFileFormat):
            self.parse(b"analysis_id,count_value\n0,1000\n")

    def test_invalid_and_missing_counts_are_reported(self):
        for count in (b"notanumber", b""):
            with self.subTest(count=count):
                _, _, rows, errors = self.parse(VALID_CSV_7 + b"101,,,,,," + count + b"\n")
                self.assertEqual(len(rows), 2)
                self.assertTrue(any(error["line"] == 4 and "count_value" in error["reason"] for error in errors))

    def test_missing_analysis_id_0(self):
        header = VALID_CSV_7.splitlines(keepends=True)[0]
        with self.assertRaises(MissingFieldValue):
            self.parse(header + b"1,,,,,,1\n")

    def test_duplicated_analysis_id_0(self):
        with self.assertRaises(DuplicatedMetadataRow):
            self.parse(VALID_CSV_7 + b"00,,v1.0,2020-01-01,,,1000\n")


class UploadRegressionTests(TransactionTestCase):
    databases = "__all__"
    fixtures = ("countries", "two_data_sources")

    def setUp(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.source = DataSource.objects.get(acronym="test1")
        DatabaseType.objects.get_or_create(type=self.source.database_type)

    def make_upload(self, name, count):
        csv = (
            "analysis_id,stratum_1,stratum_2,stratum_3,stratum_4,stratum_5,count_value\n"
            f"0,,3,,,,{count}\n"
            f"101,2,5,0,,,{count}\n"
        )
        return PendingUpload.objects.create(
            data_source=self.source,
            original_filename=name,
            uploaded_file=SimpleUploadedFile(name, csv.encode()),
        )

    def test_checksum_compares_only_the_latest_successful_upload(self):
        incoming = self.make_upload("duplicate.csv", 2202)
        with incoming.uploaded_file.open("rb") as source:
            checksum = hashlib.sha256(source.read()).hexdigest()
        UploadHistory.objects.create(
            data_source=self.source, errors_summary={"file_hash": checksum},
        )
        with self.assertRaises(EqualFileAlreadyUploaded):
            check_for_duplicated_files(incoming.uploaded_file, self.source.pk)

        UploadHistory.objects.create(
            data_source=self.source, errors_summary={"file_hash": "different-checksum"},
        )
        check_for_duplicated_files(incoming.uploaded_file, self.source.pk)

    def test_failed_history_write_rolls_back_replacement(self):
        upload = self.make_upload("rollback.csv", 2202)
        previous_history = UploadHistory.objects.create(data_source=self.source)
        previous = AchillesResults.objects.create(
            data_source=self.source, analysis_id=101, count_value=2000,
        )
        cache = Mock()
        cache.lock.return_value = nullcontext()
        with patch("uploader.tasks.caches", {"workers_locks": cache}), patch(
            "uploader.tasks.RWLock", return_value=nullcontext()
        ), patch(
            "uploader.tasks.validate_data_in_existing_mat_views_df", return_value=[]
        ), patch(
            "uploader.tasks.UploadHistory.save", side_effect=RuntimeError("history write failed")
        ), patch("uploader.tasks.refresh") as refresh_views, patch.object(
            upload_results_file, "update_state"
        ):
            with self.assertRaisesMessage(RuntimeError, "history write failed"):
                upload_results_file.run(upload.pk)

        refresh_views.assert_not_called()
        previous.refresh_from_db()
        upload.refresh_from_db()
        self.assertEqual(previous.count_value, 2000)
        self.assertEqual(upload.status, PendingUpload.STATE_FAILED)
        self.assertTrue(upload.uploaded_file.storage.exists(upload.uploaded_file.name))
        self.assertFalse(list(Path(settings.MEDIA_ROOT).glob("**/success/*.csv")))
        self.assertEqual(
            list(AchillesResults.objects.filter(data_source=self.source).values_list("pk", flat=True)),
            [previous.pk],
        )
        self.assertFalse(AchillesResultsArchive.objects.filter(data_source=self.source).exists())
        self.assertEqual(
            list(UploadHistory.objects.filter(data_source=self.source).values_list("pk", flat=True)),
            [previous_history.pk],
        )

    def test_archive_uses_first_history_when_second_upload_runs_during_finalization(self):
        first = self.make_upload("a.csv", 2101)
        second = self.make_upload("b.csv", 2202)
        storage = first.uploaded_file.storage
        pending_paths = (first.uploaded_file.name, second.uploaded_file.name)
        source_locked = False
        refresh_started = []

        @contextmanager
        def lock_source(_key):
            nonlocal source_locked
            self.assertFalse(source_locked)
            source_locked = True
            try:
                yield
            finally:
                source_locked = False

        def refresh(_logger, _source_id):
            self.assertFalse(source_locked)
            refresh_started.append(True)
            if len(refresh_started) == 1:
                self.assertTrue(PendingUpload.objects.filter(pk=first.pk).exists())
                upload_results_file.run(second.pk)

        cache = Mock()
        cache.lock.side_effect = lock_source
        with patch("uploader.tasks.caches", {"workers_locks": cache}), patch(
            "uploader.tasks.RWLock", return_value=nullcontext()
        ), patch(
            "uploader.tasks.validate_data_in_existing_mat_views_df", return_value=[]
        ), patch("uploader.tasks.refresh", side_effect=refresh), patch.object(
            upload_results_file, "update_state"
        ):
            upload_results_file.run(first.pk)

        history = UploadHistory.objects.get(pending_upload_id=first.pk)
        self.assertEqual(history.errors_summary["original_filename"], "a.csv")
        for upload_id, pending_path in zip((first.pk, second.pk), pending_paths):
            accepted = UploadHistory.objects.get(pending_upload_id=upload_id)
            self.assertEqual(Path(accepted.uploaded_file.name).parent.name, "success")
            self.assertTrue(storage.exists(accepted.uploaded_file.name))
            with accepted.uploaded_file.open("rb") as copied:
                self.assertEqual(hashlib.sha256(copied.read()).hexdigest(), accepted.errors_summary["file_hash"])
            self.assertFalse(storage.exists(pending_path))
        self.assertEqual(len(refresh_started), 2)
        self.assertEqual(
            list(AchillesResults.objects.filter(
                data_source=self.source, analysis_id=101
            ).values_list("count_value", flat=True)),
            [2202],
        )
        self.assertEqual(
            list(AchillesResultsArchive.objects.filter(
                data_source=self.source, analysis_id=101
            ).values_list("count_value", "upload_info_id")),
            [(2101, history.pk)],
        )

    def test_zero_protected_by_nullif_produces_no_probe_error(self):
        definition = (
            "WITH achilles_results(count_value) AS (VALUES (0)) "
            "SELECT 300 / NULLIF(count_value, 0) AS ratio FROM achilles_results"
        )
        query = Mock()
        query.to_dict.return_value = {"matviewname": "nullif_regression", "definition": definition}
        with patch("uploader.file_handler.checks.MaterializedQuery.objects.exclude", return_value=[query]):
            views = _get_mat_view_queries()

        with connections["achilles"].cursor() as cursor:
            cursor.execute(views["nullif_regression_tmp"][0])
            self.assertEqual(cursor.fetchone(), (None,))

        rows = pandas.DataFrame({"analysis_id": [1], "count_value": [0]})
        errors, rejected = _probe_charts(views, rows, Mock(), {})
        self.assertEqual(errors, {})
        self.assertEqual(rejected, set())

    def test_refresh_failure_preserves_committed_upload_and_can_resume(self):
        self.check_post_commit_recovery(False)

    def test_redis_failure_preserves_committed_upload_and_can_resume(self):
        self.check_post_commit_recovery(True)

    def check_post_commit_recovery(self, redis_failure):
        upload = self.make_upload("recovery.csv", 2202)
        cache = Mock()
        cache.lock.return_value = nullcontext()
        if redis_failure:
            cache.decr.side_effect = RuntimeError("Redis unavailable")
        refresh_result = [] if redis_failure else ["example_view"]
        with patch("uploader.tasks.caches", {"workers_locks": cache}), patch(
            "uploader.tasks.RWLock", return_value=nullcontext()
        ), patch(
            "uploader.tasks.validate_data_in_existing_mat_views_df", return_value=[]
        ), patch("uploader.tasks.refresh", return_value=refresh_result) as refresh_views, patch.object(
            upload_results_file, "update_state"
        ):
            with self.assertRaises(RuntimeError):
                upload_results_file.run(upload.pk)
            upload.refresh_from_db()
            self.assertEqual(upload.get_status(), "Finalization failed")
            self.assertIn("The data was saved", upload.failure_message())
            history = UploadHistory.objects.get(pending_upload_id=upload.pk)

            refresh_views.return_value = []
            with patch("uploader.tasks.update_achilles_results_from_df") as replace_rows:
                upload_results_file.run(upload.pk)
                replace_rows.assert_not_called()

        self.assertFalse(PendingUpload.objects.filter(pk=upload.pk).exists())
        self.assertEqual(UploadHistory.objects.get(pending_upload_id=upload.pk).pk, history.pk)
        self.assertEqual(AchillesResults.objects.get(data_source=self.source, analysis_id=101).count_value, 2202)

    def test_group_rejection_reports_each_dropped_row(self):
        rows = pandas.DataFrame({"analysis_id": [101, 101], "count_value": [1500000000, 1500000000]})
        views = {
            "overflow_regression_tmp": [
                "SELECT sum(count_value)::integer AS total FROM achilles_results_tmp"
            ]
        }
        with patch("uploader.file_handler.checks.caches", {"workers_locks": Mock()}), patch(
            "uploader.file_handler.checks.RWLock", return_value=nullcontext()
        ), patch(
            "uploader.file_handler.checks._get_mat_view_queries", return_value=views
        ), patch(
            "uploader.file_handler.checks._load_staging_table_from_df",
            side_effect=self.load_staging_for_test,
        ):
            errors = validate_data_in_existing_mat_views_df(self.source.pk, rows, Mock())

        self.assertTrue(rows.empty)
        self.assertEqual({error["line"] for error in errors}, {2, 3})

    @staticmethod
    def load_staging_for_test(rows, _source_id, _logger, _context):
        with connections["achilles"].cursor() as cursor:
            cursor.execute(
                "CREATE TABLE achilles_results_tmp "
                "(source_line integer, analysis_id bigint, count_value bigint)"
            )
            cursor.executemany(
                "INSERT INTO achilles_results_tmp VALUES (%s, %s, %s)",
                [(int(index) + 2, int(row.analysis_id), int(row.count_value))
                 for index, row in rows.iterrows()],
            )

    def test_independent_zeros_are_reported_and_unrelated_zero_is_kept(self):
        rows = pandas.DataFrame({
            "analysis_id": [101, 101, 101, 202],
            "count_value": [0, 0, 1, 0],
        })
        views = {
            "division_regression_tmp": [
                "WITH selected AS (SELECT source_line, count_value "
                "FROM achilles_results_tmp WHERE analysis_id = 101) "
                "SELECT source_line, 1 / count_value AS ratio FROM selected"
            ]
        }
        with patch("uploader.file_handler.checks.caches", {"workers_locks": Mock()}), patch(
            "uploader.file_handler.checks.RWLock", return_value=nullcontext()
        ), patch(
            "uploader.file_handler.checks._get_mat_view_queries", return_value=views
        ), patch(
            "uploader.file_handler.checks._load_staging_table_from_df",
            side_effect=self.load_staging_for_test,
        ):
            errors = validate_data_in_existing_mat_views_df(self.source.pk, rows, Mock())

        self.assertEqual([error["line"] for error in errors], [2, 3])
        self.assertEqual(list(rows["count_value"]), [1, 0])

    def test_post_filter_validation_failure_preserves_previous_results(self):
        upload = self.make_upload("unresolved.csv", 1234)
        active = AchillesResults.objects.create(
            data_source=self.source, analysis_id=101, count_value=2000,
        )
        csv = (
            "analysis_id,stratum_1,stratum_2,stratum_3,stratum_4,stratum_5,count_value\n"
            "0,,3,,,,100\n"
            "101,2,5,0,,,0\n"
            "101,1,5,0,,,1\n"
        )
        upload.uploaded_file.save("unresolved.csv", SimpleUploadedFile("unresolved.csv", csv.encode()))
        views = {
            "division_regression_tmp": [
                "SELECT source_line, 1 / count_value AS ratio "
                "FROM achilles_results_tmp WHERE analysis_id = 101"
            ],
            "aggregate_regression_tmp": [
                "SELECT 1 / (sum(count_value) - count(*)) AS ratio "
                "FROM achilles_results_tmp WHERE analysis_id = 101"
            ],
        }
        with patch("uploader.file_handler.checks.caches", {"workers_locks": Mock()}), patch(
            "uploader.file_handler.checks.RWLock", return_value=nullcontext()
        ), patch(
            "uploader.file_handler.checks._get_mat_view_queries", return_value=views
        ), patch(
            "uploader.file_handler.checks._load_staging_table_from_df",
            side_effect=self.load_staging_for_test,
        ), patch.object(upload_results_file, "update_state"):
            with self.assertRaises(FileDataCorrupted):
                upload_results_file.run(upload.pk)

        active.refresh_from_db()
        upload.refresh_from_db()
        self.assertEqual(active.count_value, 2000)
        self.assertEqual(upload.status, PendingUpload.STATE_FAILED)
        self.assertTrue(any(
            e["line"] == "N/A" and "aggregate_regression" in e["reason"]
            for e in upload.row_errors
        ))
        self.assertFalse(UploadHistory.objects.filter(pending_upload_id=upload.pk).exists())

    def test_unconfirmed_publication_does_not_leave_an_idle_pending_upload(self):
        request = RequestFactory().post(
            "/uploader/test1/?ajax=true",
            {"results_file": SimpleUploadedFile("sample.csv", b"sample")},
            HTTP_HOST=settings.MAIN_APPLICATION_HOST or "testserver",
        )
        with patch(
            "uploader.views.upload_results_file.apply_async", side_effect=RuntimeError("broker unavailable")
        ), patch("uploader.views.messages.error") as show_error:
            response = upload_achilles_results(request, data_source=self.source.hash)

        upload = PendingUpload.objects.get(data_source=self.source)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(upload.status, PendingUpload.STATE_FAILED)
        self.assertIn("The upload was not scheduled", upload.failure_message())
        show_error.assert_called_once()
        with patch.object(upload_results_file, "update_state") as progress:
            upload_results_file.run(upload.pk)
        progress.assert_not_called()

    def test_failed_progress_write_does_not_prevent_ingestion(self):
        upload = self.make_upload("progress.csv", 2202)
        cache = Mock()
        cache.lock.return_value = nullcontext()
        with patch("uploader.tasks.caches", {"workers_locks": cache}), patch(
            "uploader.tasks.RWLock", return_value=nullcontext()
        ), patch(
            "uploader.tasks.validate_data_in_existing_mat_views_df", return_value=[]
        ), patch("uploader.tasks.refresh", return_value=[]), patch.object(
            upload_results_file, "update_state", side_effect=RuntimeError("progress backend unavailable")
        ):
            upload_results_file.run(upload.pk)

        self.assertFalse(PendingUpload.objects.filter(pk=upload.pk).exists())
        self.assertTrue(UploadHistory.objects.filter(pending_upload_id=upload.pk).exists())
        self.assertEqual(
            AchillesResults.objects.get(data_source=self.source, analysis_id=101).count_value,
            2202,
        )

    def test_validation_timeout_preserves_previous_results(self):
        active = AchillesResults.objects.create(
            data_source=self.source, analysis_id=101, count_value=2000,
        )
        csv = (
            "analysis_id,stratum_1,stratum_2,stratum_3,stratum_4,stratum_5,count_value\n"
            "0,,3,,,,100\n"
            "101,2,5,0,,,0\n"
            "101,1,5,0,,,1\n"
        )

        def sql_error(state):
            error = RuntimeError(f"simulated SQLSTATE {state}")
            error.pgcode = state
            return error

        timeout = sql_error("57014")
        overflow = sql_error("22003")
        division_by_zero = sql_error("22012")
        scenarios = (
            ("initial", [timeout], []),
            ("row localization", [overflow], [None, overflow, timeout]),
            ("zero localization", [division_by_zero], [timeout]),
            ("revalidation", [overflow, timeout], [None, overflow, overflow, None]),
        )
        views = {"timeout_regression_tmp": ["SELECT 1 AS value"]}
        for phase, view_results, probe_results in scenarios:
            with self.subTest(phase=phase):
                upload = PendingUpload.objects.create(
                    data_source=self.source,
                    uploaded_file=SimpleUploadedFile("timeout.csv", csv.encode()),
                )
                with patch("uploader.file_handler.checks.caches", {"workers_locks": Mock()}), patch(
                    "uploader.file_handler.checks.RWLock", return_value=nullcontext()
                ), patch(
                    "uploader.file_handler.checks._get_mat_view_queries", return_value=views
                ), patch(
                    "uploader.file_handler.checks._load_staging_table_from_df",
                    side_effect=self.load_staging_for_test,
                ), patch(
                    "uploader.file_handler.checks._check_staged_view", side_effect=view_results
                ), patch(
                    "uploader.file_handler.checks._run_probe", side_effect=probe_results
                ), patch.object(upload_results_file, "update_state"):
                    with self.assertRaises(TemporaryFailure) as failure:
                        upload_results_file.run(upload.pk)

                self.assertIs(failure.exception.__cause__, timeout)
                active.refresh_from_db()
                upload.refresh_from_db()
                self.assertEqual(active.count_value, 2000)
                self.assertEqual(upload.status, PendingUpload.STATE_FAILED)
                self.assertFalse(UploadHistory.objects.filter(pending_upload_id=upload.pk).exists())

    def test_status_remains_available_when_progress_backend_fails(self):
        upload = self.make_upload("status.csv", 2202)
        PendingUpload.objects.filter(pk=upload.pk).update(
            status=PendingUpload.STATE_STARTED,
            task_id="progress-unavailable",
        )
        request = RequestFactory().get("/")
        result = Mock()
        type(result).state = PropertyMock(side_effect=RuntimeError("backend unavailable"))
        with patch("uploader.views.AsyncResult", return_value=result):
            response = get_upload_task_status(request, self.source.hash, upload.pk)
            self.assertEqual(response.status_code, 200)
            status = json.loads(response.content)
            self.assertEqual(status["status"], "Started")
            self.assertFalse(status["completed"])
            self.assertEqual(status["percent"], 20)

            UploadHistory.objects.create(data_source=self.source, pending_upload_id=upload.pk)
            response = get_upload_task_status(request, self.source.hash, upload.pk)
            status = json.loads(response.content)
            self.assertEqual(status["status"], "Started")
            self.assertFalse(status["completed"])
            self.assertEqual(status["percent"], 85)

    def test_fully_rejected_csv_identifies_lines_and_preserves_data(self):
        upload = self.make_upload("invalid.csv", "notanumber")
        active = AchillesResults.objects.create(data_source=self.source, analysis_id=101, count_value=2000)
        with patch(
            "uploader.tasks.validate_data_in_existing_mat_views_df", return_value=[]
        ), patch.object(upload_results_file, "update_state"):
            with self.assertRaises(NoValidRows):
                upload_results_file.run(upload.pk)

        upload.refresh_from_db()
        self.assertEqual(upload.status, PendingUpload.STATE_FAILED)
        self.assertEqual(
            [(error["line"], error["analysis_id"]) for error in upload.row_errors],
            [(2, "0"), (3, "101")],
        )
        self.assertTrue(all("count_value" in error["reason"] for error in upload.row_errors))
        self.assertIn("Open the Log", upload.failure_message())
        active.refresh_from_db()
        self.assertEqual(active.count_value, 2000)
        self.assertFalse(UploadHistory.objects.filter(pending_upload_id=upload.pk).exists())


class ValidatedRowsTests(SimpleTestCase):
    def setUp(self):
        self.rows = ValidatedRows()
        self.addCleanup(self.rows.close)

    def test_round_trip_preserves_types_indices_and_chunk_boundaries(self):
        for indices in ([0, 2], [101, 104]):
            self.rows.append(pandas.DataFrame({
                "analysis_id": pandas.array([1, 2], dtype="int64"),
                "stratum_1": pandas.array(["0", None], dtype="string"),
                "count_value": pandas.array([0, 42], dtype="int64"),
            }, index=indices))
        chunks = list(iter_data_chunks(self.rows))
        self.assertEqual([list(c.index) for c in chunks], [[0, 2], [101, 104]])
        self.assertEqual(len(self.rows), 4)
        self.assertEqual(str(chunks[0].stratum_1.dtype), "string")
        self.assertEqual(chunks[0].loc[0, "stratum_1"], "0")
        self.assertTrue(pandas.isna(chunks[0].loc[2, "stratum_1"]))

    def test_removal_is_persisted_across_reads_and_preserves_csv_line_numbers(self):
        self.rows.append(pandas.DataFrame({"analysis_id": [1, 2]}, index=[0, 101]))
        self.rows.drop(index=[0, 999], inplace=True)
        for _ in range(2):
            self.assertEqual([idx + 2 for idx, row in self.rows.iterrows()], [103])
        self.assertEqual(len(self.rows), 1)
        self.rows.drop(index=[101], inplace=True)
        self.assertTrue(self.rows.empty)
        self.assertEqual(list(self.rows.iter_chunks()), [])

    def test_empty_input_and_cleanup(self):
        self.rows.append(pandas.DataFrame())
        self.assertTrue(self.rows.empty)
        directory = Path(self.rows._directory.name)
        self.rows.close()
        self.assertFalse(directory.exists())


@override_settings(ALLOWED_HOSTS=["*"])
class UploaderViewsTestCase(TransactionTestCase):
    databases = "__all__"
    fixtures = ("countries", "two_data_sources")

    def setUp(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        media_settings = override_settings(MEDIA_ROOT=media.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.data_source = DataSource.objects.get(acronym="test1")
        self.host = settings.MAIN_APPLICATION_HOST if not settings.SINGLE_APPLICATION_MODE else "testserver"

    def test_error_reports_show_rejected_lines_for_completed_and_failed_uploads(self):
        errors = [{"line": 5, "analysis_id": "101", "reason": "A required count is missing"}]
        history = UploadHistory.objects.create(
            data_source=self.data_source,
            errors_summary={"original_filename": "partial.csv", "errors": errors},
            has_warnings=True,
        )
        failed = PendingUpload.objects.create(
            data_source=self.data_source, status=PendingUpload.STATE_FAILED,
            uploaded_file=SimpleUploadedFile("failed.csv", VALID_CSV_7),
            original_filename="failed.csv", row_errors=errors,
        )

        uploads = (
            (f"{history.pk}/error-report/", "partial.csv"),
            (f"{failed.pk}/failed-error-report/", "failed.csv"),
        )
        for url, filename in uploads:
            with self.subTest(filename=filename):
                response = self.client.get(
                    f"/uploader/{self.data_source.hash}/upload/{url}", HTTP_HOST=self.host,
                )
                self.assertContains(response, filename)
                self.assertContains(response, "Row 5")
                self.assertContains(response, "A required count is missing")
                self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_upload_data_report_view(self):
        results_file = SimpleUploadedFile("dummy.csv", VALID_CSV_7)
        report_file = SimpleUploadedFile("onboarding.csv", b"col1,col2\nval1,val2")
        with patch("uploader.views.upload_results_file.apply_async") as enqueue:
            response = self.client.post(
                f"/uploader/{self.data_source.hash}/?ajax=true",
                {"results_file": results_file, "onboarding": report_file},
                HTTP_HOST=self.host,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        enqueue.assert_called_once()
        self.assertTrue(
            DataReport.objects.filter(
                data_source=self.data_source,
                report_type=DataReport.ReportType.ONBOARDING,
            ).exists()
        )

    def test_failed_report_save_leaves_the_csv_scheduled(self):
        with patch("uploader.views.upload_results_file.apply_async") as enqueue, patch(
            "uploader.views.DataReport.objects.create", side_effect=OSError("report storage unavailable")
        ) as save_report:
            response = self.client.post(
                f"/uploader/{self.data_source.hash}/?ajax=true",
                {
                    "results_file": SimpleUploadedFile("dummy.csv", VALID_CSV_7),
                    "onboarding": SimpleUploadedFile("onboarding.csv", b"col1,col2"),
                },
                HTTP_HOST=self.host,
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        pending = PendingUpload.objects.get(pk=response.json()["upload_id"])
        self.assertTrue(pending.task_id)
        enqueue.assert_called_once_with(args=[pending.pk], task_id=pending.task_id)
        save_report.assert_called_once()
        self.assertFalse(DataReport.objects.filter(data_source=self.data_source).exists())
        self.assertTrue(any(
            "Could not save the Onboarding Report" in str(message)
            for message in get_messages(response.wsgi_request)
        ))
