"""Document ingestion: PDF and plain-text extraction.

PDF text is collected page by page so citations can name a page number. Scanned
PDFs with no text layer are detected and reported honestly rather than being
treated as empty documents.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from pypdf import PdfReader

MAX_PDF_PAGES = 300


class DocumentError(ValueError):
    """Raised when a document cannot be parsed."""


@dataclass(slots=True)
class PageText:
    page: int
    text: str


@dataclass(slots=True)
class DocumentResult:
    kind: str = "pdf"
    text: str = ""
    pages: list[PageText] = field(default_factory=list)
    page_count: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


def extract_pdf(data: bytes) -> DocumentResult:
    """Extract per-page text from a PDF.

    Encrypted PDFs are attempted with an empty password (the common
    "owner-password only" case) and rejected with a clear message otherwise.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise DocumentError("That PDF could not be opened — it may be corrupt.") from exc

    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception as exc:
            raise DocumentError("That PDF is password protected.") from exc
        if not unlocked:
            raise DocumentError("That PDF is password protected.")

    total = len(reader.pages)
    if total == 0:
        raise DocumentError("That PDF has no pages.")
    if total > MAX_PDF_PAGES:
        raise DocumentError(
            f"That PDF has {total} pages; the limit is {MAX_PDF_PAGES}. "
            "Split it and upload the relevant section."
        )

    pages: list[PageText] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception:
            raw = ""
        cleaned = " ".join(raw.split())
        if cleaned:
            pages.append(PageText(page=number, text=cleaned))

    full_text = "\n\n".join(f"[page {p.page}] {p.text}" for p in pages)
    has_text_layer = bool(pages)

    return DocumentResult(
        kind="pdf",
        text=full_text,
        pages=pages,
        page_count=total,
        meta={
            "total_pages": total,
            "pages_with_text": len(pages),
            "has_text_layer": has_text_layer,
            # A scanned PDF looks like a document but contains no words, so
            # retrieval would silently return nothing. Say so in the UI.
            "likely_scanned": total > 0 and not has_text_layer,
            "char_count": len(full_text),
        },
    )


def extract_text(data: bytes) -> DocumentResult:
    """Decode a plain-text upload, tolerating a BOM."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentError("That text file is not valid UTF-8.") from exc

    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        raise DocumentError("That text file is empty.")

    return DocumentResult(
        kind="text",
        text=cleaned,
        pages=[PageText(page=1, text=cleaned)],
        page_count=1,
        meta={"char_count": len(cleaned), "lines": cleaned.count("\n") + 1},
    )
