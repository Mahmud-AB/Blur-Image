from django.contrib import admin

from .models import AnnotationRow, ImageUpload


@admin.register(ImageUpload)
class ImageUploadAdmin(admin.ModelAdmin):
    list_display = ("original_name", "user", "is_edited", "created_at")
    list_filter = ("is_edited",)
    search_fields = ("original_name", "user__username")


@admin.register(AnnotationRow)
class AnnotationRowAdmin(admin.ModelAdmin):
    list_display = ("image_name", "category", "user", "updated_at")
    list_filter = ("category",)
    search_fields = ("image_name", "user__username")
