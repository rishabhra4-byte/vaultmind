"""Turn uploaded bytes into plain text for indexing."""

import io
import re
import zipfile
from pathlib import PurePath

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
        return text
    if ext == ".pdf":
        return _pdf(data)
    if ext == ".docx":
        return _docx(data)
    raise UnsupportedFile(f"Unsupported file type '{ext or filename}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # pypdf raises many exception types for malformed files
        raise UnsupportedFile(f"Could not read PDF: {exc}") from exc


def _docx(data: bytes) -> str:
    """Read paragraphs straight from word/document.xml; no python-docx dependency needed."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise UnsupportedFile("Could not read .docx file") from exc
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
    lines = ["".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S)) for p in paragraphs]
    return "\n\n".join(line for line in lines if line.strip())
