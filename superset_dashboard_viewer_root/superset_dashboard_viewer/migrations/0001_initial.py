# Generated by Django 2.2.5 on 2019-09-16 17:08

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Tab',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.TextField()),
                ('icon', models.TextField()),
                ('url', models.URLField()),
                ('position', models.PositiveIntegerField()),
                ('visible', models.BooleanField()),
            ],
        ),
    ]
