"""Tests for file ingestion: type detection, image normalisation, PDF parsing."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from pypdf import PdfWriter

from app.services.documents import DocumentError, extract_pdf, extract_text
from app.services.imaging import (
    UnsupportedFileError,
    detect_kind,
    dominant_palette,
    process_image,
    sniff_image_mime,
)


def _png_bytes(colour: tuple[int, int, int] = (200, 40, 90), size=(120, 90)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 120, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


# 1 ---------------------------------------------------------------------------
def test_detect_kind_uses_magic_bytes_not_extension():
    assert detect_kind(_png_bytes()) == "image"
    assert detect_kind(_jpeg_bytes()) == "image"
    assert detect_kind(b"%PDF-1.7\n...") == "pdf"
    assert detect_kind("héllo wörld".encode()) == "text"


# 2 ---------------------------------------------------------------------------
def test_detect_kind_rejects_empty_and_binary():
    with pytest.raises(UnsupportedFileError):
        detect_kind(b"")
    with pytest.raises(UnsupportedFileError):
        detect_kind(b"\x00\x01\x02\x03\x00\xff\xfe")


# 3 ---------------------------------------------------------------------------
def test_sniff_image_mime_distinguishes_png_and_jpeg():
    assert sniff_image_mime(_png_bytes()) == "image/png"
    assert sniff_image_mime(_jpeg_bytes()) == "image/jpeg"


# 4 ---------------------------------------------------------------------------
def test_process_image_records_real_dimensions_and_reencodes_to_png():
    result = process_image(_jpeg_bytes())
    assert result.width == 64 and result.height == 64
    assert result.mime == "image/png"
    assert result.payload.startswith(b"\x89PNG"), "stored bytes must be the re-encoded PNG"
    assert result.meta["original_mime"] == "image/jpeg"
    assert result.thumb, "a thumbnail must be produced"


# 5 ---------------------------------------------------------------------------
def test_process_image_strips_exif_metadata():
    """A re-encode must drop EXIF — this is what removes GPS from phone photos."""
    source = Image.new("RGB", (80, 80), (30, 30, 30))
    exif = source.getexif()
    exif[0x0112] = 6  # Orientation
    exif[0x010F] = "TestCamera"
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", exif=exif)
    original = buffer.getvalue()
    assert b"TestCamera" in original, "precondition: the fixture really has EXIF"

    result = process_image(original)
    assert b"TestCamera" not in result.payload
    with Image.open(io.BytesIO(result.payload)) as stored:
        assert len(stored.getexif()) == 0


# 6 ---------------------------------------------------------------------------
def test_process_image_downscales_oversized_images():
    big = _png_bytes(size=(3000, 2000))
    result = process_image(big, max_edge=800)
    assert max(result.meta["stored_width"], result.meta["stored_height"]) <= 800
    # The original dimensions are preserved in metadata for the UI to display.
    assert result.meta["original_width"] == 3000


# 7 ---------------------------------------------------------------------------
def test_dominant_palette_returns_hex_colours():
    palette = dominant_palette(Image.new("RGB", (60, 60), (200, 40, 90)))
    assert palette
    assert all(hex_code.startswith("#") and len(hex_code) == 7 for hex_code in palette)
    assert palette[0].lower() == "#c8285a"


# 8 ---------------------------------------------------------------------------
def test_process_image_rejects_tiny_images():
    with pytest.raises(UnsupportedFileError):
        process_image(_png_bytes(size=(4, 4)))


# 9 ---------------------------------------------------------------------------
def test_extract_text_handles_bom_and_normalises_newlines():
    result = extract_text("\ufeffline one\r\nline two".encode("utf-8"))
    assert result.text == "line one\nline two"
    assert result.page_count == 1
    assert result.meta["lines"] == 2


# 10 --------------------------------------------------------------------------
def test_extract_text_rejects_empty_and_invalid_utf8():
    with pytest.raises(DocumentError):
        extract_text(b"   \n  ")
    with pytest.raises(DocumentError):
        extract_text(b"\xff\xfe\x00\x00invalid")


# 11 --------------------------------------------------------------------------
def test_extract_pdf_rejects_non_pdf_bytes():
    with pytest.raises(DocumentError):
        extract_pdf(b"this is definitely not a pdf file")


# 12 --------------------------------------------------------------------------
def test_extract_pdf_flags_scanned_documents_with_no_text_layer():
    """A blank PDF looks like a document but carries no words.

    Retrieval would silently return nothing for it, so the app must mark it.
    """
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    result = extract_pdf(buffer.getvalue())
    assert result.page_count == 2
    assert result.meta["has_text_layer"] is False
    assert result.meta["likely_scanned"] is True
