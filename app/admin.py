from django.contrib import admin
from .models import ImageUpload, UserImages
# Register your models here.
admin.site.register(ImageUpload)
admin.site.register(UserImages)