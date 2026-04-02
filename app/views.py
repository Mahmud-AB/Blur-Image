import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.core.files.base import ContentFile
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from PIL import Image, ImageDraw, ImageFilter

from .models import ImageUpload, UserImages


DEFAULT_SUFFIX = ".png"
BLUR_RADIUS = 18
JPEG_SUFFIXES = {".jpg", ".jpeg"}
WEBP_SUFFIX = ".webp"
User = get_user_model()


def _file_suffix(filename):
    return Path(filename).suffix or DEFAULT_SUFFIX


def _output_format_from_name(filename):
    suffix = Path(filename).suffix.lower()
    if suffix in JPEG_SUFFIXES:
        return "JPEG"
    if suffix == WEBP_SUFFIX:
        return "WEBP"
    return "PNG"


def _image_payload(image):
    return {
        "id": image.id,
        "name": image.original_name,
        "url": image.image.url,
        "is_edited": image.is_edited,
    }


def _normalize_points(points):
    normalized_points = []
    for point in points:
        try:
            x = float(point["x"])
            y = float(point["y"])
        except (KeyError, TypeError, ValueError):
            return None
        normalized_points.append((x, y))
    return normalized_points


def _require_auth_json(request):
    if request.user.is_authenticated:
        return None
    return JsonResponse({"error": "Authentication required."}, status=401)


def _images_for_user(user):
    return ImageUpload.objects.filter(user_images__user=user).distinct()


def _updated_images_for_user(user):
    return _images_for_user(user).filter(is_edited=True)


def _link_image_to_user(image, user):
    user_images, _ = UserImages.objects.get_or_create(user=user)
    user_images.image.add(image)


def ensure_original_backup(image):
    if image.original_image:
        return

    image.image.open("rb")
    current_bytes = image.image.read()
    image.image.close()
    backup_name = f"original_{image.id}{_file_suffix(image.original_name)}"
    image.original_image.save(backup_name, ContentFile(current_bytes), save=False)
    image.save(update_fields=["original_image"])


@require_GET
@ensure_csrf_cookie
def home(request):
    auth_error = request.GET.get("auth_error", "")
    images = ImageUpload.objects.none()
    updated_images_count = 0
    if request.user.is_authenticated:
        images = _images_for_user(request.user)
        updated_images_count = images.filter(is_edited=True).count()
    return render(
        request,
        "app/home.html",
        {
            "images": images,
            "auth_error": auth_error,
            "updated_images_count": updated_images_count,
        },
    )


@require_POST
def signup(request):
    username = request.POST.get("username", "").strip()
    password = request.POST.get("password", "")
    if not username or not password:
        return redirect("/?auth_error=Username%20and%20password%20are%20required.")
    if User.objects.filter(username=username).exists():
        return redirect("/?auth_error=Username%20already%20exists.")

    user = User.objects.create_user(username=username, password=password, is_active=True)
    login(request, user)
    return redirect("home")


@require_POST
def login_user(request):
    username = request.POST.get("username", "").strip()
    password = request.POST.get("password", "")
    user = authenticate(request, username=username, password=password)
    if user is None:
        return redirect("/?auth_error=Invalid%20username%20or%20password.")

    login(request, user)
    return redirect("home")


@require_POST
def logout_user(request):
    logout(request)
    return redirect("home")


@require_GET
def download_all_updated_images(request):
    if not request.user.is_authenticated:
        return redirect("/?auth_error=Please%20log%20in%20to%20download%20images.")

    images = _updated_images_for_user(request.user)
    if not images.exists():
        return redirect("home")

    zip_buffer = BytesIO()

    with ZipFile(zip_buffer, "w", compression=ZIP_DEFLATED) as zip_file:
        for image in images:
            image.image.open("rb")
            image_bytes = image.image.read()
            image.image.close()
            filename = Path(image.original_name).name or f"image_{image.id}.png"
            zip_file.writestr(filename, image_bytes)

    zip_buffer.seek(0)
    response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="updated-images.zip"'
    return response


@require_POST
def delete_all_updated_images(request):
    if not request.user.is_authenticated:
        return redirect("/?auth_error=Please%20log%20in%20to%20delete%20images.")

    images = list(_updated_images_for_user(request.user))
    user_links = UserImages.objects.filter(user=request.user)
    for image in images:
        for link in user_links:
            link.image.remove(image)

        if image.user_images.exists():
            continue

        image.image.close()
        if image.original_image:
            image.original_image.close()
        image.image.delete(save=False)
        if image.original_image:
            image.original_image.delete(save=False)
        image.delete()

    return redirect("home")


@require_POST
def upload_images(request):
    auth_response = _require_auth_json(request)
    if auth_response:
        return auth_response

    files = request.FILES.getlist("images")

    if not files:
        return JsonResponse({"error": "No images were uploaded."}, status=400)

    uploaded_images = []
    for file in files:
        file_bytes = file.read()
        suffix = _file_suffix(file.name)
        image = ImageUpload(original_name=file.name)
        image.image.save(file.name, ContentFile(file_bytes), save=False)
        image.save()
        image.original_image.save(
            f"original_{image.id}{suffix}",
            ContentFile(file_bytes),
            save=False,
        )
        image.save(update_fields=["original_image"])
        _link_image_to_user(image, request.user)
        uploaded_images.append(_image_payload(image))

    return JsonResponse({"images": uploaded_images}, status=201)


@require_POST
def delete_image(request, image_id):
    auth_response = _require_auth_json(request)
    if auth_response:
        return auth_response

    image = get_object_or_404(_images_for_user(request.user), id=image_id)

    user_links = UserImages.objects.filter(user=request.user, image=image)
    for link in user_links:
        link.image.remove(image)

    if image.user_images.exists():
        return JsonResponse({"deleted_id": image_id})

    image.image.close()
    if image.original_image:
        image.original_image.close()
    image.image.delete(save=False)
    if image.original_image:
        image.original_image.delete(save=False)
    image.delete()
    return JsonResponse({"deleted_id": image_id})


@require_POST
def edit_image(request, image_id):
    auth_response = _require_auth_json(request)
    if auth_response:
        return auth_response

    image = get_object_or_404(_images_for_user(request.user), id=image_id)
    ensure_original_backup(image)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid edit payload."}, status=400)

    points = payload.get("points", [])
    if len(points) < 3:
        return JsonResponse({"error": "At least 3 points are required."}, status=400)

    image.image.open("rb")
    with Image.open(image.image) as original_image:
        working_image = original_image.convert("RGB")
        blurred_image = working_image.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))

        mask = Image.new("L", working_image.size, 0)
        normalized_points = _normalize_points(points)
        if normalized_points is None:
            image.image.close()
            return JsonResponse({"error": "Invalid point coordinates."}, status=400)

        ImageDraw.Draw(mask).polygon(normalized_points, fill=255)
        result_image = Image.composite(blurred_image, working_image, mask)

        output = BytesIO()
        output_format = _output_format_from_name(image.original_name)
        result_image.save(output, format=output_format)
    image.image.close()

    suffix = _file_suffix(image.original_name)
    replacement_name = f"edited_{image_id}{suffix}"

    image.image.delete(save=False)
    image.image.save(replacement_name, ContentFile(output.getvalue()), save=False)
    image.is_edited = True
    image.save(update_fields=["image", "is_edited"])

    return JsonResponse(_image_payload(image))


@require_POST
def restore_image(request, image_id):
    auth_response = _require_auth_json(request)
    if auth_response:
        return auth_response

    image = get_object_or_404(_images_for_user(request.user), id=image_id)
    ensure_original_backup(image)

    image.original_image.open("rb")
    original_bytes = image.original_image.read()
    image.original_image.close()
    suffix = _file_suffix(image.original_name)
    restored_name = f"restored_{image_id}{suffix}"

    image.image.close()
    image.image.delete(save=False)
    image.image.save(restored_name, ContentFile(original_bytes), save=False)
    image.is_edited = False
    image.save(update_fields=["image", "is_edited"])

    return JsonResponse(_image_payload(image))
