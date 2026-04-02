from django.conf import settings
from django.db import migrations, models


def move_owner_fk_to_userimages(apps, schema_editor):
    ImageUpload = apps.get_model("app", "ImageUpload")
    UserImages = apps.get_model("app", "UserImages")

    for image in ImageUpload.objects.exclude(user_id__isnull=True):
        user_images, _ = UserImages.objects.get_or_create(user_id=image.user_id)
        user_images.image.add(image)


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0005_convert_users_m2m_to_user_fk"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserImages",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.CASCADE,
                        related_name="uploaded_images",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "image",
                    models.ManyToManyField(
                        blank=True,
                        related_name="user_images",
                        to="app.imageupload",
                    ),
                ),
            ],
        ),
        migrations.RunPython(move_owner_fk_to_userimages, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="imageupload",
            name="user",
        ),
    ]
