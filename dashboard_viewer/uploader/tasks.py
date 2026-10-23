import os

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
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
from .file_handler.errors import TemporaryFailure
from .file_handler.postgres_errors import is_connection_lost
from .file_handler.updates import update_achilles_results_from_df
from .models import AchillesResults, PendingUpload, UploadHistory

logger = get_task_logger(__name__)


@shared_task(bind=True)
def upload_results_file(self, pending_upload_id: int):
    start_time = timezone.now()

    pending_upload = PendingUpload.objects.get(id=pending_upload_id)
    pending_upload.status = PendingUpload.STATE_STARTED
    pending_upload.save()

    self.update_state(state='PROGRESS', meta={'percent': 20, 'step': 'Parsing CSV & Metadata...'})

    data_source = pending_upload.data_source

    logger.info("Started to process file [datasource %d, pending upload %d]", data_source.id, pending_upload_id)

    try:
        check_for_duplicated_files(pending_upload.uploaded_file, data_source.id)

        _, data, valid_df, parsing_errors = extract_data_from_uploaded_file_tolerant(
            pending_upload.uploaded_file
        )

        self.update_state(state='PROGRESS', meta={'percent': 40, 'step': 'Simulating Materialized Views...'})

        view_errors = validate_data_in_existing_mat_views_df(
            data_source.id, valid_df, pending_upload
        )

        all_errors = sort_errors_by_line(parsing_errors + view_errors)
        has_warnings = len(all_errors) > 0

        cache = caches["workers_locks"]

        self.update_state(state='PROGRESS', meta={'percent': 70, 'step': 'Persisting Valid Records...'})

        try:
            cache.incr("celery_workers_updating", ignore_key_check=True)

            with RWLock(
                cache.client.get_client(),
                "celery_worker_updating",
                RWLock.READ,
                expire=None,
            ), cache.lock(
                f"celery_worker_lock_db_{data_source.id}"
            ), transaction.atomic(
                using=router.db_for_write(AchillesResults)
            ), settings.ACHILLES_DB_SQLALCHEMY_ENGINE.connect() as pandas_connection, pandas_connection.begin():

                update_achilles_results_from_df(
                    logger,
                    pending_upload,
                    valid_df,
                    pandas_connection,
                )

                data_source.release_date = data["source_release_date"]
                data_source.save()
                
                pending_upload.uploaded_file.seek(0)

                original_filename = pending_upload.original_filename or (
                    os.path.basename(pending_upload.uploaded_file.name) if pending_upload.uploaded_file else "achilles_results.csv"
                )

                file_hash = _calculate_sha256(pending_upload.uploaded_file.path) if os.path.exists(pending_upload.uploaded_file.path) else None

                logger.debug(
                    "PERFORMANCE: File '%s' (%d rows) processed in %.2f seconds [Datasource ID: %d]",
                    original_filename,
                    len(valid_df),
                    (timezone.now() - pending_upload.upload_date).total_seconds(),
                    data_source.id
                )

        finally:
            workers_updating = cache.decr("celery_workers_updating")

    except Exception as e:
        logger.info("Upload failed [datasource %d, upload %d]", data_source.id, pending_upload_id)

        if is_connection_lost(e) or isinstance(e, TemporaryFailure):
            for alias in ("default", "achilles"):
                try:
                    connections[alias].close()
                except Exception:
                    pass

        PendingUpload.objects.filter(id=pending_upload_id).update(
            status=PendingUpload.STATE_FAILED
        )
        raise e

    ingestion_end_time = timezone.now()
    ingestion_duration = (ingestion_end_time - start_time).total_seconds()

    self.update_state(state='PROGRESS', meta={'percent': 85, 'step': 'Refreshing Dashboard Materialized Views...'})

    if not workers_updating:
        refresh(logger, data_source.id)

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

    with transaction.atomic(using=router.db_for_write(UploadHistory)):
        UploadHistory.objects.create(
            data_source=data_source,
            r_package_version=data["r_package_version"],
            generation_date=data["generation_date"],
            cdm_release_date=data["cdm_release_date"],
            cdm_version=data["cdm_version"],
            vocabulary_version=data["vocabulary_version"],
            uploaded_file=pending_upload.uploaded_file,
            pending_upload_id=pending_upload.id,
            errors_summary={
                "original_filename": original_filename,
                "file_hash": file_hash, 
                "errors": all_errors,
            },
            has_warnings=has_warnings,
        )

    pending_upload.delete()

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
