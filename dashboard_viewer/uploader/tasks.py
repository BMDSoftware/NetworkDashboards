import os

from celery import shared_task
from celery.utils.log import get_task_logger
from django.core import serializers
from django.core.cache import caches
from django.db import connections
from django.db import router, transaction
from materialized_queries_manager.utils import refresh
from redis_rw_lock import RWLock
from django.utils import timezone

from .file_handler.checks import (
    _calculate_sha256,
    check_for_duplicated_files,
    extract_data_from_uploaded_file_tolerant,
    validate_data_in_existing_mat_views_df,
    sort_errors_by_line,
)
from .file_handler.errors import FileDataCorrupted, NoValidRows, TemporaryFailure
from .file_handler.postgres_errors import is_connection_lost
from .file_handler.updates import update_achilles_results_from_df
from .models import AchillesResults, PendingUpload, UploadHistory

logger = get_task_logger(__name__)


def _update_upload_progress(task, percent, step):
    try:
        task.update_state(state="PROGRESS", meta={"percent": percent, "step": step})
    except Exception:
        logger.warning("Could not update upload progress to %d%%", percent, exc_info=True)


def _finish_persisted_upload(task, pending_upload):
    try:
        _update_upload_progress(task, 85, "Refreshing Dashboard Materialized Views...")
        failed_views = refresh(logger, pending_upload.data_source_id)
        if failed_views:
            raise RuntimeError(f"Materialized views failed to refresh: {', '.join(failed_views)}")
        original = pending_upload.uploaded_file
        # Older history rows can still refer to the pending file.
        if original and not UploadHistory.objects.filter(uploaded_file=original.name).exists():
            original.delete(save=False)
        pending_upload.delete()
    except Exception:
        PendingUpload.objects.filter(pk=pending_upload.pk).update(
            status=PendingUpload.STATE_FAILED
        )
        raise


@shared_task(bind=True)
def upload_results_file(self, pending_upload_id: int):
    start_time = timezone.now()

    pending = PendingUpload.objects.filter(id=pending_upload_id)
    pending_upload = pending.first()
    if pending_upload is None:
        return

    if UploadHistory.objects.filter(
        data_source_id=pending_upload.data_source_id,
        pending_upload_id=pending_upload_id,
    ).exists():
        pending.update(status=PendingUpload.STATE_STARTED)
        _finish_persisted_upload(self, pending_upload)
        return

    if not pending.filter(
        status__in=(PendingUpload.STATE_PENDING, PendingUpload.STATE_STARTED)
    ).update(status=PendingUpload.STATE_STARTED):
        # The web request may have cancelled a task whose publication was uncertain.
        return

    data_source = pending_upload.data_source

    logger.info("Started to process file [datasource %d, pending upload %d]", data_source.id, pending_upload_id)

    valid_df = None
    archived_file = None
    failure_row_errors = []
    try:
        _update_upload_progress(self, 20, "Parsing CSV & Metadata...")

        # To prevent the database owner from uploading the same data to the datasource

        logger.info(
            "Verifying Checksum of the upload file with recent data [datasource %d, pending upload %d]",
            data_source.id,
            pending_upload.id,
        )

        check_for_duplicated_files(pending_upload.uploaded_file, data_source.id)
       
        logger.info(
            "Checking file format and data [datasource %d, pending upload %d]",
            data_source.id,
            pending_upload_id,
        )

        _, data, valid_df, parsing_errors = extract_data_from_uploaded_file_tolerant(
            pending_upload.uploaded_file
        )

        logger.info(
            "Validating if data is not corrupted [datasource %d, pending upload %d]",
            data_source.id,
            pending_upload_id,
        )

        _update_upload_progress(self, 40, "Simulating Materialized Views...")

        view_errors = validate_data_in_existing_mat_views_df(
            data_source.id, valid_df, pending_upload
        )

        all_errors = sort_errors_by_line(parsing_errors + view_errors)
        if valid_df.empty:
            failure_row_errors = all_errors
            raise NoValidRows()

        if any(error["line"] == "N/A" for error in view_errors):
            failure_row_errors = all_errors
            raise FileDataCorrupted(
                "The dashboard queries could not be validated with the remaining rows. "
                "No rows were imported. Existing results were kept.",
                details=("Open the Log to see which queries failed validation.",),
            )

        has_warnings = len(all_errors) > 0

        cache = caches["workers_locks"]

        _update_upload_progress(self, 70, "Persisting Valid Records...")

        cache.incr("celery_workers_updating", ignore_key_check=True)
        try:
            with RWLock(# several workers can update their records in parallel -> same as -> several threads can read from the same file
                cache.client.get_client(),
                "celery_worker_updating",
                RWLock.READ,
                expire=None,
            ), cache.lock(  # but only one worker can make updates associated to a specific data source at the same time
                f"celery_worker_lock_db_{data_source.id}"
            ), transaction.atomic(
                using=router.db_for_write(AchillesResults)
            ):
                # A second delivery of this task may have finished ingestion while we waited.
                if UploadHistory.objects.filter(
                    data_source_id=data_source.id,
                    pending_upload_id=pending_upload_id,
                ).exists():
                    return

                logger.info(
                    "Updating results data [datasource %d, pending upload %d]",
                    data_source.id,
                    pending_upload_id,
                )

                update_achilles_results_from_df(
                    logger,
                    pending_upload,
                    valid_df,
                )

                logger.info(
                    "Saving data source release date [datasource %d, pending upload %d]",
                    data_source.id,
                    pending_upload_id,
                )

                data_source.release_date = data["source_release_date"]
                data_source.save()

                logger.info(
                    "Creating an upload history record [datasource %d, pending upload %d]",
                    data_source.id,
                    pending_upload_id,
                )
                
                original_filename = pending_upload.original_filename or (
                    os.path.basename(pending_upload.uploaded_file.name) if pending_upload.uploaded_file else "achilles_results.csv"
                )

                file_hash = _calculate_sha256(pending_upload.uploaded_file.path) if os.path.exists(pending_upload.uploaded_file.path) else None

                # The archive, new results and history use the same Achilles transaction.
                history = UploadHistory(
                    data_source=data_source,
                    r_package_version=data["r_package_version"],
                    generation_date=data["generation_date"],
                    cdm_release_date=data["cdm_release_date"],
                    cdm_version=data["cdm_version"],
                    vocabulary_version=data["vocabulary_version"],
                    pending_upload_id=pending_upload.id,
                    errors_summary={
                        "original_filename": original_filename,
                        "file_hash": file_hash,
                        "errors": all_errors,
                    },
                    has_warnings=has_warnings,
                )
                with pending_upload.uploaded_file.open("rb") as original:
                    history.uploaded_file.save(os.path.basename(original_filename), original, save=False)
                archived_file = history.uploaded_file
                history.save(force_insert=True)


                logger.debug(
                    "PERFORMANCE: File '%s' (%d rows) processed in %.2f seconds [Datasource ID: %d]",
                    original_filename,
                    len(valid_df),
                    (timezone.now() - pending_upload.upload_date).total_seconds(),
                    data_source.id
                )

        finally:
            cache.decr("celery_workers_updating")

    except Exception as e:
        logger.info("Upload task interrupted [datasource %d, upload %d]", data_source.id, pending_upload_id)

        if is_connection_lost(e) or isinstance(e, TemporaryFailure):
            for alias in ("default", "achilles"):
                try:# The connection might not be able to be reused to update the state of the pending upload,
                    # so we close it here
                    # When pending upload updates its state afterwards, it will create a new connection
                    connections[alias].close()
                except Exception:
                    logger.info("Could not close connection %s", alias, exc_info=True)
                    pass

        if archived_file:
            try:
                # A Redis error can arrive after commit. Keep copies referenced by history.
                if not UploadHistory.objects.filter(uploaded_file=archived_file.name).exists():
                    archived_file.delete(save=False)
            except Exception:
                logger.warning("Could not clean up the upload copy [upload %d]", pending_upload_id, exc_info=True)

        PendingUpload.objects.filter(id=pending_upload_id).update(
            status=PendingUpload.STATE_FAILED, row_errors=failure_row_errors
        )
        raise

    finally:
        if valid_df is not None:
            valid_df.close()

    ingestion_end_time = timezone.now()
    ingestion_duration = (ingestion_end_time - start_time).total_seconds()

    _finish_persisted_upload(self, pending_upload)

    total_end_time = timezone.now()
    refresh_duration = (total_end_time - ingestion_end_time).total_seconds()
    total_duration = (total_end_time - start_time).total_seconds()

    logger.info(
        "[BENCHMARK] File '%s' | Rows: %d | Ingestion Time: %.2fs | MV Refresh Time: %.2fs | Total Time: %.2fs",
        original_filename,
        len(valid_df),
        ingestion_duration,
        refresh_duration,
        total_duration
    )


@shared_task
def delete_datasource(objs):
    cache = caches["workers_locks"]

    cache.incr("celery_workers_updating", ignore_key_check=True)

    try:
        with RWLock(
            cache.client.get_client(),
            "celery_worker_updating",
            RWLock.READ,
            expire=None,
        ):
            objs = serializers.deserialize("json", objs)

            for obj in objs:
                obj = obj.object
                with cache.lock(f"celery_worker_lock_db_{obj.pk}"):
                    obj.delete()
    finally:
        workers_updating = cache.decr("celery_workers_updating")

    if not workers_updating:
        refresh(logger)
