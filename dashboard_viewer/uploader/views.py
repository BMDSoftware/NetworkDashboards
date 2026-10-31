import itertools
import os
import json
import constance
import pandas as pd

from django.contrib import messages
from django.db import router, transaction
from django.forms import fields
from django.http import Http404
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.html import format_html, mark_safe
from django.views.decorators.clickjacking import xframe_options_exempt

from materialized_queries_manager.models import MaterializedQuery
from materialized_queries_manager.tasks import request_refresh
from rest_framework import viewsets
from rest_framework.response import Response
from celery.result import AsyncResult
from . import serializers
from .decorators import uploader_decorator
from .forms import AchillesResultsForm, EditSourceForm, SourceForm, ReportsForm
from .models import Country, DataSource, PendingUpload, UploadHistory, DataReport
from .tasks import upload_results_file
from .file_handler.checks import sort_errors_by_line

PAGE_TITLE = "Dashboard Data Upload"

REPORT_METADATA = {
    'onboarding': {
        'code': DataReport.ReportType.ONBOARDING,
        'title': 'Onboarding Report',
        'caption_key': 'CAPTION_ONBOARDING'
    },
    'analyticalbenchmarks': {
        'code': DataReport.ReportType.ANALYTICAL_BENCHMARK,
        'title': 'Analytical Benchmarks',
        'caption_key': 'CAPTION_ANALYTICAL_BENCHMARK'
    },
    'perinetstudy': {
        'code': DataReport.ReportType.PERINET_STUDY,
        'title': 'PeriNet Study',
        "caption_key": "CAPTION_PERINET_STUDY"
    },
}

def _normalize_errors_summary(raw_errors, fallback_file_name):
    if not raw_errors:
        return fallback_file_name, []

    if isinstance(raw_errors, str):
        try:
            raw_errors = json.loads(raw_errors)
        except json.JSONDecodeError:
            return fallback_file_name, [{
                "line": "N/A",
                "analysis_id": "N/A",
                "reason": str(raw_errors),
            }]

    file_name = fallback_file_name
    errors = []

    if isinstance(raw_errors, dict):
        file_name = raw_errors.get("original_filename") or fallback_file_name

        if isinstance(raw_errors.get("errors"), list):
            errors = raw_errors["errors"]
        elif all(k in raw_errors for k in ("line", "analysis_id", "reason")):
            errors = [raw_errors]
        else:
            errors = [{
                "line": "N/A",
                "analysis_id": "N/A",
                "reason": json.dumps(raw_errors, ensure_ascii=False),
            }]

    elif isinstance(raw_errors, list):
        errors = raw_errors

    else:
        errors = [{
            "line": "N/A",
            "analysis_id": "N/A",
            "reason": str(raw_errors),
        }]

    formatted_errors = []
    for err in errors:
        if isinstance(err, dict):
            raw_reason = str(err.get("reason", ""))
            clean_reason = raw_reason.replace("$", "")
            
            formatted_errors.append({
                "line": err.get("line", "N/A"),
                "analysis_id": err.get("analysis_id", "N/A"),
                "reason": clean_reason,
            })
        else:
            raw_reason = str(err)
            clean_reason = raw_reason.replace("$", "")
            
            formatted_errors.append({
                "line": "N/A",
                "analysis_id": "N/A",
                "reason": clean_reason,
            })

    return file_name, sort_errors_by_line(formatted_errors)


@uploader_decorator
def download_upload_error_report(request, data_source, upload_history_id):
    data_source_obj = get_object_or_404(DataSource, hash=data_source)

    try:
        upload_record = UploadHistory.objects.get(
            data_source=data_source_obj,
            id=upload_history_id,
        )
    except UploadHistory.DoesNotExist:
        upload_record = get_object_or_404(
            UploadHistory,
            data_source=data_source_obj,
            pending_upload_id=upload_history_id,
        )

    fallback_file_name = f"{data_source_obj.acronym}_achilles_results.csv"
    file_name, formatted_errors = _normalize_errors_summary(
        upload_record.errors_summary,
        fallback_file_name,
    )

    timestamp = upload_record.upload_date.strftime("%Y%m%d_%H%M")
    base_file_name = os.path.splitext(file_name)[0]
    download_filename = f"Error_Log_{timestamp}_{base_file_name}.pdf"

    context = {
        "data_source": data_source_obj,
        "upload_record": upload_record,
        "file_name": file_name,
        "errors": formatted_errors,
        "page_title": download_filename,
    }

    response = render(request, "upload_error_report_pdf.html", context)
    response["Content-Disposition"] = f'inline; filename="{download_filename}"'
    return response

@xframe_options_exempt
def upload_achilles_results(request, *args, **kwargs):
    data_source = kwargs.get("data_source")
    try:
        obj_data_source = DataSource.objects.get(hash=data_source)
    except DataSource.DoesNotExist:
        return create_data_source(request, *args, **kwargs)

    if request.method == "POST":
        form = AchillesResultsForm(request.POST, request.FILES)
        report_forms = ReportsForm(request.POST, request.FILES)

        if form.is_valid():
            if request.FILES.get('results_file', None) is not None:
                results_file = request.FILES["results_file"]
                
                pending_upload = PendingUpload.objects.create(
                    data_source=obj_data_source, 
                    uploaded_file=results_file,
                    original_filename=results_file.name
                )

                pending_upload.original_filename = results_file.name

                messages.success(
                    request,
                    "File uploaded with success. The file is being processed...",
                )

                task = upload_results_file.delay(pending_upload.id)
                pending_upload.task_id = task.task_id
                pending_upload.save()

                if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.GET.get('ajax') == 'true':
                    return JsonResponse({
                        'success': True,
                        'upload_id': pending_upload.id
                    })

            report_keys = [
                'onboarding', 
                'analyticalbenchmarks', 
                'perinetstudy'
            ]
            for key in report_keys:
                report_file = request.FILES.get(key)
                metadata = REPORT_METADATA.get(key)
                if report_file is not None and metadata is not None:
                    DataReport.objects.create(
                        data_source=obj_data_source, 
                        uploaded_file=report_file,
                        report_type=metadata['code']
                    )

            return redirect(request.path)

    else:
        form = AchillesResultsForm()
        report_forms = ReportsForm()

    completed_pending_ids = UploadHistory.objects.filter(
        data_source=obj_data_source,
        pending_upload_id__isnull=False
    ).values_list('pending_upload_id', flat=True)

    upload_history = sorted(
        itertools.chain(
            UploadHistory.objects.filter(data_source=obj_data_source),
            PendingUpload.objects.filter(data_source=obj_data_source)
            .exclude(id__in=completed_pending_ids),
            DataReport.objects.filter(data_source=obj_data_source)
        ),
        key=lambda upload: upload.upload_date,
        reverse=True,
    )

    upload_history = list(map(lambda obj: (obj, obj.get_status(), obj.get_report_type_display() if isinstance(obj, DataReport) else 'Catalogue'), upload_history))

    return render(
        request,
        "upload_achilles_results.html",
        {
            "form": form,
            "report_forms": report_forms,
            "obj_data_source": obj_data_source,
            "upload_history": upload_history,
            "submit_button_text": mark_safe("<i class='fas fa-upload'></i> Upload"),
            "constance_config": constance.config,
            "page_title": PAGE_TITLE,
        },
    )


def get_upload_task_status(_request, data_source, upload_id):
    data_source_obj = get_object_or_404(DataSource, hash=data_source)

    try:
        pending_upload = PendingUpload.objects.get(
            id=upload_id, data_source=data_source_obj
        )
    except PendingUpload.DoesNotExist:
        upload = get_object_or_404(
            UploadHistory, data_source=data_source_obj, pending_upload_id=upload_id
        )

        status_text = upload.get_status()
        has_warnings = upload.has_warnings or status_text == "Done with Warnings"

        return JsonResponse(
            {
                "status": status_text,  
                "completed": True,              
                "has_warnings": has_warnings,
                "percent": 100,
                "step_message": "Completed with Warnings!" if has_warnings else "Completed successfully!",
                "data": {
                    "r_package_version": upload.r_package_version,
                    "generation_date": upload.generation_date,
                    "cdm_version": upload.cdm_version,
                    "vocabulary_version": upload.vocabulary_version,
                },
            }
        )

    if pending_upload.status == PendingUpload.STATE_FAILED:
        return JsonResponse(
            {
                "status": "Failed", 
                "completed": True, 
                "failure_msg": pending_upload.failure_message(),
                "percent": 0,
            }
        )

    percent = 20
    step_message = "Processing file..."
    if pending_upload.task_id:
        task_result = AsyncResult(pending_upload.task_id)
        if task_result.state == 'PROGRESS' and isinstance(task_result.info, dict):
            percent = task_result.info.get('percent', 20)
            step_message = task_result.info.get('step', step_message)

    return JsonResponse({
        "status": pending_upload.get_status(),
        "completed": False,
        "percent": percent,
        "step_message": step_message,
    })


def _get_fields_initial_values(request, initial):
    for field_name, field in SourceForm.base_fields.items():
        if isinstance(field, fields.MultiValueField):
            for i in range(len(field.widget.widgets)):
                generated_field_name = f"{field_name}_{i}"
                field_value = request.GET.get(generated_field_name)
                if field_value:
                    initial[generated_field_name] = field_value
        elif field_name == "country" and "country" in request.GET:
            countries_found = Country.objects.filter(
                country__icontains=request.GET["country"]
            )
            if countries_found.count() == 1:
                initial["country"] = countries_found.get().id
        else:
            field_value = request.GET.get(field_name)
            if field_value:
                initial[field_name] = field_value


def _leave_valid_fields_values_only(request, initial, aux_form):
    for field_name, field in SourceForm.base_fields.items():
        if isinstance(field, fields.MultiValueField):
            decompressed = []

            for i in range(len(field.widget.widgets)):
                generated_field_name = f"{field_name}_{i}"
                value = request.GET.get(generated_field_name)
                if value:
                    del initial[generated_field_name]
                    decompressed.append(value)
                else:
                    decompressed = []
                    break

            if decompressed:
                initial[field_name] = field.compress(decompressed)
        else:
            if (
                field_name in aux_form.cleaned_data
                and aux_form.cleaned_data[field_name] not in field.empty_values
            ):
                initial[field_name] = aux_form.cleaned_data[field_name]
            elif field_name in initial:
                del initial[field_name]


@uploader_decorator
@xframe_options_exempt
def create_data_source(request, *_, **kwargs):
    data_source = kwargs.get("data_source")
    if request.method == "GET":
        initial = {}
        if data_source is not None:
            initial["hash"] = data_source

        if request.GET:  
            _get_fields_initial_values(request, initial)

            aux_form = SourceForm(initial)
            if aux_form.is_valid():
                obj = aux_form.save(commit=False)
                lat, lon = aux_form.cleaned_data["coordinates"].split(",")
                obj.latitude, obj.longitude = float(lat), float(lon)
                obj.data_source = data_source
                obj.save()

                return redirect(f"/uploader/{obj.hash}")

            _leave_valid_fields_values_only(request, initial, aux_form)

            form = SourceForm(initial=initial)
            for key in initial:
                form.fields[key].widget.attrs["readonly"] = True

        else:
            form = SourceForm(initial=initial)

        if data_source is not None:
            form.fields["hash"].disabled = True
    elif request.method == "POST":
        post_data = request.POST.copy()
        if "hash" not in post_data and data_source is not None:
            post_data.update({"hash": data_source})

        form = SourceForm(post_data)
        if form.is_valid():
            obj = form.save(commit=False)
            lat, lon = form.cleaned_data["coordinates"].split(",")
            obj.latitude, obj.longitude = float(lat), float(lon)
            obj.data_source = data_source
            obj.save()

            messages.success(
                request,
                format_html(
                    "Data source <b>{}</b> created with success. You may now upload results files.",
                    obj.name,
                ),
            )
            return redirect(f"/uploader/{obj.hash}")

    return render(
        request,
        "data_source.html",
        {
            "form": form,
            "report_forms": None,
            "editing": False,
            "submit_button_text": mark_safe(
                "<i class='fas fa-plus-circle'></i> Create"
            ),
            "constance_config": constance.config,
            "page_title": PAGE_TITLE,
        },
    )


@uploader_decorator
@xframe_options_exempt
def edit_data_source(request, *_, **kwargs):
    data_source = kwargs.get("data_source")
    try:
        data_source = DataSource.objects.get(hash=data_source)
    except DataSource.DoesNotExist:
        messages.error(
            request,
            format_html("No data source with the hash <b>{}</b>", data_source),
        )

        return redirect("/uploader/")

    if request.method == "GET":
        form = EditSourceForm(
            initial={
                "name": data_source.name,
                "release_date": data_source.release_date,
                "database_type": data_source.database_type,
                "country": data_source.country,
                "coordinates": f"{data_source.latitude},{data_source.longitude}",
                "link": data_source.link,
                "draft": data_source.draft,
            }
        )
    elif request.method == "POST":
        form = EditSourceForm(request.POST, instance=data_source)
        if form.is_valid():
            obj = form.save(commit=False)
            lat, lon = form.cleaned_data["coordinates"].split(",")
            obj.latitude, obj.longitude = float(lat), float(lon)
            obj.save()

            messages.success(
                request,
                format_html("Data source <b>{}</b> edited with success.", obj.name),
            )
            return redirect(f"/uploader/{obj.hash}")

    return render(
        request,
        "data_source.html",
        {
            "form": form,
            "editing": True,
            "submit_button_text": mark_safe("<i class='far fa-edit'></i> Edit"),
            "constance_config": constance.config,
            "page_title": PAGE_TITLE,
        },
    )


@uploader_decorator
@xframe_options_exempt
def show_csv_report(request, data_source, report_type):
    data_source_obj = None

    metadata = REPORT_METADATA.get(report_type, None)      
    if metadata is None:
        raise Http404("Invalid report type.")

    caption_key = metadata.get('caption_key')
    report_caption = getattr(constance.config, caption_key, "")

    try:
        data_source_obj = DataSource.objects.get(hash=data_source)
        report_file = DataReport.objects.filter(data_source=data_source_obj.id, report_type=metadata['code']).latest('id').uploaded_file
        df = pd.read_csv(report_file, index_col=0)
    except:
        df = pd.DataFrame([])

    context = {'headers': df.columns.tolist(),
               'body': df.values.tolist(),
               'constance_config': constance.config,
               'obj_data_source': data_source_obj,
               'page_title': metadata['title'],
               'report_caption': report_caption}
    
    return render(request, "report.html", context)

class DataSourceUpdate(viewsets.GenericViewSet):
    authentication_classes = ()
    permission_classes = ()

    lookup_field = "hash"
    serializer_class = serializers.DataSourceSerializer
    queryset = DataSource.objects.all()

    def get_object_for_patch(self):

        queryset = self.filter_queryset(self.get_queryset().select_for_update())

        assert (
            self.lookup_field and not self.lookup_url_kwarg
        ), "Expected lookup_field to be defined and not lookup_url_kwarg."

        filter_kwargs = {self.lookup_field: self.kwargs[self.lookup_field]}
        obj = get_object_or_404(queryset, **filter_kwargs)

        self.check_object_permissions(self.request, obj)

        return obj

    def partial_update(self, request, *_, **__):
        with transaction.atomic(using=router.db_for_write(DataSource)):
            instance = self.get_object_for_patch()
            serializer = self.get_serializer(instance, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()

        request_refresh()

        if getattr(instance, "_prefetched_objects_cache", None):

            instance._prefetched_objects_cache = {}  # noqa

        return Response(serializer.data)
