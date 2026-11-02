"""Helpers for handling user-supplied file names and content types safely."""

import re
import unicodedata
from pathlib import PurePath
from urllib.parse import quote

# Downloads are served with a type chosen from the extension, never the uploader's claim,
# so an uploaded HTML or SVG file can't be served back as something a browser would run.
_MEDIA_TYPES = {
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def safe_filename(name: str | None, default: str = "upload.txt") -> str:
    """Strip directories, control characters and quotes; keep it short and non-empty."""
    base = PurePath((name or "").replace("\\", "/")).name
    base = unicodedata.normalize("NFC", base)
    base = re.sub(r"[\x00-\x1f\x7f\"<>|*?:]", "", base).strip(" .")
    if not base:
        return default
    stem, dot, ext = base.rpartition(".")
    if dot and len(ext) <= 10:
        return stem[: 200 - len(ext) - 1] + "." + ext
    return base[:200]


def media_type_for(filename: str) -> str:
    return _MEDIA_TYPES.get(PurePath(filename).suffix.lower(), "application/octet-stream")


def content_disposition(filename: str) -> str:
    """RFC 6266 attachment header with an ASCII fallback and a UTF-8 filename*."""
    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode() or "download"
    ascii_name = re.sub(r"[^A-Za-z0-9._ ()-]", "_", ascii_name)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"
