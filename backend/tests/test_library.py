"""The library map: every document with its size and type, duplicates, and clean-up suggestions."""

import gzip
import io
import json
from datetime import timedelta

from sqlalchemy import select

from app import library
from app.database import SessionLocal
from app.models import AuditEvent, Document, User, utcnow
from app.seed_docs import DOCS

HANDBOOK = next(d["body"] for d in DOCS if d["title"] == "Employee Handbook 2026")


def _map(client, headers, **params) -> dict:
    r = client.get("/api/admin/library", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _upload(client, headers, content: str, filename: str = "notes.md", **form):
    files = {"file": (filename, io.BytesIO(content.encode()), "text/markdown")}
    r = client.post("/api/documents", headers=headers, files=files, data={"classification": "internal", **form})
    assert r.status_code == 201, r.text
    return r.json()["document"]["id"]


def _by_id(m: dict) -> dict[int, dict]:
    return {d["id"]: d for d in m["documents"]}


def test_only_administrators_see_the_library_map(client, login):
    assert client.get("/api/admin/library").status_code == 401
    assert client.get("/api/admin/library", headers=login("meera")).status_code == 403  # the CEO isn't an admin
    assert client.get("/api/admin/library", headers=login("dev")).status_code == 200


def test_it_lists_every_document_with_its_size_type_team_and_folder(client, login):
    admin = login("dev")
    m = _map(client, admin)
    with SessionLocal() as db:
        docs = {d.id: d for d in db.scalars(select(Document))}
    assert {d["id"] for d in m["documents"]} == set(docs) and not m["truncated"]
    assert m["totals"]["documents"] == len(docs)
    assert m["totals"]["bytes"] == sum(d.size for d in docs.values())
    assert m["totals"]["passages"] == sum(d["passages"] for d in m["documents"]) > 0

    handbook = next(d for d in m["documents"] if d["title"] == "Employee Handbook 2026")
    assert handbook["kind"] == "markdown" and handbook["bytes"] == docs[handbook["id"]].size
    assert handbook["folder"] == "" and handbook["source_id"] is None  # uploaded here
    wifi = next(d for d in m["documents"] if d["title"] == "Wi-Fi and Printers")  # from the demo shared drive
    assert wifi["kind"] == "text" and wifi["folder"] == "Company" and wifi["source_id"] is not None

    kinds = {k["kind"]: k for k in m["kinds"]}
    assert set(kinds) == {"markdown", "text"}
    assert sum(k["documents"] for k in m["kinds"]) == len(docs)
    assert sum(k["bytes"] for k in m["kinds"]) == m["totals"]["bytes"]
    assert [k["bytes"] for k in m["kinds"]] == sorted((k["bytes"] for k in m["kinds"]), reverse=True)
    assert {t["name"] for t in m["teams"]} >= {"Human Resources", "Engineering"}
    assert [s["name"] for s in m["sources"]] == ["Shared drive (demo)"]
    assert m["duplicates"] == []  # the demo library has none


def test_a_large_map_is_sent_compressed(client, login):
    r = client.get("/api/admin/library", headers={**login("dev"), "Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip" and r.headers["cache-control"] == "no-store"
    raw = client.get("/api/admin/library", headers={**login("dev"), "Accept-Encoding": "identity"})
    assert "content-encoding" not in raw.headers
    assert json.loads(raw.content)["totals"] == r.json()["totals"]
    assert len(gzip.compress(raw.content)) < len(raw.content)


def test_the_same_file_twice_is_a_duplicate_and_the_less_used_copy_is_the_one_to_clean_up(client, login):
    text = "Quarterly vendor review checklist.\n\n" + " ".join(f"Item {i}: confirm the vendor contract and invoices." for i in range(30))
    # Different teams that can't see each other's internal files can upload the same file.
    hr_copy = _upload(client, login("neha"), text, filename="vendor-review.md", title="Vendor review (HR)")
    sales_copy = _upload(client, login("karan"), text, filename="vendor-review.md", title="Vendor review (Sales)")
    client.get(f"/api/documents/{sales_copy}", headers=login("karan"))  # Sales uses theirs

    m = _map(client, login("dev"))
    groups = [g for g in m["duplicates"] if g["kind"] == "same_file"]
    assert groups == [{"kind": "same_file", "similarity": 1.0, "documents": sorted([hr_copy, sales_copy])}]
    docs = _by_id(m)
    assert docs[hr_copy]["duplicate"] == docs[sales_copy]["duplicate"] == m["duplicates"].index(groups[0])
    assert docs[sales_copy]["opens"] == 1

    [item] = [c for c in m["cleanup"] if c["kind"] == "same_file"]
    assert item["doc_id"] == hr_copy and item["keep_id"] == sales_copy
    assert "The same file as “Vendor review (Sales)”" in item["why"]
    # Same text too, but it's reported once, as the same file.
    assert not [g for g in m["duplicates"] if g["kind"] == "same_text"]

    # Deleting the extra copy (with the existing delete) clears it.
    assert client.delete(f"/api/documents/{hr_copy}", headers=login("dev")).status_code == 204
    m = _map(client, login("dev"))
    assert m["duplicates"] == [] and not [c for c in m["cleanup"] if c["doc_id"] == hr_copy]


def test_almost_the_same_text_is_found_and_unrelated_documents_are_not(client, login, doc_id):
    handbook = doc_id("Employee Handbook 2026")
    # An older copy someone kept: a few figures differ, the rest is the same.
    old = HANDBOOK.replace("24 days of earned leave", "21 days of earned leave").replace("60 days", "45 days")
    copy = _upload(client, login("vikram"), old, filename="handbook-2025.md", title="Employee Handbook (old copy)")

    m = _map(client, login("dev"))
    [group] = m["duplicates"]
    assert group["kind"] == "same_text" and group["documents"] == sorted([handbook, copy])
    assert library.NEAR <= group["similarity"] < 1.0
    [item] = [c for c in m["cleanup"] if c["kind"] == "same_text"]
    assert {item["doc_id"], item["keep_id"]} == {handbook, copy}
    assert "Almost the same text as" in item["why"] and "% the same" in item["why"]
    assert m["totals"]["compared"] >= len(m["documents"]) - 5  # only very short documents aren't compared

    # A new version with different text: no longer a near-duplicate.
    new = "Facilities request form.\n\n" + " ".join(f"Room {i} needs a projector and a whiteboard." for i in range(40))
    files = {"file": ("handbook-2025.md", io.BytesIO(new.encode()), "text/markdown")}
    assert client.post(f"/api/documents/{copy}/versions", headers=login("vikram"), files=files).status_code == 200
    assert _map(client, login("dev"))["duplicates"] == []


def test_sketches_estimate_shared_text():
    words = [f"w{i}" for i in range(400)]
    a = library.sketch(" ".join(words))
    assert library.resemblance(a, library.sketch(" ".join(words))) == 1.0
    assert library.resemblance(a, library.sketch(" ".join(f"x{i}" for i in range(400)))) == 0.0
    # Change the last tenth of the words: about 0.8 of the five-word runs are shared.
    changed = library.sketch(" ".join(words[:360] + [f"y{i}" for i in range(40)]))
    assert 0.65 <= library.resemblance(a, changed) <= 0.95
    assert library.sketch("Too short to compare.") is None
    assert len(a) == library.K


def test_file_types_come_from_the_extension():
    assert library.kind_of("Q3 report.PDF") == "pdf"
    assert library.kind_of("budget.xlsx") == library.kind_of("export.csv") == "sheet"
    assert library.kind_of("deck.pptx") == "slides" and library.kind_of("plan.docx") == "word"
    assert library.kind_of("notes.md") == "markdown" and library.kind_of("readme.txt") == "text"
    assert library.kind_of("scan", "application/pdf") == "pdf" and library.kind_of("blob.bin") == "other"


def test_old_documents_people_still_use_and_unused_ones_are_suggested_for_clean_up(client, login, doc_id):
    holidays = doc_id("Holiday Calendar 2026")
    conduct = doc_id("Code of Conduct")
    dev = None
    with SessionLocal() as db:
        dev = db.scalar(select(User.id).where(User.email == "dev@northwind.example"))
        long_ago = utcnow() - timedelta(days=400)
        for d in db.scalars(select(Document).where(Document.id.in_([holidays, conduct]))):
            d.created_at = d.updated_at = long_ago
        db.execute(AuditEvent.__table__.delete().where(AuditEvent.target_id.in_([holidays, conduct])))
        db.add(AuditEvent(actor_id=dev, action="document.view", target_type="document", target_id=holidays, detail={}))
        db.commit()

    m = _map(client, login("dev"))
    by_doc = {c["doc_id"]: c for c in m["cleanup"]}
    assert by_doc[holidays]["kind"] == "old_but_used"
    assert by_doc[holidays]["why"] == "Not updated for 13 months, but opened once in the last 90 days. Check it's still right."
    assert by_doc[conduct]["kind"] == "unused"
    assert by_doc[conduct]["why"] == "Nobody opened it or got it in an answer in the last 90 days."
    # Something added recently isn't "unused" yet.
    fresh = _upload(client, login("rohan"), "Brand new page about the build cache. " * 20, filename="cache.md", title="Build cache")
    assert fresh not in {c["doc_id"] for c in _map(client, login("dev"))["cleanup"]}


def test_answers_count_as_use(client, login, doc_id):
    handbook = doc_id("Employee Handbook 2026")
    before = _by_id(_map(client, login("dev")))[handbook]["cited"]
    r = client.post("/api/assistant/chat", headers=login("priya"), json={"message": "How many days of earned leave do I get?"})
    assert r.status_code == 200
    assert handbook in [c["id"] for c in r.json()["reply"]["meta"]["citations"]]
    assert _by_id(_map(client, login("dev")))[handbook]["cited"] == before + 1
