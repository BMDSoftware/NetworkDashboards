# Generated by Django 2.2.7 on 2020-02-14 11:28

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tabsManager", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tab",
            name="icon",
            field=models.CharField(
                help_text="Font awesome icon v5. Just the end part, e.g. fa-clock-o -> clock-o",
                max_length=20,
            ),
        ),
    ]
