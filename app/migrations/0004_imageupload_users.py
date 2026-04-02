from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0003_imageupload_is_edited"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="imageupload",
            name="users",
            field=models.ManyToManyField(
                blank=True,
                related_name="uploaded_images",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
