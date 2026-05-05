import constance
from bootstrap_datepicker_plus import DatePickerInput
from django import forms
from django.core.exceptions import ValidationError

from .fields import CoordinatesField
from .models import DatabaseType, DataSource
from .widgets import ListTextWidget


class SourceForm(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if not constance.config.ALLOW_NEW_DATABASE_TYPES:
            # standard Select dropdown
            self.fields["database_type"].widget = forms.Select(
                choices=[(obj.type, obj.type) for obj in DatabaseType.objects.all()]
            )
        else:
            # Keep ListTextWidget for free-text entry
            self.fields["database_type"].widget = ListTextWidget(DatabaseType.objects)

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
        exists = DatabaseType.objects.filter(type=value).exists()

        # Check if creating new types is allowed
        if not constance.config.ALLOW_NEW_DATABASE_TYPES and not exists:
            raise ValidationError(
                f"Creation of new database types is disabled. "
                f"'{value}' is not a valid existing type."
            )

        return value


class EditSourceForm(SourceForm):
    class Meta(SourceForm.Meta):
        fields = ("name", "country", "link", "database_type") + (
            ("draft",) if constance.config.UPLOADER_ALLOW_EDIT_DRAFT_STATUS else ()
        )


class AchillesResultsForm(forms.Form):
    if constance.config.ALLOW_REPORTS_UPLOAD:
        results_file = forms.FileField(required=False, label='Catalogue Export File')
    else:
        results_file = forms.FileField()

class ReportsForm(forms.Form):
    onboarding = forms.FileField(required=False, label='Onboarding Report file (Optional)')
    analyticalbenchmarks = forms.FileField(required=False, label='Analytical Benchmark upload (Optional)')
    perinetstudy = forms.FileField(required=False, label='PeriNet Study upload (Optional)')