from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("uploader", "0022_pendingupload_original_filename")]

    operations = [
        migrations.AddField(
            model_name="pendingupload",
            name="row_errors",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
