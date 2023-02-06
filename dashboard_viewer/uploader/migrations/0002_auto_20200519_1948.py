# Generated by Django 2.2.7 on 2020-05-19 19:48

from django.db import migrations, models


def fill_acronym(apps, schema_editor):
    DataSource = apps.get_model("uploader", "DataSource")

    for data_source in DataSource.objects.all():
        data_source.acronym = data_source.slug
        data_source.save()


class Migration(migrations.Migration):
    dependencies = [
        ("uploader", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="datasource",
            name="acronym",
            field=models.CharField(
                default=None,
                null=True,
                help_text="Short label for the data source, containing only letters, numbers, underscores or hyphens.",
                max_length=50,
                unique=True,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(fill_acronym),
        migrations.AlterField(
            model_name="datasource",
            name="acronym",
            field=models.CharField(
                default=None,
                help_text="Short label for the data source, containing only letters, numbers, underscores or hyphens.",
                max_length=50,
                unique=True,
            ),
            preserve_default=False,
        ),
        migrations.RemoveField(
            model_name="datasource",
            name="slug",
        ),
    ]
