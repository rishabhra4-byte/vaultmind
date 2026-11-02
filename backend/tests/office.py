"""Small Excel and PowerPoint files built by hand for tests: just the parts VaultMind reads, so
each test controls exactly what is inside (shared or inline strings, dates, hidden sheets,
speaker notes). Real files saved by Office tools are in tests/fixtures/."""

import io
import zipfile
from xml.sax.saxutils import escape

R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
DOC = f"{R}/officeDocument"


def _zip(parts: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _rels(items: list[tuple[str, str, str]]) -> str:
    rows = "".join(f'<Relationship Id="{i}" Type="{t}" Target="{escape(target)}"/>' for i, t, target in items)
    return f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PKG}">{rows}</Relationships>'


class Date(float):
    """An Excel date serial, shown with a date format."""


class Percent(float):
    """A fraction shown as a percentage."""


def xlsx(sheets: dict[str, list[list]], *, inline: bool = False, date1904: bool = False, hidden: tuple[str, ...] = ()) -> bytes:
    """Rows of values: str, int/float, bool, Date, Percent or None (an empty cell)."""
    strings: list[str] = []
    parts: dict[str, str | bytes] = {}
    workbook_sheets = []
    rels = []
    for n, (name, rows) in enumerate(sheets.items(), start=1):
        xml_rows = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, value in enumerate(row):
                ref = f"{chr(65 + c)}{r}"
                if value is None:
                    continue
                if isinstance(value, bool):
                    cells.append(f'<c r="{ref}" t="b"><v>{int(value)}</v></c>')
                elif isinstance(value, Date):
                    cells.append(f'<c r="{ref}" s="1"><v>{float(value)}</v></c>')
                elif isinstance(value, Percent):
                    cells.append(f'<c r="{ref}" s="2"><v>{float(value)}</v></c>')
                elif isinstance(value, int | float):
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                elif inline:
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
                else:
                    strings.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
        parts[f"xl/worksheets/sheet{n}.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{SS}">'
            f"<sheetData>{''.join(xml_rows)}</sheetData></worksheet>"
        )
        state = ' state="hidden"' if name in hidden else ""
        workbook_sheets.append(f'<sheet name="{escape(name)}" sheetId="{n}"{state} r:id="rId{n}"/>')
        rels.append((f"rId{n}", f"{R}/worksheet", f"worksheets/sheet{n}.xml"))
    rels.append(("rIdS", f"{R}/sharedStrings", "sharedStrings.xml"))
    rels.append(("rIdT", f"{R}/styles", "styles.xml"))
    si = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
    parts["xl/sharedStrings.xml"] = f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="{SS}">{si}</sst>'
    parts["xl/styles.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="{SS}"><cellXfs>'
        '<xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="9"/></cellXfs></styleSheet>'
    )
    pr = '<workbookPr date1904="1"/>' if date1904 else ""
    parts["xl/workbook.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{SS}" xmlns:r="{R}">{pr}'
        f"<sheets>{''.join(workbook_sheets)}</sheets></workbook>"
    )
    parts["xl/_rels/workbook.xml.rels"] = _rels(rels)
    parts["_rels/.rels"] = _rels([("rId1", DOC, "xl/workbook.xml")])
    parts["[Content_Types].xml"] = (
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
    )
    return _zip(parts)


def _shape(text_lines: list[str], ph: str | None) -> str:
    nv = f'<p:nvPr><p:ph type="{ph}"/></p:nvPr>' if ph else "<p:nvPr/>"
    paras = "".join(f"<a:p><a:r><a:t>{escape(t)}</a:t></a:r></a:p>" for t in text_lines)
    return f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="s"/><p:cNvSpPr/>{nv}</p:nvSpPr><p:txBody>{paras}</p:txBody></p:sp>'


def _table(rows: list[list[str]]) -> str:
    trs = "".join(
        "<a:tr>"
        + "".join(f"<a:tc><a:txBody><a:p><a:r><a:t>{escape(c)}</a:t></a:r></a:p></a:txBody></a:tc>" for c in row)
        + "</a:tr>"
        for row in rows
    )
    return f"<p:graphicFrame><a:graphic><a:graphicData><a:tbl>{trs}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"


def pptx(slides: list[dict]) -> bytes:
    """Slides as dicts: title, bullets (list), table (rows), notes (str), hidden (bool), number (str)."""
    parts: dict[str, str | bytes] = {}
    ids, rels = [], []
    for n, s in enumerate(slides, start=1):
        shapes = []
        if s.get("title"):
            shapes.append(_shape([s["title"]], "title"))
        if s.get("bullets"):
            shapes.append(_shape(s["bullets"], "body"))
        if s.get("table"):
            shapes.append(_table(s["table"]))
        shapes.append(_shape([str(n)], "sldNum"))
        show = ' show="0"' if s.get("hidden") else ""
        parts[f"ppt/slides/slide{n}.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"{show}>'
            f"<p:cSld><p:spTree>{''.join(shapes)}</p:spTree></p:cSld></p:sld>"
        )
        if s.get("notes"):
            parts[f"ppt/notesSlides/notesSlide{n}.xml"] = (
                f'<?xml version="1.0" encoding="UTF-8"?><p:notes xmlns:p="{P}" xmlns:a="{A}">'
                f"<p:cSld><p:spTree>{_shape([str(n)], 'sldNum')}{_shape([s['notes']], 'body')}</p:spTree></p:cSld></p:notes>"
            )
            parts[f"ppt/slides/_rels/slide{n}.xml.rels"] = _rels(
                [("rId1", f"{R}/notesSlide", f"../notesSlides/notesSlide{n}.xml")]
            )
        ids.append(f'<p:sldId id="{255 + n}" r:id="rId{n}"/>')
        rels.append((f"rId{n}", f"{R}/slide", f"slides/slide{n}.xml"))
    parts["ppt/presentation.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><p:presentation xmlns:p="{P}" xmlns:r="{R}">'
        f"<p:sldIdLst>{''.join(ids)}</p:sldIdLst></p:presentation>"
    )
    parts["ppt/_rels/presentation.xml.rels"] = _rels(rels)
    parts["_rels/.rels"] = _rels([("rId1", DOC, "ppt/presentation.xml")])
    parts["[Content_Types].xml"] = (
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
    )
    return _zip(parts)
