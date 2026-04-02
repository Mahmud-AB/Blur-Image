from django.urls import path

from .views import (
    delete_image,
    delete_all_updated_images,
    download_all_updated_images,
    edit_image,
    home,
    login_user,
    logout_user,
    restore_image,
    signup,
    upload_images,
)

urlpatterns = [
    path('', home, name='home'),
    path('signup/', signup, name='signup'),
    path('login/', login_user, name='login'),
    path('logout/', logout_user, name='logout'),
    path('download-updated/', download_all_updated_images, name='download_all_updated_images'),
    path('delete-updated/', delete_all_updated_images, name='delete_all_updated_images'),
    path('upload/', upload_images, name='upload_images'),
    path('delete/<int:image_id>/', delete_image, name='delete_image'),
    path('edit/<int:image_id>/', edit_image, name='edit_image'),
    path('restore/<int:image_id>/', restore_image, name='restore_image'),
]
