import json
from io import BytesIO
from pathlib import Path
from tempfile import SpooledTemporaryFile
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from openpyxl import Workbook

from .imaging import (
    blur_shapes,
    ensure_thumbnail,
    file_suffix,
    guess_content_type,
    normalize_upload_bytes,
    read_field_bytes,
    read_upload_bytes,
    save_thumbnail,
)
from .models import AnnotationCategory, AnnotationRow, ImageUpload, NO_BLUR_CATEGORIES

User = get_user_model()


def _auth_redirect(message):
    return redirect(f"/?auth_error={quote(message)}")


def _require_auth_json(request):
    if request.user.is_authenticated:
        return None
    return JsonResponse({"error": "Authentication required."}, status=401)


def _user_images(user, edited=None):
    queryset = ImageUpload.objects.filter(user=user)
    if edited is not None:
        queryset = queryset.filter(is_edited=edited)
    return queryset


def _image_url(image):
    return reverse("serve_image", args=[image.id])


def _image_payload(image, width=None, height=None, file_size=None):
    payload = {
        "id": image.id,
        "name": image.original_name,
        "url": _image_url(image),
        "is_edited": image.is_edited,
    }
    if width is not None and height is not None:
        payload["width"] = width
        payload["height"] = height
    if file_size is not None:
        payload["file_size"] = file_size
    return payload


def _purge_image(image):
    for field in (image.image, image.original_image, image.thumbnail):
        if field:
            field.delete(save=False)
    image.delete()


def ensure_original_backup(image):
    if image.original_image:
        return
    current_bytes = read_field_bytes(image.image)
    image.original_image.save(
        f"original_{image.id}{file_suffix(image.original_name)}",
        ContentFile(current_bytes),
        save=False,
    )
    image.save(update_fields=["original_image"])


def _normalize_points(points):
    try:
        return [(float(point["x"]), float(point["y"])) for point in points]
    except (KeyError, TypeError, ValueError):
        return None


def _normalize_category(raw_category):
    if not raw_category:
        return None
    normalized = str(raw_category).strip().lower()
    return normalized if normalized in AnnotationCategory.values else None


def _format_coord(value):
    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:.2f}"


def _save_annotation_rows(image, user, category, shapes):
    if not category or not shapes:
        return
    image_name = Path(image.original_name).name or f"image_{image.id}"
    groups = [
        ";".join(f"{_format_coord(x)},{_format_coord(y)}" for x, y in points)
        for points in shapes
        if len(points) >= 2
    ]
    if not groups:
        return
    AnnotationRow.objects.update_or_create(
        user=user,
        category=category,
        image_name=image_name,
        defaults={"coordinate_text": "|".join(groups)},
    )


def _parse_edit_payload(payload):
    per_category = {}
    raw_shapes = []

    annotations = payload.get("annotations")
    if annotations is not None:
        if not isinstance(annotations, list):
            raise ValueError("Invalid annotations payload.")
        for entry in annotations:
            if not isinstance(entry, dict) or not isinstance(entry.get("shapes"), list):
                raise ValueError("Invalid annotation entry.")
            raw_category = entry.get("category")
            category = _normalize_category(raw_category)
            if raw_category is not None and category is None:
                raise ValueError("Invalid category in annotations.")
            for shape in entry["shapes"]:
                raw_shapes.append(shape)
                if category:
                    per_category.setdefault(category, []).append(shape)
    else:
        shapes = payload.get("shapes")
        if shapes is None:
            points = payload.get("points") or []
            shapes = [points] if points else []
        elif not isinstance(shapes, list):
            raise ValueError("Invalid shapes payload.")
        raw_shapes = shapes
        raw_category = payload.get("category")
        category = _normalize_category(raw_category)
        if raw_category is not None and category is None:
            raise ValueError("Invalid category.")
        if category:
            per_category[category] = list(shapes)

    normalized = []
    for shape in raw_shapes:
        points = _normalize_points(shape)
        if points is None:
            raise ValueError("Invalid point coordinates.")
        if len(points) >= 2:
            normalized.append(points)
    if not normalized:
        raise ValueError("At least one shape with 2 points is required.")

    categorized = {}
    for category, shapes in per_category.items():
        ready = []
        for shape in shapes:
            points = _normalize_points(shape)
            if points and len(points) >= 2:
                ready.append(points)
        if ready:
            categorized[category] = ready
    return normalized, categorized


def _safe_sheet_name(name):
    cleaned = "".join("_" if char in "[]:*?/\\" else char for char in str(name or "")).strip()
    return (cleaned or "Sheet")[:31]


def _annotation_shape_texts(coordinate_text):
    if not coordinate_text:
        return []
    return [part.strip() for part in str(coordinate_text).split("|") if part.strip()]


def _annotations_xlsx_bytes(rows):
    workbook = Workbook()
    default_sheet = workbook.active
    created_sheet = False
    rows_by_category = {}
    for row in rows:
        rows_by_category.setdefault(row.category, []).append(row)

    category_order = list(AnnotationCategory.values)
    for category in rows_by_category:
        if category not in category_order:
            category_order.append(category)

    for category in category_order:
        category_rows = rows_by_category.get(category)
        if not category_rows:
            continue
        worksheet = default_sheet if not created_sheet else workbook.create_sheet()
        worksheet.title = _safe_sheet_name(category)
        created_sheet = True
        worksheet.append(["Image Name", "Coordinates"])
        for row in category_rows:
            for shape_text in _annotation_shape_texts(row.coordinate_text):
                worksheet.append([row.image_name, shape_text])
        worksheet.column_dimensions["A"].width = 40
        worksheet.column_dimensions["B"].width = 80

    if not created_sheet:
        default_sheet.title = "Annotations"
        default_sheet.append(["Image Name", "Coordinates"])

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


@require_GET
@ensure_csrf_cookie
def home(request):
    images = ImageUpload.objects.none()
    updated_images_count = 0
    if request.user.is_authenticated:
        images = _user_images(request.user).only("id", "original_name", "is_edited")
        updated_images_count = images.filter(is_edited=True).count()
    return render(
        request,
        "app/home.html",
        {
            "images": images,
            "auth_error": request.GET.get("auth_error", ""),
            "updated_images_count": updated_images_count,
        },
    )


@require_POST
def signup(request):
    username = request.POST.get("username", "").strip()
    password = request.POST.get("password", "")
    if not username or not password:
        return _auth_redirect("Username and password are required.")
    if User.objects.filter(username=username).exists():
        return _auth_redirect("Username already exists.")
    user = User.objects.create_user(username=username, password=password, is_active=True)
    login(request, user)
    return redirect("home")


@require_POST
def login_user(request):
    user = authenticate(
        request,
        username=request.POST.get("username", "").strip(),
        password=request.POST.get("password", ""),
    )
    if user is None:
        return _auth_redirect("Invalid username or password.")
    login(request, user)
    return redirect("home")


@require_POST
def logout_user(request):
    logout(request)
    return redirect("home")


@require_GET
def serve_image(request, image_id):
    if not request.user.is_authenticated:
        return HttpResponse(status=401)

    image = get_object_or_404(_user_images(request.user), id=image_id)
    if request.GET.get("thumb"):
        ensure_thumbnail(image)
        field = image.thumbnail or image.image
        filename = f"thumb_{Path(image.original_name).stem}.jpg"
    else:
        field = image.image
        filename = Path(image.original_name).name or f"image_{image_id}.png"

    response = FileResponse(field.open("rb"), content_type=guess_content_type(filename))
    response["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response["Pragma"] = "no-cache"
    if request.GET.get("download"):
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_GET
def download_all_updated_images(request):
    if not request.user.is_authenticated:
        return _auth_redirect("Please log in to download images.")

    images = _user_images(request.user, edited=True)
    if not images.exists():
        return redirect("home")

    zip_buffer = SpooledTemporaryFile(max_size=8_000_000)
    with ZipFile(zip_buffer, "w", compression=ZIP_DEFLATED) as zip_file:
        used_names = set()
        for image in images.iterator():
            filename = Path(image.original_name).name or f"image_{image.id}.png"
            if filename in used_names:
                filename = f"{Path(filename).stem}_{image.id}{Path(filename).suffix}"
            used_names.add(filename)
            zip_file.writestr(filename, read_field_bytes(image.image))

    zip_buffer.seek(0)
    return FileResponse(
        zip_buffer,
        as_attachment=True,
        filename="updated-images.zip",
        content_type="application/zip",
    )


@require_GET
def download_annotations(request):
    if not request.user.is_authenticated:
        return _auth_redirect("Please log in to download annotations.")

    rows = AnnotationRow.objects.filter(user=request.user).order_by("category", "image_name")
    download_name = "annotations.xlsx"
    image_id = request.GET.get("image_id")
    if image_id:
        image = get_object_or_404(_user_images(request.user), id=image_id)
        image_name = Path(image.original_name).name or f"image_{image.id}"
        rows = rows.filter(image_name=image_name)
        download_name = f"{Path(image_name).stem or f'image_{image.id}'}_annotations.xlsx"

    if not rows.exists():
        if image_id:
            return HttpResponse(status=404)
        return redirect("home")

    response = HttpResponse(
        _annotations_xlsx_bytes(rows),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{download_name}"'
    return response


@require_POST
def delete_all_updated_images(request):
    if not request.user.is_authenticated:
        return _auth_redirect("Please log in to delete images.")
    for image in _user_images(request.user, edited=True):
        _purge_image(image)
    return redirect("home")


@require_POST
@transaction.atomic
def upload_images(request):
    auth_response = _require_auth_json(request)
    if auth_response:
        return auth_response

    files = request.FILES.getlist("images")
    if not files:
        return JsonResponse({"error": "No images were uploaded."}, status=400)

    uploaded_images = []
    for file in files:
        try:
            file_bytes = read_upload_bytes(file)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        original_name = Path(file.name).name or "image.png"
        try:
            file_bytes, width, height = normalize_upload_bytes(file_bytes, original_name)
        except Exception:
            return JsonResponse({"error": "Could not process uploaded image."}, status=400)

        suffix = file_suffix(original_name)
        image = ImageUpload(original_name=original_name, user=request.user)
        image.save()
        image.image.save(f"upload_{image.id}{suffix}", ContentFile(file_bytes), save=False)
        image.original_image.save(f"original_{image.id}{suffix}", ContentFile(file_bytes), save=False)
        save_thumbnail(image, file_bytes)
        image.save(update_fields=["image", "original_image", "thumbnail"])
        uploaded_images.append(
            _image_payload(image, width=width, height=height, file_size=len(file_bytes))
        )

    return JsonResponse({"images": uploaded_images}, status=201)


@require_POST
def delete_image(request, image_id):
    auth_response = _require_auth_json(request)
    if auth_response:
        return auth_response

    image = get_object_or_404(_user_images(request.user), id=image_id)
    _purge_image(image)
    return JsonResponse({"deleted_id": image_id})


@require_POST
@transaction.atomic
def edit_image(request, image_id):
    auth_response = _require_auth_json(request)
    if auth_response:
        return auth_response

    image = get_object_or_404(_user_images(request.user), id=image_id)
    ensure_original_backup(image)

    try:
        payload = json.loads(request.body.decode("utf-8"))
        shapes, per_category = _parse_edit_payload(payload)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid edit payload."}, status=400)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    to_blur = [
        points
        for category, category_shapes in per_category.items()
        if category not in NO_BLUR_CATEGORIES
        for points in category_shapes
    ]
    if not per_category:
        to_blur = shapes

    if to_blur:
        source = image.image if image.is_edited else image.original_image
        result_bytes = blur_shapes(read_field_bytes(source), image.original_name, to_blur)

        suffix = file_suffix(image.original_name)
        image.image.delete(save=False)
        image.image.save(f"edited_{image_id}{suffix}", ContentFile(result_bytes), save=False)
        save_thumbnail(image, result_bytes)
        image.is_edited = True
        image.save(update_fields=["image", "thumbnail", "is_edited"])

    for category, category_shapes in per_category.items():
        _save_annotation_rows(image, request.user, category, category_shapes)

    return JsonResponse(_image_payload(image))


@require_POST
def restore_image(request, image_id):
    auth_response = _require_auth_json(request)
    if auth_response:
        return auth_response

    image = get_object_or_404(_user_images(request.user), id=image_id)
    ensure_original_backup(image)

    original_bytes = read_field_bytes(image.original_image)
    suffix = file_suffix(image.original_name)
    image.image.delete(save=False)
    image.image.save(f"restored_{image_id}{suffix}", ContentFile(original_bytes), save=False)
    save_thumbnail(image, original_bytes)
    image.is_edited = False
    image.save(update_fields=["image", "thumbnail", "is_edited"])
    return JsonResponse(_image_payload(image))
