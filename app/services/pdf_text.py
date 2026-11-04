from collections.abc import Iterator

import pymupdf


def page_count(content: bytes) -> int:
    with pymupdf.open(stream=content, filetype="pdf") as document:  # type: ignore[no-untyped-call]
        return document.page_count  # type: ignore[no-any-return]


def extract_text(content: bytes) -> str:
    with pymupdf.open(stream=content, filetype="pdf") as document:  # type: ignore[no-untyped-call]
        return "\n".join(page.get_text("text") for page in document)


def render_pages(content: bytes, max_pages: int) -> Iterator[bytes]:
    with pymupdf.open(stream=content, filetype="pdf") as document:  # type: ignore[no-untyped-call]
        for index, page in enumerate(document):
            if index >= max_pages:
                break
            pixmap = page.get_pixmap(dpi=150)
            yield pixmap.tobytes("png")
