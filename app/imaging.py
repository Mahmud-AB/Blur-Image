import math
import mimetypes
from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageDraw, ImageFilter, ImageOps

DEFAULT_SUFFIX = ".png"
BLUR_RADIUS = 22
BLUR_PAD = math.ceil(BLUR_RADIUS * 3)
THUMB_MAX = 216
JPEG_QUALITY = 100
WEBP_QUALITY = 100
JPEG_SUFFIXES = {".jpg", ".jpeg"}


def file_suffix(filename):
    return Path(filename).suffix or DEFAULT_SUFFIX


def output_format(filename):
    suffix = Path(filename).suffix.lower()
    if suffix in JPEG_SUFFIXES:
        return "JPEG"
    if suffix == ".webp":
        return "WEBP"
    return "PNG"


def guess_content_type(filename):
    content_type, _ = mimetypes.guess_type(filename)
    return content_type or "application/octet-stream"


def read_upload_bytes(uploaded_file):
    uploaded_file.seek(0)
    file_bytes = b"".join(uploaded_file.chunks())
    if uploaded_file.size and len(file_bytes) != uploaded_file.size:
        raise ValueError("Incomplete image upload.")
    return file_bytes


def read_field_bytes(field):
    field.open("rb")
    try:
        return field.read()
    finally:
        field.close()


def _needs_exif_transpose(image):
    try:
        orientation = image.getexif().get(0x0112, 1)
    except Exception:
        return False
    return orientation not in (None, 1)


def _save_options(source, format_name):
    options = {}
    if format_name == "JPEG":
        options = {
            "quality": source.info.get("quality", JPEG_QUALITY),
            "subsampling": source.info.get("subsampling", 0),
            "optimize": False,
        }
        if "exif" in source.info:
            options["exif"] = source.info["exif"]
    elif format_name == "WEBP":
        options = {
            "lossless": True,
            "quality": source.info.get("quality", WEBP_QUALITY),
            "method": 6,
        }
    elif format_name == "PNG":
        options = {"compress_level": source.info.get("compress_level", 3)}

    if "icc_profile" in source.info:
        options["icc_profile"] = source.info["icc_profile"]
    dpi = source.info.get("dpi")
    if dpi:
        options["dpi"] = dpi
    return options


def encode_image(image, format_name, metadata_source=None):
    output = BytesIO()
    image.save(output, format=format_name, **_save_options(metadata_source or image, format_name))
    return output.getvalue()


def prepare_working_image(image, format_name):
    if format_name == "JPEG":
        return image.convert("RGB")
    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        return image.convert("RGBA")
    if image.mode == "L":
        return image.convert("L")
    return image.convert("RGB")


def normalize_upload_bytes(file_bytes, original_name):
    fmt = output_format(original_name)
    with Image.open(BytesIO(file_bytes)) as original:
        oriented = ImageOps.exif_transpose(original)
        width, height = oriented.size
        if not _needs_exif_transpose(original):
            return file_bytes, width, height
        return encode_image(oriented, fmt, oriented), width, height


def make_thumbnail_bytes(file_bytes):
    with Image.open(BytesIO(file_bytes)) as image:
        thumb = ImageOps.exif_transpose(image)
        thumb.thumbnail((THUMB_MAX, THUMB_MAX))
        if thumb.mode not in ("RGB", "L"):
            thumb = thumb.convert("RGB")
        output = BytesIO()
        thumb.save(output, format="JPEG", quality=82, optimize=True)
        return output.getvalue()


def save_thumbnail(image_upload, file_bytes):
    if image_upload.thumbnail:
        image_upload.thumbnail.delete(save=False)
    image_upload.thumbnail.save(
        f"thumb_{image_upload.id}.jpg",
        ContentFile(make_thumbnail_bytes(file_bytes)),
        save=False,
    )


def ensure_thumbnail(image_upload):
    if image_upload.thumbnail:
        return
    try:
        save_thumbnail(image_upload, read_field_bytes(image_upload.image))
        image_upload.save(update_fields=["thumbnail"])
    except Exception:
        return


def _line_mask_width(image_size):
    return max(8, min(image_size) // 40)


def order_points_clockwise(points):
    if len(points) < 4:
        return points
    center_x = sum(point[0] for point in points) / len(points)
    center_y = sum(point[1] for point in points) / len(points)
    return sorted(
        points,
        key=lambda point: math.atan2(point[1] - center_y, point[0] - center_x),
    )


def draw_shape_on_mask(draw, points, image_size):
    if len(points) >= 3:
        draw.polygon(order_points_clockwise(points), fill=255)
    elif len(points) == 2:
        draw.line(points, fill=255, width=_line_mask_width(image_size))


def union_padded_bbox(shapes, size, pad):
    width, height = size
    xs = [x for points in shapes for x, _ in points]
    ys = [y for points in shapes for _, y in points]
    if not xs:
        return None
    x0 = max(0, math.floor(min(xs)) - pad)
    y0 = max(0, math.floor(min(ys)) - pad)
    x1 = min(width, math.ceil(max(xs)) + pad)
    y1 = min(height, math.ceil(max(ys)) + pad)
    if x1 <= x0 or y1 <= y0:
        return None
    return int(x0), int(y0), int(x1), int(y1)


def blur_shapes(source_bytes, original_name, shapes):
    fmt = output_format(original_name)
    with Image.open(BytesIO(source_bytes)) as opened:
        original = ImageOps.exif_transpose(opened)
        working = prepare_working_image(original, fmt)
        box = union_padded_bbox(shapes, working.size, BLUR_PAD) or (0, 0, *working.size)
        crop = working.crop(box)
        blurred = crop.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
        mask = Image.new("L", crop.size, 0)
        draw = ImageDraw.Draw(mask)
        origin_x, origin_y = box[0], box[1]
        for points in shapes:
            shifted = [(x - origin_x, y - origin_y) for x, y in points]
            draw_shape_on_mask(draw, shifted, crop.size)
        working.paste(Image.composite(blurred, crop, mask), (origin_x, origin_y))
        return encode_image(working, fmt, original)
