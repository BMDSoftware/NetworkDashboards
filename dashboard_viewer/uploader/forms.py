import constance
from bootstrap_datepicker_plus import DatePickerInput
from django import forms

from .fields import CoordinatesField
from .models import DatabaseType, DataSource
from django.core.exceptions import ValidationError

class SourceForm(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["database_type"].widget = forms.Select(
            choices=[(obj.type, obj.type) for obj in DatabaseType.objects.all()]
        )

    coordinates = CoordinatesField(
        help_text="Coordinates for the location of the data source"
    )

    class Meta:
        model = DataSource
        fields = ("name", "acronym", "country", "link", "database_type", "hash")
        widgets = {
            "release_date": DatePickerInput(),  # format %m/%d/%Y. Using a ModelForm this can't be changed
            "hash": forms.HiddenInput(),
        }

    def clean_database_type(self):
        value = self.cleaned_data["database_type"].strip().title()
        if not DatabaseType.objects.filter(type=value).exists():
            raise ValidationError(f"Database type '{value}' does not exist.")
        return value


class EditSourceForm(SourceForm):
    class Meta(SourceForm.Meta):
        fields = ("name", "country", "link", "database_type") + (
            ("draft",) if constance.config.UPLOADER_ALLOW_EDIT_DRAFT_STATUS else ()
        )


class AchillesResultsForm(forms.Form):
    if constance.config.ALLOW_ONBOARDING_UPLOAD:
        results_file = forms.FileField(required=False, label='Catalogue Export File')
    else:
        results_file = forms.FileField()

class OnboardingReportForm(forms.Form):
    onboarding_results_file = forms.FileField(required=False, label='Onboarding Report file (Optional)')