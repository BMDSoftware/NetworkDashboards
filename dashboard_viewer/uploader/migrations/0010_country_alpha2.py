# Generated by Django 2.2.17 on 2021-04-20 21:52

import json
from os import path

from django.conf import settings
from django.db import migrations, models


def fill_apha2(apps, schema_editor):
    changes = {
        "Aland Islands": "Åland Islands",
        "Bolivia (Plurinational State of)": "Bolivia, Plurinational State of",
        "Cabo Verde": "Cape Verde",
        "Congo (Democratic Republic of the)": "Congo, the Democratic Republic of the",
        "Holy See": "Holy See (Vatican City State)",
        "Iran (Islamic Republic of)": "Iran, Islamic Republic of",
        "Korea (Democratic People's Republic of)": "Korea, Democratic People's Republic of",
        "Korea (Republic of)": "Korea, Republic of",
        "Macedonia (the former Yugoslav Republic of)": "Macedonia, the Former Yugoslav Republic of",
        "Micronesia (Federated States of)": "Micronesia, Federated States of",
        "Moldova (Republic of)": "Moldova, Republic of",
        "United Kingdom of Great Britain and Northern Ireland": "United Kingdom",
        "United States of America": "United States",
        "Venezuela (Bolivarian Republic of)": "Venezuela, Bolivarian Republic of",
        "Virgin Islands (British)": "Virgin Islands, British",
        "Virgin Islands (U.S.)": "Virgin Islands, U.S.",
    }

    with open(
        path.join(settings.BASE_DIR, "uploader", "fixtures", "countries.json")
    ) as countries_file:
        countries_records = json.load(countries_file)

    countries_data = dict()
    for record in countries_records:
        fields = record["fields"]
        countries_data[fields["country"]] = fields["alpha2"]

    Country = apps.get_model("uploader", "Country")
    for country in Country.objects.all():
        if country.country not in countries_data:
            new_name = changes[country.country]

            country.country = new_name

        country.alpha2 = countries_data[country.country]

        country.save()


class Migration(migrations.Migration):

    dependencies = [
        ("uploader", "0009_auto_20210112_1719"),
    ]

    operations = [
        migrations.AddField(
            model_name="country",
            name="alpha2",
            field=models.CharField(
                default="", help_text="ISO 3166-1 Alpha-2 Code", max_length=3
            ),
            preserve_default=False,
        ),
        migrations.RunPython(fill_apha2),
        migrations.AlterField(
            model_name="country",
            name="alpha2",
            field=models.CharField(
                default="",
                help_text="ISO 3166-1 Alpha-2 Code",
                max_length=3,
                unique=True,
            ),
            preserve_default=False,
        ),
    ]
