"""Excel and PowerPoint files: read with the standard library, written so a row or a slide found
by search still makes sense on its own, and refused safely when hostile or damaged."""

import io
import zipfile
from pathlib import Path

import pytest
from office import Date, Percent, pptx, xlsx

from app.extract import UnsupportedFile, extract_text

FIXTURES = Path(__file__).parent / "fixtures"


def _upload(client, headers, name, data, **form):
    form = {"classification": "internal", **form}
    return client.post(
        "/api/documents", headers=headers, files={"file": (name, io.BytesIO(data), "application/octet-stream")}, data=form
    )


# ---------------------------------------------------------------- files saved by real tools


@pytest.mark.parametrize("name", ["expense-limits.xlsx", "expense-limits-libreoffice.xlsx"])
def test_a_real_workbook_reads_as_labelled_rows(name):
    text = extract_text(name, (FIXTURES / name).read_bytes())
    assert text.startswith("# Allowances\n\nTravel and expense limits, FY 2026")  # a title above the table stays a line
    assert (
        "City tier: Tier 2; Hotel per night (INR): 5000; Meals per day (INR): 900; Effective from: 2026-04-01; GST: 12%." in text
    ), "each row carries its column labels, dates read as dates and percentages as percentages"
    assert "# Approvers" in text and "Amount up to (INR): Above; Approver: CFO." in text
    assert "# Lists (hidden sheet)" in text


def test_a_formula_shows_the_value_the_spreadsheet_saved():
    # LibreOffice saves each formula's last result; openpyxl doesn't, so that copy has no value to show.
    assert "City tier: Average; Hotel per night (INR): 5166.666667." in extract_text(
        "x.xlsx", (FIXTURES / "expense-limits-libreoffice.xlsx").read_bytes()
    )
    assert "City tier: Average." in extract_text("x.xlsx", (FIXTURES / "expense-limits.xlsx").read_bytes())


@pytest.mark.parametrize("name", ["benefits-2026.pptx", "benefits-2026-libreoffice.pptx"])
def test_a_real_deck_reads_slide_by_slide_with_notes_and_tables(name):
    text = extract_text(name, (FIXTURES / name).read_bytes())
    assert text.split("\n\n")[:2] == ["# Slide 1: Benefits 2026", "- What changes on 1 January"]
    assert (
        "# Slide 2: Health insurance\n\n- Parents are covered from 1 January 2026\n- Cover goes up to INR 5 lakh a year" in text
    )
    assert "Speaker notes: Remind people to add parents in the HR portal before 15 January." in text
    assert "- Gym | INR 1,500 a month" in text
    assert "Click to" not in text, "placeholder prompts from the layout aren't content"


# ---------------------------------------------------------------- details


def test_workbook_details():
    data = xlsx(
        {
            "Data": [
                ["Name & role", "Joined", "Bonus", "Remote"],
                ["Asha <lead>", Date(46113.5), Percent(0.075), True],
                [None, None, None, None],  # an empty row is skipped
                ["Ravi", None, Percent(0.05), False],
            ],
            "Numbers only": [[1, 2.5], [3, 1e-7]],
        },
        inline=True,
    )
    text = extract_text("t.xlsx", data)
    assert "Name & role: Asha <lead>; Joined: 2026-04-01 12:00; Bonus: 7.5%; Remote: TRUE." in text
    assert "Name & role: Ravi; Bonus: 5%; Remote: FALSE." in text, "empty cells are left out, not shown as blanks"
    assert "# Numbers only\n\n1 | 2.5\n\n3 | 1e-07" in text, "a sheet with no row of labels reads row by row"


def test_dates_in_workbooks_that_count_from_1904():
    # Old Mac Excel files count days from 1904: the same date is 1462 days smaller.
    assert "When: 2026-04-01" in extract_text("t.xlsx", xlsx({"S": [["When", "What"], [Date(46113 - 1462), "x"]]}, date1904=True))


def test_deck_details():
    text = extract_text(
        "t.pptx",
        pptx(
            [
                {"title": "Hotel limits", "table": [["Tier", "Limit"], ["Metro", "INR 7,000"]], "hidden": True},
                {"bullets": ["A slide without a title"]},
                {},  # an empty slide adds nothing
            ]
        ),
    )
    assert text == (
        "# Slide 1: Hotel limits (hidden slide)\n\n- Tier | Limit\n- Metro | INR 7,000\n\n# Slide 2\n\n- A slide without a title"
    )


# ---------------------------------------------------------------- refusals


def test_old_office_formats_get_a_clear_message():
    for ext, new in ((".xls", ".xlsx"), (".ppt", ".pptx"), (".doc", ".docx")):
        with pytest.raises(UnsupportedFile, match=f"Save it as \\{new}"):
            extract_text("old" + ext, b"\xd0\xcf\x11\xe0")


def test_damaged_and_password_protected_files_are_refused_plainly():
    # A password-protected Office file isn't a zip at all (it's an encrypted container).
    for name in ("x.xlsx", "x.pptx"):
        with pytest.raises(UnsupportedFile, match="couldn't be read. It may be damaged or password-protected"):
            extract_text(name, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "not a workbook")
    with pytest.raises(UnsupportedFile, match="couldn't be read"):
        extract_text("x.xlsx", buf.getvalue())


def test_xml_entity_attacks_are_refused():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>&lol2;</t></si></sst>'
    )
    buf = io.BytesIO(xlsx({"S": [["a", "b"]]}))
    out = io.BytesIO()
    with zipfile.ZipFile(buf) as src, zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, bomb if item.filename == "xl/sharedStrings.xml" else src.read(item))
    with pytest.raises(UnsupportedFile, match="couldn't be read"):
        extract_text("x.xlsx", out.getvalue())


def test_zip_bombs_are_refused(client, login):
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(xlsx({"S": [["a", "b"]]}))) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b"<sheetData>", b"<sheetData>" + b" " * (31 * 1024 * 1024))
            dst.writestr(item.filename, data)
    assert len(buf.getvalue()) < 200_000  # tiny on the wire, huge when inflated
    r = _upload(client, login("rohan"), "bomb.xlsx", buf.getvalue())
    assert r.status_code == 415 and "too large" in r.json()["detail"]


def test_too_many_sheets_are_refused():
    with pytest.raises(UnsupportedFile, match="more than 100 sheets"):
        extract_text("x.xlsx", xlsx({f"S{i}": [["a"]] for i in range(101)}))


# ---------------------------------------------------------------- end to end


def test_an_uploaded_spreadsheet_answers_questions_with_the_row_as_evidence(client, login):
    rohan = login("rohan")
    r = _upload(
        client,
        rohan,
        "Approval limits.xlsx",
        xlsx(
            {
                "Approvers": [
                    ["Amount up to (INR)", "Approver"],
                    [50000, "Manager"],
                    [200000, "Department head"],
                    ["Above", "Chief Financial Officer"],
                ]
            }
        ),
    )
    assert r.status_code == 201, r.text
    doc = r.json()["document"]
    page = client.get(f"/api/documents/{doc['id']}", headers=rohan).json()
    assert any("Amount up to (INR): 200000; Approver: Department head." in p["text"] for p in page["passages"])
    reply = client.post(
        "/api/assistant/chat", headers=rohan, json={"message": "Who is the approver for amounts up to 200000?"}
    ).json()["reply"]
    assert doc["id"] in [c["id"] for c in reply["meta"]["citations"]]
    assert "Department head" in reply["content"]
    download = client.get(f"/api/documents/{doc['id']}/download", headers=rohan)
    assert download.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_a_scanned_pdf_gets_a_clear_message(client, login):
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(200, 200)  # a page with no text on it, like a scan
    buf = io.BytesIO()
    w.write(buf)
    r = _upload(client, login("rohan"), "scan.pdf", buf.getvalue())
    assert r.status_code == 415 and "probably scanned" in r.json()["detail"]
