import io
import json
import tempfile
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from .models import AnnotationRow, ImageUpload, UserImages

User = get_user_model()


def generate_test_image(name='sample.jpg', image_format='JPEG', color='blue'):
    buffer = BytesIO()
    Image.new('RGB', (32, 32), color=color).save(buffer, format=image_format)
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type=f'image/{image_format.lower()}',
    )


class HomePageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='secret')
        self.client.force_login(self.user)

    def test_home_page_renders(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upload images')

    def test_upload_images_saves_files(self):
        file = generate_test_image()

        response = self.client.post(reverse('upload_images'), {'images': [file]})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(ImageUpload.objects.count(), 1)
        self.assertTrue(ImageUpload.objects.get().original_image)
        self.assertFalse(ImageUpload.objects.get().is_edited)

    def test_upload_images_preserves_bytes_and_resolution(self):
        buffer = BytesIO()
        Image.new('RGB', (1920, 1080), color='red').save(buffer, format='JPEG', quality=92)
        original_bytes = buffer.getvalue()
        file = SimpleUploadedFile('large.jpg', original_bytes, content_type='image/jpeg')

        response = self.client.post(reverse('upload_images'), {'images': [file]})

        self.assertEqual(response.status_code, 201)
        payload = response.json()['images'][0]
        self.assertEqual(payload['width'], 1920)
        self.assertEqual(payload['height'], 1080)
        self.assertEqual(payload['file_size'], len(original_bytes))

        image = ImageUpload.objects.get()
        image.image.open('rb')
        saved_bytes = image.image.read()
        image.image.close()
        self.assertEqual(saved_bytes, original_bytes)

    def test_delete_image_removes_saved_record(self):
        file = generate_test_image()
        image = ImageUpload.objects.create(image=file, original_name='sample.jpg')
        user_images, _ = UserImages.objects.get_or_create(user=self.user)
        user_images.image.add(image)

        response = self.client.post(reverse('delete_image', args=[image.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ImageUpload.objects.count(), 0)

    def test_edit_image_replaces_saved_file(self):
        original_file = generate_test_image()
        image = ImageUpload.objects.create(image=original_file, original_name='sample.jpg')
        user_images, _ = UserImages.objects.get_or_create(user=self.user)
        user_images.image.add(image)

        response = self.client.post(
            reverse('edit_image', args=[image.id]),
            data=json.dumps(
                {
                    'points': [
                        {'x': 0, 'y': 0},
                        {'x': 10, 'y': 0},
                        {'x': 10, 'y': 10},
                    ]
                }
            ),
            content_type='application/json',
        )

        image.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(image.image.name.endswith('.jpg'))
        self.assertTrue(image.is_edited)

    def test_edit_image_preserves_resolution(self):
        buffer = BytesIO()
        Image.new('RGB', (1920, 1080), color='green').save(buffer, format='JPEG', quality=95)
        original_file = SimpleUploadedFile(
            'large.jpg',
            buffer.getvalue(),
            content_type='image/jpeg',
        )
        image = ImageUpload.objects.create(image=original_file, original_name='large.jpg')
        user_images, _ = UserImages.objects.get_or_create(user=self.user)
        user_images.image.add(image)

        response = self.client.post(
            reverse('edit_image', args=[image.id]),
            data=json.dumps(
                {
                    'points': [
                        {'x': 100, 'y': 100},
                        {'x': 400, 'y': 100},
                        {'x': 400, 'y': 400},
                    ]
                }
            ),
            content_type='application/json',
        )

        image.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        with Image.open(image.image) as edited_image:
            self.assertEqual(edited_image.size, (1920, 1080))
            self.assertEqual(edited_image.format, 'JPEG')

    def test_serve_image_returns_bytes_for_linked_user(self):
        file = generate_test_image()
        image = ImageUpload.objects.create(image=file, original_name='sample.jpg')
        user_images, _ = UserImages.objects.get_or_create(user=self.user)
        user_images.image.add(image)

        response = self.client.get(reverse('serve_image', args=[image.id]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content)
        self.assertIn('image/', response['Content-Type'])

    def test_serve_image_download_uses_original_filename(self):
        file = generate_test_image(name='2_fp_img_0165.jpg')
        image = ImageUpload.objects.create(image=file, original_name='2_fp_img_0165.jpg')
        user_images, _ = UserImages.objects.get_or_create(user=self.user)
        user_images.image.add(image)

        response = self.client.get(reverse('serve_image', args=[image.id]), {'download': '1'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('2_fp_img_0165.jpg', response['Content-Disposition'])

    def test_image_payload_uses_serve_url(self):
        file = generate_test_image()
        response = self.client.post(reverse('upload_images'), {'images': [file]})

        payload = response.json()['images'][0]
        self.assertEqual(payload['url'], reverse('serve_image', args=[payload['id']]))

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_edit_image_saves_annotation_coordinates_by_category(self):
        original_file = generate_test_image(color='blue')
        image = ImageUpload.objects.create(image=original_file, original_name='sample.jpg')
        user_images, _ = UserImages.objects.get_or_create(user=self.user)
        user_images.image.add(image)

        response = self.client.post(
            reverse('edit_image', args=[image.id]),
            data=json.dumps(
                {
                    'category': 'signboard',
                    'shapes': [
                        [
                            {'x': 0, 'y': 0},
                            {'x': 10, 'y': 0},
                            {'x': 10, 'y': 10},
                            {'x': 0, 'y': 10},
                        ]
                    ],
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        annotation = AnnotationRow.objects.filter(
            user=self.user,
            category='signboard',
            image_name='sample.jpg',
        ).first()
        self.assertIsNotNone(annotation)
        self.assertEqual(
            annotation.coordinate_text,
            '0,0;10,0;10,10;0,10',
        )

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_edit_image_updates_existing_annotation_row(self):
        original_file = generate_test_image(color='blue')
        image = ImageUpload.objects.create(image=original_file, original_name='sample.jpg')
        user_images, _ = UserImages.objects.get_or_create(user=self.user)
        user_images.image.add(image)

        self.client.post(
            reverse('edit_image', args=[image.id]),
            data=json.dumps(
                {
                    'category': 'signboard',
                    'shapes': [
                        [
                            {'x': 0, 'y': 0},
                            {'x': 10, 'y': 0},
                            {'x': 10, 'y': 10},
                        ]
                    ],
                }
            ),
            content_type='application/json',
        )

        self.client.post(
            reverse('edit_image', args=[image.id]),
            data=json.dumps(
                {
                    'category': 'signboard',
                    'shapes': [
                        [
                            {'x': 5, 'y': 5},
                            {'x': 15, 'y': 5},
                            {'x': 15, 'y': 15},
                        ]
                    ],
                }
            ),
            content_type='application/json',
        )

        rows = AnnotationRow.objects.filter(user=self.user, category='signboard', image_name='sample.jpg')
        self.assertEqual(rows.count(), 1)
        annotation = rows.first()
        self.assertEqual(annotation.coordinate_text, '5,5;15,5;15,15')

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_download_annotations_zip(self):
        image_name = 'sample.jpg'
        AnnotationRow.objects.create(
            user=self.user,
            category='signboard',
            image_name=image_name,
            coordinate_text='0,0;10,0;10,10;0,10',
        )
        AnnotationRow.objects.create(
            user=self.user,
            category='road',
            image_name=image_name,
            coordinate_text='20,20;30,20;30,30;20,30',
        )

        response = self.client.get(reverse('download_annotations'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn('attachment; filename="annotations.zip"', response['Content-Disposition'])

        with ZipFile(io.BytesIO(response.content), 'r') as zip_file:
            namelist = zip_file.namelist()
            self.assertIn('signboard.txt', namelist)
            self.assertIn('road.txt', namelist)
            signboard_data = zip_file.read('signboard.txt').decode('utf-8')
            road_data = zip_file.read('road.txt').decode('utf-8')
            self.assertIn('sample.jpg\t0,0;10,0;10,10;0,10', signboard_data)
            self.assertIn('sample.jpg\t20,20;30,20;30,30;20,30', road_data)

    def test_restore_image_replaces_edited_version_with_original(self):
        original_file = generate_test_image(color='blue')
        image = ImageUpload(original_name='sample.jpg')
        original_bytes = original_file.read()
        image.image.save('sample.jpg', ContentFile(original_bytes), save=False)
        image.save()
        image.original_image.save('original_sample.jpg', ContentFile(original_bytes), save=False)
        image.save(update_fields=['original_image'])
        user_images, _ = UserImages.objects.get_or_create(user=self.user)
        user_images.image.add(image)

        self.client.post(
            reverse('edit_image', args=[image.id]),
            data=json.dumps(
                {
                    'points': [
                        {'x': 0, 'y': 0},
                        {'x': 31, 'y': 0},
                        {'x': 31, 'y': 31},
                    ]
                }
            ),
            content_type='application/json',
        )
        response = self.client.post(reverse('restore_image', args=[image.id]))

        image.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(image.image.name.endswith('.jpg'))
        self.assertFalse(image.is_edited)
