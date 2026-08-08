from PIL import Image, ImageDraw, ImageFilter


def blur_polygon(image, coordinates, blur_radius=15):
    """
    Blur the area inside a polygon.
    coordinates = [(x1, y1), (x2, y2), ...]
    """

    # Create a mask for the polygon
    mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(mask)

    # Convert coordinates to integers
    points = [(int(x), int(y)) for x, y in coordinates]

    # White = area to blur
    mask_draw.polygon(points, fill=255)

    # Blur the entire image
    blurred = image.filter(
        ImageFilter.GaussianBlur(blur_radius)
    )

    # Put blurred pixels only inside the polygon
    image.paste(blurred, mask=mask)


def blur_image(input_path, output_path, regions):
    image = Image.open(input_path).convert("RGB")

    for coordinates in regions:
        blur_polygon(
            image,
            coordinates,
            blur_radius=15
        )

    image.save(output_path)


# =========================
# NUMBER PLATE
# =========================
number_plate = [
    (235, 195.50),
    (209, 263.50),
    (272, 252.50),
]


# =========================
# SIGNBOARD
# =========================
signboard = [
    (9, 4.50),
    (268, 10.50),
    (202, 101.50),
]


# Blur both areas
regions = [
    number_plate,
    signboard,
]


blur_image(
    "Screenshot_1.png",
    "output.png",
    regions
)

print("Done! Saved as output.png")