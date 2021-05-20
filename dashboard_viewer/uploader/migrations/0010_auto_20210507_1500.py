# Generated by Django 2.2.17 on 2021-05-07 15:00
import json
import os
import shutil
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def set_hash(apps, schema_editor):
    DataSource = apps.get_model("uploader", "DataSource")
    data_sources = DataSource.objects.all()
    if data_sources.exists():
        achilles_results_storage_path = os.path.join(
            settings.BASE_DIR,
            settings.ACHILLES_RESULTS_STORAGE_PATH,
        )

        try:
            with open("acronym2hash_mappings.json") as mappings_file:
                mappings = json.load(mappings_file)

                for data_source in data_sources:
                    try:
                        data_source.hash = mappings[data_source.acronym]
                    except KeyError:
                        raise KeyError(
                            f'No mapping for data source with acronym "{data_source.acronym}"'
                        )

                DataSource.objects.bulk_update(data_sources)

                # move old achilles results files
                for data_source in data_sources:
                    shutil.move(
                        os.path.join(
                            achilles_results_storage_path,
                            data_source.acronym,
                        ),
                        os.path.join(
                            achilles_results_storage_path,
                            data_source.hash,
                        )
                    )
        except FileNotFoundError:
            raise FileNotFoundError(
                "There are DataSources on the database, for that a mappings file is"
                "required to set their respective hash values"
            )


class Migration(migrations.Migration):
    dependencies = [
        ("uploader", "0009_auto_20210112_1719"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="uploadhistory",
            options={"get_latest_by": "upload_date", "ordering": ("-upload_date",)},
        ),
        migrations.AddField(
            model_name="datasource",
            name="draft",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="datasource",
            name="hash",
            field=models.CharField(
                blank=True,
                default=lambda: uuid.uuid4().hex,
                max_length=255,
                null=False,
                unique=True,
            ),
        ),
        migrations.RunPython(set_hash),
        migrations.AlterField(
            model_name="achillesresults",
            name="data_source",
            field=models.ForeignKey(
                limit_choices_to={"draft": False},
                on_delete=django.db.models.deletion.CASCADE,
                to="uploader.DataSource",
            ),
        ),
        migrations.AlterField(
            model_name="datasource",
            name="release_date",
            field=models.CharField(
                blank=True,
                help_text="Date at which DB is available for research for current release.",
                max_length=50,
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="AchillesResultsDraft",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("analysis_id", models.BigIntegerField()),
                ("stratum_1", models.TextField(null=True)),
                ("stratum_2", models.TextField(null=True)),
                ("stratum_3", models.TextField(null=True)),
                ("stratum_4", models.TextField(null=True)),
                ("stratum_5", models.TextField(null=True)),
                ("count_value", models.BigIntegerField()),
                ("min_value", models.BigIntegerField(null=True)),
                ("max_value", models.BigIntegerField(null=True)),
                ("avg_value", models.FloatField(null=True)),
                ("stdev_value", models.FloatField(null=True)),
                ("median_value", models.BigIntegerField(null=True)),
                ("p10_value", models.BigIntegerField(null=True)),
                ("p25_value", models.BigIntegerField(null=True)),
                ("p75_value", models.BigIntegerField(null=True)),
                ("p90_value", models.BigIntegerField(null=True)),
                (
                    "data_source",
                    models.ForeignKey(
                        limit_choices_to={"draft": True},
                        on_delete=django.db.models.deletion.CASCADE,
                        to="uploader.DataSource",
                    ),
                ),
            ],
            options={
                "db_table": "achilles_results_draft",
            },
        ),
        migrations.AddIndex(
            model_name="achillesresultsdraft",
            index=models.Index(
                fields=["data_source"], name="achilles_re_data_so_009db8_idx"
            ),
        ),
    ]
