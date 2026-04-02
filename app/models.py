from django.conf import settings
from django.db import models


class UserImages(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='uploaded_images',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    image = models.ManyToManyField(
        'ImageUpload',
        related_name='user_images',
        blank=True,
    )

class ImageUpload(models.Model):
    image = models.FileField(upload_to='uploads/')
    original_image = models.FileField(upload_to='original_uploads/', blank=True, null=True)
    original_name = models.CharField(max_length=255)
    is_edited = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.original_name
