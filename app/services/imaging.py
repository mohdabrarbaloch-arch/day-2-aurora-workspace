"""Image ingestion: verification, re-encoding, OCR and palette extraction.

Security posture: the uploaded bytes are never trusted. The declared MIME type
and the filename extension are treated as hints; the magic bytes decide.
Everything is re-encoded through Pillow, which drops EXIF (including GPS
coordinates) and neutralises polyglot files that are valid images *and* valid
scripts.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageStat, UnidentifiedImageError

try:  # OCR is optional: the app works without the tesseract binary.
    import pytesseract
except ImportError:  # pragma: no cover - exercised only when the lib is absent
    pytesseract = None  # type: ignore[assignment]

MAX_EDGE = 1600
THUMB_EDGE = 320

# Magic-byte signatures. Extension is irrelevant here.
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)
_PDF_SIGNATURE = b"%PDF-"
_WEBP_SIGNATURE = b"RIFF"


class UnsupportedFileError(ValueError):
    """Raised when the bytes are not a type this app can ingest."""


@dataclass(slots=True)
class ImageResult:
    kind: str = "image"
    mime: str = "image/png"
    width: int = 0
    height: int = 0
    ocr_text: str = ""
    palette: list[str] = field(default_factory=list)
    payload: bytes = b""
    thumb: bytes = b""
    meta: dict[str, Any] = field(default_factory=dict)


def detect_kind(data: bytes) -> str:
    """Classify raw bytes as ``image``, ``pdf`` or ``text``.

    Raises UnsupportedFileError rather than guessing — a caller that silently
    accepts garbage ends up storing it.
    """
    if not data:
        raise UnsupportedFileError("The file is empty.")

    for signature, _mime in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return "image"
    if data[:4] == _WEBP_SIGNATURE and data[8:12] == b"WEBP":
        return "image"
    if data.startswith(_PDF_SIGNATURE):
        return "pdf"

    # Text must decode cleanly as UTF-8 and contain no NUL bytes; binary that
    # happens to be valid UTF-8 is rare enough to accept the small risk.
    sample = data[:4096]
    if b"\x00" not in sample:
        try:
            sample.decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            pass

    raise UnsupportedFileError(
        "Unsupported file type. Upload a JPEG/PNG/WebP/GIF image, a PDF, or a UTF-8 text file."
    )


def sniff_image_mime(data: bytes) -> str:
    for signature, mime in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == _WEBP_SIGNATURE and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    """Convert anything to RGB, compositing transparency onto white.

    Converting a transparent PNG straight to RGB turns transparent pixels
    black, which wrecks OCR contrast — so alpha is flattened deliberately.
    """
    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def dominant_palette(image: Image.Image, colours: int = 5) -> list[str]:
    """Return the most prominent colours as hex strings."""
    small = image.copy()
    small.thumbnail((120, 120))
    quantised = _flatten_to_rgb(small).quantize(colors=colours, method=Image.Quantize.MEDIANCUT)
    palette = quantised.getpalette() or []
    counts = sorted(quantised.getcolors() or [], reverse=True)
    results: list[str] = []
    for _count, index in counts[:colours]:
        base = index * 3
        if base + 2 >= len(palette):
            continue
        r, g, b = palette[base], palette[base + 1], palette[base + 2]
        results.append(f"#{r:02x}{g:02x}{b:02x}")
    return results


def brightness(image: Image.Image) -> float:
    """Mean luminance 0-255 — used to warn about low-contrast scans."""
    stat = ImageStat.Stat(_flatten_to_rgb(image).convert("L"))
    return round(stat.mean[0], 2)


def run_ocr(image: Image.Image) -> str:
    """Extract text. Returns ``""`` when OCR is unavailable or finds nothing."""
    if pytesseract is None:
        return ""
    try:
        text = pytesseract.image_to_string(_flatten_to_rgb(image))
    except Exception:
        return ""
    return " ".join(text.split())


def process_image(data: bytes, *, max_edge: int = MAX_EDGE) -> ImageResult:
    """Validate, normalise and describe an uploaded image."""
    mime = sniff_image_mime(data)
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            width, height = opened.size
            working = _flatten_to_rgb(opened)
    except (UnidentifiedImageError, OSError) as exc:
        raise UnsupportedFileError("That file is not a readable image.") from exc

    if width < 8 or height < 8:
        raise UnsupportedFileError("Image is too small to be useful (min 8x8 px).")

    # OCR the ORIGINAL resolution: downscaling first loses small text.
    ocr_text = run_ocr(working)
    palette = dominant_palette(working)
    mean_luma = brightness(working)

    stored = working.copy()
    if max(stored.size) > max_edge:
        stored.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

    thumb = stored.copy()
    thumb.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)

    # Re-encode with no metadata: this is what strips EXIF/GPS.
    out = io.BytesIO()
    stored.save(out, format="PNG", optimize=True)
    thumb_out = io.BytesIO()
    thumb.save(thumb_out, format="PNG", optimize=True)

    return ImageResult(
        mime="image/png",
        width=width,
        height=height,
        ocr_text=ocr_text,
        palette=palette,
        payload=out.getvalue(),
        thumb=thumb_out.getvalue(),
        meta={
            "original_mime": mime,
            "original_width": width,
            "original_height": height,
            "stored_width": stored.size[0],
            "stored_height": stored.size[1],
            "palette": palette,
            "mean_luminance": mean_luma,
            "ocr_chars": len(ocr_text),
            "low_contrast": mean_luma < 40 or mean_luma > 225,
        },
    )
