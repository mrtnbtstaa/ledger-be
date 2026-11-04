from io import BytesIO

from PIL import Image, ImageOps

from app.core.errors import ApiError

Image.MAX_IMAGE_PIXELS = 40_000_000


def normalize_image(content: bytes, extension: str) -> bytes:
    try:
        with Image.open(BytesIO(content)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((2048, 2048))
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            output = BytesIO()
            if extension.lower() == "png":
                image.save(output, format="PNG", optimize=True)
            else:
                image.convert("RGB").save(output, format="JPEG", quality=90)
            return output.getvalue()
    except Exception as exc:
        raise ApiError(400, "empty_file", "The image could not be read.") from exc
