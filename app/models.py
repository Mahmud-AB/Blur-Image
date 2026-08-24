from django.conf import settings
from django.db import models


class ImageUpload(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="images",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    image = models.FileField(upload_to="uploads/")
    original_image = models.FileField(upload_to="original_uploads/", blank=True, null=True)
    thumbnail = models.FileField(upload_to="thumbs/", blank=True, null=True)
    original_name = models.CharField(max_length=255)
    is_edited = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name


class AnnotationCategory(models.TextChoices):
    SIGNBOARD = "signboard", "Signboard"
    ROAD = "road", "Road"
    NUMBER_PLATE = "number_plate", "Number plate"
    OBSTACLE = "obstacle", "Obstacle"


# Categories that are recorded but not blurred on the image.
NO_BLUR_CATEGORIES = frozenset({
    AnnotationCategory.ROAD,
    AnnotationCategory.OBSTACLE,
})


class AnnotationRow(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="annotation_rows",
        on_delete=models.CASCADE,
    )
    category = models.CharField(max_length=32, choices=AnnotationCategory.choices)
    image_name = models.CharField(max_length=255)
    coordinate_text = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "category", "image_name")
        ordering = ["category", "image_name"]

    def __str__(self):
        return f"{self.user} {self.category} {self.image_name}"
