import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def copy_owners_from_userimages(apps, schema_editor):
    ImageUpload = apps.get_model("app", "ImageUpload")
    UserImages = apps.get_model("app", "UserImages")
    for link in UserImages.objects.all().iterator():
        if link.user_id is None:
            continue
        for image in link.image.all().iterator():
            if image.user_id is None:
                image.user_id = link.user_id
                image.save(update_fields=["user_id"])


def reverse_owners_to_userimages(apps, schema_editor):
    ImageUpload = apps.get_model("app", "ImageUpload")
    UserImages = apps.get_model("app", "UserImages")
    for image in ImageUpload.objects.exclude(user_id=None).iterator():
        link, _ = UserImages.objects.get_or_create(user_id=image.user_id)
        link.image.add(image)


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0008_alter_annotationrow_category"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="imageupload",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="images",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="imageupload",
            name="thumbnail",
            field=models.FileField(blank=True, null=True, upload_to="thumbs/"),
        ),
        migrations.RunPython(copy_owners_from_userimages, reverse_owners_to_userimages),
        migrations.DeleteModel(name="UserImages"),
    ]
