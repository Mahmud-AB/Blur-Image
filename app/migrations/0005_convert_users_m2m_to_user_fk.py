from django.conf import settings
from django.db import migrations, models


def copy_first_user_to_fk(apps, schema_editor):
    ImageUpload = apps.get_model("app", "ImageUpload")
    through_model = ImageUpload.users.through

    for image in ImageUpload.objects.all():
        relation = (
            through_model.objects
            .filter(imageupload_id=image.id)
            .order_by("id")
            .first()
        )
        if relation:
            image.user_id = relation.user_id
            image.save(update_fields=["user"])


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0004_imageupload_users"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="imageupload",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.CASCADE,
                related_name="uploaded_images",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(copy_first_user_to_fk, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="imageupload",
            name="users",
        ),
    ]
