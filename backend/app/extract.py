"""Turn uploaded bytes into plain text for indexing."""

import io
import logging
import re
import zipfile
from pathlib import PurePath

log = logging.getLogger(__name__)

MAX_PDF_PAGES = 500
MAX_DOCX_XML_BYTES = 30 * 1024 * 1024  # uncompressed; guards against zip bombs
MAX_TEXT_CHARS = 2_000_000  # enough for ~400 pages of prose

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".yaml", ".yml", ".html", ".xml"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx"}


class UnsupportedFile(ValueError):
    pass


def extract_text(filename: str, data: bytes) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext in TEXT_EXTENSIONS:
        text = data.decode("utf-8", errors="replace")
        if ext in {".html", ".xml"}:
            text = re.sub(r"<[^>]+>", " ", text)
    elif ext == ".pdf":
        text = _pdf(data)
    elif ext == ".docx":
        text = _docx(data)
    else:
        raise UnsupportedFile(f"Unsupported file type '{ext or filename}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    return text[:MAX_TEXT_CHARS]


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UnsupportedFile("This PDF is password-protected. Upload an unlocked copy.")
        pages = reader.pages[:MAX_PDF_PAGES]
        return "\n\n".join((page.extract_text() or "") for page in pages)
    except UnsupportedFile:
        raise
    except Exception as exc:  # pypdf raises many exception types for malformed files
        log.warning("PDF extraction failed: %s", exc)
        raise UnsupportedFile("This PDF couldn't be read. It may be damaged.") from exc


def _docx(data: bytes) -> str:
    """Read paragraphs straight from word/document.xml; no python-docx dependency needed."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            info = zf.getinfo("word/document.xml")
            # Check the declared size, then cap what is actually inflated, since headers can lie.
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise UnsupportedFile("This .docx is too large to process.")
            with zf.open(info) as fh:
                raw = fh.read(MAX_DOCX_XML_BYTES + 1)
            if len(raw) > MAX_DOCX_XML_BYTES:
                raise UnsupportedFile("This .docx is too large to process.")
            xml = raw.decode("utf-8", errors="replace")
    except UnsupportedFile:
        raise
    except (zipfile.BadZipFile, KeyError, RuntimeError, OSError) as exc:
        raise UnsupportedFile("This .docx couldn't be read. It may be damaged.") from exc
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
    lines = ["".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S)) for p in paragraphs]
    return "\n\n".join(line for line in lines if line.strip())
