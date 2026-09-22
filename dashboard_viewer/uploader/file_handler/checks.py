import csv
import hashlib
import io
import logging
import os

import numpy
import pandas
from django.conf import settings
from django.core.cache import caches
from django.db import connections
from materialized_queries_manager.models import MaterializedQuery
from redis_rw_lock import RWLock
from uploader.models import AchillesResults, DataSource, UploadHistory

from .errors import (
    DuplicatedMetadataRow,
    EqualFileAlreadyUploaded,
    FileDataCorrupted,
    InvalidCSVFile,
    InvalidFieldValue,
    InvalidFileFormat,
    MissingFieldValue,
    TemporaryFailure,
    translate,
)

logger = logging.getLogger(__name__)

def _generate_file_reader(uploaded_file):
    """
    Receives a python file pointer and returns a pandas csv file reader, along with the columns
     present in the file
    :param uploaded_file: python file pointer of the uploaded file
    """
    columns = [
        "analysis_id",
        "stratum_1",
        "stratum_2",
        "stratum_3",
        "stratum_4",
        "stratum_5",
        "count_value",
    ]

    wrapper = io.TextIOWrapper(uploaded_file)
    csv_reader = csv.reader(wrapper)

    try:
        first_row = next(csv_reader)
    except:  # noqa
        raise InvalidCSVFile(
            "There was an error parsing the provided file. "
            "Uploaded files must be comma-separated values (CSV) files. "
            "If you think this is an error, please contact the system administrator."
        )

    wrapper.detach()

    if len(first_row) == 16:
        columns.extend(
            (
                "min_value",
                "max_value",
                "avg_value",
                "stdev_value",
                "median_value",
                "p10_value",
                "p25_value",
                "p75_value",
                "p90_value",
            )
        )
    elif len(first_row) != 7:
        raise InvalidFileFormat(
            "The provided file has an invalid number of columns. "
            "Make sure you uploaded a valid comma-separated values (CSV) file."
        )

    uploaded_file.seek(0)

    try:
        file_reader = pandas.read_csv(
            uploaded_file,
            header=0,
            dtype=str,
            skip_blank_lines=False,
            index_col=False,
            names=columns,
            chunksize=100,
        )
    except:  # noqa
        raise InvalidCSVFile(
            "There was an error parsing the provided file. "
            "Uploaded files must be comma-separated values (CSV) files. "
            "If you think this is an error, please contact the system administrator."
        )
    else:
        return file_reader, columns


def _check_correct(names, values, check, transform=None):
    """
    Transforms the values of given fields from the uploaded file
     and check if they end up in the desired format

    :param names: names of the fields to check
    :param values: values of the fields to transform and check if they are
     in the right format
    :param transform: callable to transform the values of the
     provided fields
    :param check: callable check if the transform processes generated
     a valid output
    :return: the transformed fields or an error string
    """
    assert len(names) == len(values)

    transformed_elements = [None] * len(names)
    bad_elements = []

    for i, name in enumerate(names):
        transformed = values[i] if not transform else transform(values[i])
        if not check(transformed):
            bad_elements.append(name)
        else:
            transformed_elements[i] = transformed

    if bad_elements:
        return (
            f" {bad_elements[0]} is"
            if len(bad_elements) == 1
            else f"s {', '.join(bad_elements[:-1])} and {bad_elements[-1]} are"
        )

    return transformed_elements


def extract_data_from_uploaded_file(uploaded_file):
    file_reader, columns = _generate_file_reader(uploaded_file)

    types = {
        "analysis_id": numpy.int64,
        "stratum_1": "string",
        "stratum_2": "string",
        "stratum_3": "string",
        "stratum_4": "string",
        "stratum_5": "string",
        "count_value": numpy.int64,
    }
    if len(columns) == 16:
        types.update(
            {
                "min_value": float,
                "max_value": float,
                "avg_value": float,
                "stdev_value": float,
                "median_value": float,
                "p10_value": float,
                "p25_value": float,
                "p75_value": float,
                "p90_value": float,
            },
        )

    metadata = None

    while True:
        try:
            chunk = next(file_reader)
        except ValueError:
            raise InvalidFileFormat(
                "The provided file has an invalid csv format. Make sure is a text file separated"
                " by <b>commas</b> and you either have 7 (regular results file) or 13 (results file"
                " with dist columns) columns."
            )
        except StopIteration:
            break
        except:  # noqa
            raise InvalidCSVFile(
                "There was an error parsing the provided file. "
                "Uploaded files must be comma-separated values (CSV) files. "
                "If you think this is an error, please contact the system administrator."
            )

        if chunk[["analysis_id", "count_value"]].isna().values.any():
            raise InvalidFieldValue(
                'Some rows have null values either on the column "analysis_id" or "count_value".'
            )

        try:
            chunk = chunk.astype(types)
        except OverflowError as exc:
            raise InvalidFieldValue(
                'Some numbers in "analysis_id" or "count_value" are too large to store.'
            ) from exc
        except ValueError as exc:
            raise InvalidFieldValue(
                'The provided file has invalid values on some columns. Remember that only the "stratum_*" columns'
                " accept strings, all the other fields expect numeric types."
            ) from exc

        metadata_rows = chunk[chunk.analysis_id.isin((0, 5000))]

        if metadata is None:
            metadata = metadata_rows
        else:
            metadata = pandas.concat(
                (metadata, metadata_rows), ignore_index=True, copy=False
            )

        output = _check_correct(
            ["0", "5000"],
            (
                metadata[metadata.analysis_id == 0],
                metadata[metadata.analysis_id == 5000],
            ),
            lambda e: len(e) <= 1,
        )
        if isinstance(output, str):
            raise DuplicatedMetadataRow(
                f"Analysis id{output} duplicated on multiple rows. Try (re)running the plugin "
                "<a href='https://github.com/EHDEN/CatalogueExport'>CatalogueExport</a>"
                " on your database."
            )

    analysis_0 = metadata[metadata.analysis_id == 0]
    if analysis_0.empty:
        raise MissingFieldValue(
            "Analysis id 0 is missing. Try (re)running the plugin "
            "<a href='https://github.com/EHDEN/CatalogueExport'>CatalogueExport</a>"
            " on your database."
        )

    analysis_0 = analysis_0.reset_index()
    analysis_5000 = metadata[metadata.analysis_id == 5000].reset_index()

    return {"columns": columns, "types": types}, {
        "generation_date": _get_upload_attr(analysis_0, "stratum_3"),
        "source_release_date": _get_upload_attr(analysis_5000, "stratum_2"),
        "cdm_release_date": _get_upload_attr(analysis_5000, "stratum_3"),
        "cdm_version": _get_upload_attr(analysis_5000, "stratum_4"),
        "r_package_version": _get_upload_attr(analysis_0, "stratum_2"),
        "vocabulary_version": _get_upload_attr(analysis_5000, "stratum_5"),
    }


def _get_upload_attr(analysis, stratum):
    if analysis.empty:
        return None

    value = analysis.loc[0, stratum]

    if pandas.isna(value):
        return None
    return value

def _calculate_sha256(file_path):
    """
    Calculates the SHA-256 checksum of a file in streaming 64 KB chunks.
    Reads the target file in fixed-size blocks to safely compute hashes
    for large files without causing Out-Of-Memory (OOM) errors.
    Args:
        file_path (str | PathLike): The absolute or relative path to the file on disk.
    Returns:
        str | None: The hexadecimal SHA-256 digest string if successful,
                    or None if an IOError occurs while reading the file.
    """
    hasher = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            # Iteratively read 64 KB chunks until EOF (empty byte string)
            # as we might get large files, so it should prevent OOM
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except IOError:
        return None

def check_for_duplicated_files(uploaded_file, data_source_id):
    """
    Verifies that an incoming upload is not an exact duplicate of the latest upload.
    Fetches the most recent successful upload record from `UploadHistory` for the
    specified data source and compares its SHA-256 checksum against the new file.
    Args:
        uploaded_file (FieldFile | File): Django File object for the incoming upload.
        data_source_id (int): Primary key ID of the target DataSource.
    Raises:
        EqualFileAlreadyUploaded: If both files exist and their SHA-256 hashes match.
    """
    try:
        # Retrieve the latest successful upload record for this datasource
        pd = UploadHistory.objects.filter(data_source_id=data_source_id).latest()

        # Validate physical existence of both files before hashing
        if pd.uploaded_file and os.path.exists(pd.uploaded_file.path) and os.path.exists(uploaded_file.path):
            if _calculate_sha256(pd.uploaded_file.path) == _calculate_sha256(uploaded_file.path):
                raise EqualFileAlreadyUploaded("This exact file has already been uploaded for this datasource.")
    except UploadHistory.DoesNotExist:
        # First upload for this datasource; no prior history to compare against
        pass

def _get_mat_view_queries():
    """Extracts view definitions, redirects to staging table"""
    all_mat_views = MaterializedQuery.objects.exclude(matviewname__contains="tmp")
    mat_views = {}

    for mat_view in all_mat_views:
        tmp_mat_view_name = mat_view.to_dict()["matviewname"] + "_tmp"
        # To run the mat views (with data) against the "temporary table"
        # To run for all mat views, as the data source can become with draft equal to true
        tmp_definition = mat_view.to_dict()["definition"].replace(
            "achilles_results", "achilles_results_tmp"
        )
        mat_views[tmp_mat_view_name] = [
            tmp_definition,
        ]
        # since draft can change with time, we must run the queries for all types of draft, namely with draft = true and draft = false
        # We normally don't use draft set to true in the queries, otherwise we would need to test it here also
        if "draft = false" in tmp_definition:
            mat_views[tmp_mat_view_name].append(
                tmp_definition.replace("draft = false", "draft = true")
            )
    return mat_views

def validate_data_in_existing_mat_views(data_source_id, file_metadata, pending_upload):

    cache = caches["workers_locks"]
    ctx = {"ds": data_source_id, "upload": pending_upload.id}

    with RWLock(
        cache.client.get_client(), "celery_worker_updating", RWLock.WRITE, expire=None
        ):
        pending_upload.uploaded_file.seek(0)

        reader = pandas.read_csv(
            pending_upload.uploaded_file,
            header=0,
            dtype=file_metadata["types"],
            skip_blank_lines=False,
            index_col=False,
            names=file_metadata["columns"],
            chunksize=500,
        )

        try:
            mat_views = _get_mat_view_queries()
            _load_staging_table(reader, data_source_id, logger, ctx)
            failed = _probe_charts(mat_views, logger, ctx)

            if failed:
                raise FileDataCorrupted(
                    "Some charts couldn't be built "
                    "from this file. The rows loaded correctly but produced values "
                    "the database can't store or calculate."
                )
        finally:
            _drop_staging_table(logger)

def _load_staging_table(reader, data_source_id, log, ctx):
    with connections["achilles"].cursor() as cursor, \
            settings.ACHILLES_DB_SQLALCHEMY_ENGINE.connect() as pandas_connection, \
            pandas_connection.begin():
        try:
            cursor.execute("DROP TABLE IF EXISTS achilles_results_tmp CASCADE")
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS achilles_results_tmp AS SELECT * FROM "
                + AchillesResults._meta.db_table + " WHERE FALSE"
            )
            cursor.execute("CREATE SEQUENCE IF NOT EXISTS achilles_results_tmp_seq_id")
            cursor.execute(
                "ALTER TABLE achilles_results_tmp ALTER COLUMN id "
                "SET DEFAULT nextval('achilles_results_tmp_seq_id')"
            )
            cursor.execute("ALTER TABLE achilles_results_tmp ALTER COLUMN id SET NOT NULL")

            rows = 0
            for chunk in reader:
                chunk = (chunk[~chunk["stratum_1"].eq("0")]
                         .assign(data_source_id=data_source_id))
                chunk.to_sql("achilles_results_tmp", pandas_connection,
                             if_exists="append", index=False)
                rows += len(chunk)
            log.info("Staged %d rows %s", rows, ctx)
        except Exception as exc:
            raise translate(exc, "loading your data", log, **ctx) from exc

def _probe_charts(mat_views, log, ctx):
    """Build each chart against the staging data. Returns the ones the file broke."""
    failed = []
    with connections["achilles"].cursor() as cursor:
        for name in mat_views:
            for definition in mat_views[name]:
                try:
                    cursor.execute(f"CREATE MATERIALIZED VIEW {name} AS {definition}")
                    cursor.execute(f"DROP MATERIALIZED VIEW {name}")
                except Exception as exc:
                    error = translate(exc, f"verifying and uploading the file", log, view=name, **ctx)
                    if not isinstance(error, TemporaryFailure):
                        try:
                            cursor.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name}")
                        except Exception:
                            log.warning("Could not drop %s after failure", name)

                    # A broken definition or a dead database stops everything,
                    # a bad value is worth collecting so the user sees them all.
                    if not isinstance(error, FileDataCorrupted):
                        raise error from exc # stops immediately the processing
                    failed.append((name, error.what_happened))
                    break

            if failed: # stop the processing at first error for now
                return failed
    return failed

def _drop_staging_table(log):
    try:
        with connections["achilles"].cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS achilles_results_tmp CASCADE")
            cursor.execute("DROP SEQUENCE IF EXISTS achilles_results_tmp_seq_id")
    except Exception:
        log.exception("Failed to drop the staging table")