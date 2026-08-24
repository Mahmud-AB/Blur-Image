from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0009_imageupload_user_thumbnail_remove_userimages"),
    ]

    operations = [
        migrations.AlterField(
            model_name="annotationrow",
            name="category",
            field=models.CharField(
                choices=[
                    ("signboard", "Signboard"),
                    ("road", "Road"),
                    ("number_plate", "Number plate"),
                    ("obstacle", "Obstacle"),
                ],
                max_length=32,
            ),
        ),
    ]
