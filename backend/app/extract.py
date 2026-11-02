"""Turn uploaded bytes into plain text for indexing.

Word, Excel and PowerPoint files are zip archives of XML, read here with the standard library:
no Office libraries are needed. Old binary formats (.doc, .xls, .ppt) and scanned pages
(images with no text) can't be read.
"""

import html
import io
import logging
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import PurePath
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

MAX_PDF_PAGES = 500
MAX_DOCX_XML_BYTES = 30 * 1024 * 1024  # one part, uncompressed; guards against zip bombs
MAX_OFFICE_XML_BYTES = 120 * 1024 * 1024  # all parts of one file together, uncompressed
MAX_SHEETS = 100
MAX_SLIDES = 1000
MAX_TEXT_CHARS = 2_000_000  # enough for ~400 pages of prose

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".yaml", ".yml", ".html", ".xml"}
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | OFFICE_EXTENSIONS | {".pdf"}
OLD_OFFICE = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}


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
    elif ext == ".xlsx":
        text = _xlsx(data)
    elif ext == ".pptx":
        text = _pptx(data)
    elif ext in OLD_OFFICE:
        raise UnsupportedFile(
            f"Old Office files ({ext}) can't be read. Save it as {OLD_OFFICE[ext]} in Office, then upload that."
        )
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
        text = "\n\n".join((page.extract_text() or "") for page in pages)
        if pages and not text.strip():
            raise UnsupportedFile(
                "This PDF has no text in it, only images: it's probably scanned. VaultMind can't read scanned "
                "pages yet. Upload a copy with text, such as one saved from Word or run through OCR."
            )
        return text
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
    lines = [html.unescape("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S))) for p in paragraphs]
    return "\n\n".join(line for line in lines if line.strip())


# ---------------------------------------------------------------- Excel and PowerPoint


def _local(tag: str) -> str:
    """A tag without its namespace, so both Office XML flavours (transitional and strict) read the same."""
    return tag.rsplit("}", 1)[-1]


def _rel_id(el: ET.Element) -> str:
    """The r:id attribute, whatever its namespace prefix."""
    return next((v for k, v in el.attrib.items() if k.endswith("}id")), "")


class _Package:
    """An Office file's parts, read with limits: each part and all parts together are capped
    (zip bombs), and XML with a document type declaration is refused (entity expansion attacks;
    Office never writes one)."""

    def __init__(self, data: bytes, kind: str) -> None:
        self.kind = kind
        self.read_bytes = 0
        try:
            self.zf = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, OSError) as exc:
            raise self.damaged() from exc

    def damaged(self) -> UnsupportedFile:
        return UnsupportedFile(f"This {self.kind} couldn't be read. It may be damaged or password-protected.")

    def has(self, name: str) -> bool:
        return name in self.zf.NameToInfo

    def raw(self, name: str) -> bytes:
        too_large = UnsupportedFile(f"This {self.kind} is too large to process.")
        try:
            info = self.zf.getinfo(name)
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise too_large
            with self.zf.open(info) as fh:
                raw = fh.read(MAX_DOCX_XML_BYTES + 1)
        except UnsupportedFile:
            raise
        except (zipfile.BadZipFile, KeyError, RuntimeError, OSError, EOFError) as exc:
            raise self.damaged() from exc
        self.read_bytes += len(raw)
        if len(raw) > MAX_DOCX_XML_BYTES or self.read_bytes > MAX_OFFICE_XML_BYTES:
            raise too_large
        if b"<!DOCTYPE" in raw:
            raise self.damaged()
        return raw

    def xml(self, name: str) -> ET.Element:
        try:
            return ET.fromstring(self.raw(name))
        except ET.ParseError as exc:
            raise self.damaged() from exc

    def iter_end(self, name: str):
        """Elements as they finish, for parts that can be large (sheets, shared strings)."""
        try:
            for _, el in ET.iterparse(io.BytesIO(self.raw(name)), events=("end",)):
                yield el
        except ET.ParseError as exc:
            raise self.damaged() from exc

    def rels(self, part: str) -> dict[str, tuple[str, str]]:
        """Relationship id -> (type, path inside the zip) for one part."""
        folder, _, file = part.rpartition("/")
        rels_name = f"{folder}/_rels/{file}.rels" if folder else f"_rels/{file}.rels"
        if not self.has(rels_name):
            return {}
        out = {}
        for rel in self.xml(rels_name):
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            path = target.lstrip("/") if target.startswith("/") else _join(folder, target)
            out[rel.get("Id", "")] = (rel.get("Type", ""), path)
        return out


def _join(folder: str, target: str) -> str:
    parts = [p for p in folder.split("/") if p]
    for piece in target.split("/"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


def _text_of(el: ET.Element, skip: frozenset[str] = frozenset()) -> str:
    """All text in <t> elements under `el`, leaving out subtrees whose tag is in `skip`."""
    out: list[str] = []

    def walk(node: ET.Element) -> None:
        for child in node:
            tag = _local(child.tag)
            if tag in skip:
                continue
            if tag == "t":
                out.append(child.text or "")
            elif tag == "tab":
                out.append(" ")
            elif tag == "br":
                out.append("\n")
            walk(child)

    walk(el)
    return "".join(out)


# Excel ----------------------------------------------------------------

# Built-in number formats that show a date or a time (ECMA-376 part 1, 18.8.30).
_DATE_FORMATS = {
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    27,
    28,
    29,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    45,
    46,
    47,
    50,
    51,
    52,
    53,
    54,
    55,
    56,
    57,
    58,
}
_PERCENT_FORMATS = {9, 10}
_COLUMN = re.compile(r"[A-Z]+")


def _is_date_code(code: str) -> bool:
    plain = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", code).lower()
    return bool(re.search(r"[dmyhs]", plain)) and "general" not in plain


def _column(ref: str) -> int:
    m = _COLUMN.match(ref or "")
    n = 0
    for ch in m.group(0) if m else "":
        n = n * 26 + ord(ch) - 64
    return n


def _column_name(n: int) -> str:
    name = ""
    while n:
        n, rem = divmod(n - 1, 26)
        name = chr(65 + rem) + name
    return name


def _number(value: str, style: str, epoch: datetime) -> str:
    try:
        x = float(value)
    except ValueError:
        return value
    if style == "date" and 0 <= x < 2_958_466:  # Excel's last date is 9999-12-31
        moment = epoch + timedelta(days=x)
        if x < 1:
            return moment.strftime("%H:%M")
        if abs(x - round(x)) < 1e-9:
            return moment.strftime("%Y-%m-%d")
        return moment.strftime("%Y-%m-%d %H:%M")
    if style == "percent":
        return f"{x * 100:.10g}%"
    if x.is_integer() and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.10g}"


def _cell_styles(pkg: _Package, path: str) -> list[str]:
    """For each cell style index: "date", "percent" or ""."""
    if not path or not pkg.has(path):
        return []
    root = pkg.xml(path)
    custom: dict[int, str] = {}
    out: list[str] = []
    for el in root:
        tag = _local(el.tag)
        if tag == "numFmts":
            for fmt in el:
                custom[int(fmt.get("numFmtId", "0"))] = fmt.get("formatCode", "")
        elif tag == "cellXfs":
            for xf in el:
                fid = int(xf.get("numFmtId", "0"))
                code = custom.get(fid)
                if fid in _DATE_FORMATS or (code is not None and _is_date_code(code)):
                    out.append("date")
                elif fid in _PERCENT_FORMATS or (code is not None and code.rstrip().endswith("%")):
                    out.append("percent")
                else:
                    out.append("")
    return out


def _shared_strings(pkg: _Package, path: str) -> list[str]:
    if not path or not pkg.has(path):
        return []
    out: list[str] = []
    for el in pkg.iter_end(path):
        if _local(el.tag) == "si":
            out.append(_text_of(el, skip=frozenset({"rPh"})))  # leave out phonetic guides
            el.clear()
    return out


def _sheet_rows(pkg: _Package, path: str, strings: list[str], styles: list[str], epoch: datetime):
    """Each non-empty row as {column number: text}."""
    for el in pkg.iter_end(path):
        if _local(el.tag) != "row":
            continue
        row: dict[int, str] = {}
        for i, c in enumerate(child for child in el if _local(child.tag) == "c"):
            kind = c.get("t", "n")
            value = ""
            v = next((x for x in c if _local(x.tag) == "v"), None)
            if kind == "inlineStr":
                inline = next((x for x in c if _local(x.tag) == "is"), None)
                value = _text_of(inline, skip=frozenset({"rPh"})) if inline is not None else ""
            elif v is None or v.text is None:
                continue
            elif kind == "s":
                idx = int(v.text) if v.text.strip().isdigit() else -1
                value = strings[idx] if 0 <= idx < len(strings) else ""
            elif kind == "b":
                value = "TRUE" if v.text.strip() == "1" else "FALSE"
            elif kind in ("str", "e"):
                value = v.text
            else:
                s_idx = int(c.get("s", "0") or 0)
                value = _number(v.text, styles[s_idx] if s_idx < len(styles) else "", epoch)
            value = " ".join(value.split())
            if value:
                row[_column(c.get("r", "")) or i + 1] = value
        el.clear()
        if row:
            yield row


def _is_header(row: dict[int, str]) -> bool:
    return len(row) >= 2 and all(not re.fullmatch(r"[-+]?[\d.,%]+", v) for v in row.values())


def _xlsx(data: bytes) -> str:
    """Each sheet under a heading; each row as one line. When a sheet starts with a row of
    labels, every row is written with them ("City tier: Metro; Hotel per night: 7000."), so a
    row found by search still says what its numbers are."""
    pkg = _Package(data, ".xlsx")
    workbook_path = "xl/workbook.xml"
    for _rid, (rel_type, path) in pkg.rels("").items():
        if rel_type.endswith("/officeDocument"):
            workbook_path = path
    if not pkg.has(workbook_path):
        raise pkg.damaged()
    workbook = pkg.xml(workbook_path)
    rels = pkg.rels(workbook_path)
    by_type = {t.rsplit("/", 1)[-1]: p for t, p in rels.values()}
    props = next((el for el in workbook if _local(el.tag) == "workbookPr"), None)
    date1904 = props is not None and props.get("date1904", "").lower() in ("1", "true")
    epoch = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    strings = _shared_strings(pkg, by_type.get("sharedStrings", ""))
    styles = _cell_styles(pkg, by_type.get("styles", ""))
    sheets = [el for el in workbook.iter() if _local(el.tag) == "sheet"]
    if len(sheets) > MAX_SHEETS:
        raise UnsupportedFile(f"This .xlsx has more than {MAX_SHEETS} sheets, too many to process.")
    out: list[str] = []
    size = 0
    for sheet in sheets:
        rel = rels.get(_rel_id(sheet))
        if not rel or not rel[0].endswith("/worksheet") or not pkg.has(rel[1]):
            continue  # chart sheets and the like hold no cell text
        name = sheet.get("name") or "Sheet"
        hidden = " (hidden sheet)" if sheet.get("state") in ("hidden", "veryHidden") else ""
        lines: list[str] = []
        header: dict[int, str] | None = None
        for n, row in enumerate(_sheet_rows(pkg, rel[1], strings, styles, epoch)):
            # The labels row is the first all-text row of two or more cells near the top; a
            # title above it stays a plain line.
            if header is None and n < 5 and _is_header(row):
                header = row
                lines.append("Columns: " + "; ".join(row[k] for k in sorted(row)) + ".")
                continue
            if header:
                cells = [f"{header.get(k, _column_name(k))}: {v}" for k, v in sorted(row.items())]
                lines.append("; ".join(cells).rstrip(".") + ".")
            else:
                lines.append(" | ".join(row[k] for k in sorted(row)))
            size += len(lines[-1])
            if size > MAX_TEXT_CHARS:
                break
        if lines:
            out.append(f"# {name}{hidden}\n\n" + "\n\n".join(lines))
        if size > MAX_TEXT_CHARS:
            break
    return "\n\n".join(out)


# PowerPoint ----------------------------------------------------------------


def _placeholder(shape: ET.Element) -> str:
    """The placeholder type of a shape ("title", "body", ...), or "" for an ordinary shape."""
    for el in shape.iter():
        if _local(el.tag) == "ph":
            return el.get("type", "body")
    return ""


def _paragraphs(el: ET.Element) -> list[str]:
    """Lines of text in a shape; a table's rows come out one line each, cells joined by " | "."""
    table = next((t for t in el.iter() if _local(t.tag) == "tbl"), None)
    if table is not None:
        rows = []
        for tr in (r for r in table if _local(r.tag) == "tr"):
            cells = [" ".join(" ".join(_paragraphs(tc)).split()) for tc in tr if _local(tc.tag) == "tc"]
            if any(cells):
                rows.append(" | ".join(cells))
        return rows
    out = []
    for p in el.iter():
        if _local(p.tag) == "p" and p is not el:
            text = " ".join(_text_of(p).split())
            if text:
                out.append(text)
    return out


def _pptx(data: bytes) -> str:
    """Each slide under a heading with its title, then its text as a list, then the speaker
    notes, which often hold what the presenter actually says."""
    pkg = _Package(data, ".pptx")
    pres_path = "ppt/presentation.xml"
    for _rid, (rel_type, path) in pkg.rels("").items():
        if rel_type.endswith("/officeDocument"):
            pres_path = path
    if not pkg.has(pres_path):
        raise pkg.damaged()
    pres = pkg.xml(pres_path)
    rels = pkg.rels(pres_path)
    slide_ids = [el for el in pres.iter() if _local(el.tag) == "sldId"]
    if len(slide_ids) > MAX_SLIDES:
        raise UnsupportedFile(f"This .pptx has more than {MAX_SLIDES} slides, too many to process.")
    out: list[str] = []
    for n, sld in enumerate(slide_ids, start=1):
        rel = rels.get(_rel_id(sld))
        if not rel or not pkg.has(rel[1]):
            continue
        hidden = sld.get("show") == "0"
        slide = pkg.xml(rel[1])
        if slide.get("show") == "0":
            hidden = True
        title = ""
        body: list[str] = []
        for shape in slide.iter():
            if _local(shape.tag) not in ("sp", "graphicFrame"):
                continue
            kind = _placeholder(shape)
            if kind in ("title", "ctrTitle") and not title:
                title = " ".join(_paragraphs(shape))
            elif kind not in ("sldNum", "dt", "ftr"):
                body += _paragraphs(shape)
        notes: list[str] = []
        for rel_type, path in pkg.rels(rel[1]).values():
            if rel_type.endswith("/notesSlide") and pkg.has(path):
                for shape in pkg.xml(path).iter():
                    if _local(shape.tag) == "sp" and _placeholder(shape) == "body":
                        notes += _paragraphs(shape)
        heading = f"# Slide {n}" + (f": {title}" if title else "") + (" (hidden slide)" if hidden else "")
        parts = [heading]
        if body:
            parts.append("\n".join(f"- {line}" for line in body))
        if notes:
            parts.append("Speaker notes: " + " ".join(notes))
        if len(parts) > 1 or title:
            out.append("\n\n".join(parts))
    return "\n\n".join(out)
