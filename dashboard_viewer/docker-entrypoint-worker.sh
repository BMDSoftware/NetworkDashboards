#!/bin/sh

set -x

# clean locks
echo '''from django.core.cache import caches
cache = caches["workers_locks"]
cache.delete_pattern("*")
''' | python manage.py shell

# remove all pending tasks
celery -A dashboard_viewer purge -f

# start unfinished pending uploads
echo '''from uuid import uuid4
from django.db.models import Q
from uploader.models import PendingUpload, UploadHistory
from uploader.tasks import upload_results_file

committed = UploadHistory.objects.filter(pending_upload_id__isnull=False)
recoverable = PendingUpload.objects.filter(
    Q(status__in=[PendingUpload.STATE_PENDING, PendingUpload.STATE_STARTED])
    | Q(status=PendingUpload.STATE_FAILED, pk__in=committed.values("pending_upload_id"))
)
for upload_id in recoverable.values_list("pk", flat=True):
    task_id = str(uuid4())
    PendingUpload.objects.filter(pk=upload_id).update(task_id=task_id)
    print(f"Starting {upload_id}")
    upload_results_file.apply_async(args=[upload_id], task_id=task_id)
''' | python manage.py shell

# 4. start celery
exec celery -A dashboard_viewer worker -Ofair -l INFO
