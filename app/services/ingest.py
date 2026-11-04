import hashlib
from dataclasses import dataclass

import filetype  # type: ignore[import-untyped]

from app.config import get_settings
from app.core.errors import ApiError
from app.services import image_prep, pdf_text

ALLOWED = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "application/pdf": "pdf",
}


@dataclass
class PreparedUpload:
    content: bytes
    content_type: str
    file_type: str
    sha256: str


def prepare(content: bytes) -> PreparedUpload:
    settings = get_settings()
    if not content:
        raise ApiError(400, "empty_file", "The file is empty.")
    if len(content) > settings.max_file_bytes:
        raise ApiError(
            413, "file_too_large", f"The file is larger than {settings.max_file_bytes // 1024 // 1024} MB."
        )

    kind = filetype.guess(content)
    if kind is None or kind.mime not in ALLOWED:
        raise ApiError(415, "invalid_file_type", "Use a JPG, PNG or PDF file.")

    file_type = ALLOWED[kind.mime]
    if file_type == "pdf":
        try:
            pages = pdf_text.page_count(content)
        except Exception as exc:
            raise ApiError(400, "empty_file", "The PDF could not be read.") from exc
        if pages > settings.max_pdf_pages:
            raise ApiError(422, "too_many_pages", f"PDFs may have at most {settings.max_pdf_pages} pages.")
        stored = content
    else:
        stored = image_prep.normalize_image(content, file_type)

    return PreparedUpload(
        content=stored,
        content_type=kind.mime,
        file_type=file_type,
        sha256=hashlib.sha256(stored).hexdigest(),
    )
