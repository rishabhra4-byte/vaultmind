"""The library as a file explorer: folders, moving, pins, recent, the recycle bin and ZIP downloads."""

import io
import zipfile
from datetime import timedelta

from sqlalchemy import select

from app import explorer, storage
from app.database import SessionLocal
from app.models import AccessGrant, DeletedDocument, Document, DocumentVersion, Pin, User, utcnow
from app.scheduler import empty_old_bin
from app.seed import seed_demo_folders


def _doc(client, headers, title):
    docs = client.get("/api/documents", headers=headers).json()
    return next(d for d in docs if d["title"] == title)


def _folder(client, headers, name, parent_id=None, expect=201):
    r = client.post("/api/library/folders", headers=headers, json={"name": name, "parent_id": parent_id})
    assert r.status_code == expect, r.text
    return r.json()


def _folders(client, headers):
    return {f["name"]: f for f in client.get("/api/library/folders", headers=headers).json()}


def _move(client, headers, to, docs=(), folders=()):
    r = client.post(
        "/api/library/move", headers=headers, json={"document_ids": list(docs), "folder_ids": list(folders), "to": to}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _upload(client, headers, text, title, **form):
    files = {"file": (f"{title}.txt", io.BytesIO(text.encode()), "text/plain")}
    r = client.post("/api/documents", headers=headers, files=files, data={"title": title, "classification": "internal", **form})
    assert r.status_code == 201, r.text
    return r.json()["document"]


# ===================================================================== folders


def test_folders_can_be_made_nested_renamed_and_moved(client, login):
    rohan = login("rohan")
    eng = _folder(client, rohan, "  Engineering   docs ")
    assert eng["name"] == "Engineering docs" and eng["parent_id"] is None and eng["can_edit"] is True
    guides = _folder(client, rohan, "Guides", eng["id"])
    assert guides["parent_id"] == eng["id"]

    r = client.patch(f"/api/library/folders/{guides['id']}", headers=rohan, json={"name": "How-to guides"})
    assert r.status_code == 200 and r.json()["name"] == "How-to guides"
    r = client.patch(f"/api/library/folders/{guides['id']}", headers=rohan, json={"parent_id": None})
    assert r.status_code == 200 and r.json()["parent_id"] is None


def test_folder_names_are_checked(client, login):
    rohan = login("rohan")
    top = _folder(client, rohan, "Plans")
    assert "already a folder" in _folder(client, rohan, "plans", expect=409)["detail"]
    _folder(client, rohan, "Plans", top["id"])  # the same name somewhere else is fine
    for bad in ("a/b", "a\\b", "   ", "..", "x" * 121):
        assert client.post("/api/library/folders", headers=rohan, json={"name": bad}).status_code == 422, bad
    assert client.post("/api/library/folders", headers=rohan, json={"name": "Lost", "parent_id": 9999}).status_code == 404


def test_a_folder_cannot_go_inside_itself_or_too_deep(client, login):
    rohan = login("rohan")
    a = _folder(client, rohan, "A")
    b = _folder(client, rohan, "B", a["id"])
    r = client.patch(f"/api/library/folders/{a['id']}", headers=rohan, json={"parent_id": b["id"]})
    assert r.status_code == 409 and "into itself" in r.json()["detail"]
    parent = b["id"]
    for depth in range(3, explorer.MAX_DEPTH + 1):
        parent = _folder(client, rohan, f"Level {depth}", parent)["id"]
    assert "deep" in _folder(client, rohan, "One too many", parent, expect=409)["detail"]


def test_removing_a_folder_moves_what_was_in_it_up_and_deletes_nothing(client, login):
    rohan = login("rohan")
    outer = _folder(client, rohan, "Outer")
    inner = _folder(client, rohan, "Inner", outer["id"])
    kept = _folder(client, rohan, "Kept", inner["id"])
    guide = _doc(client, rohan, "Backend Onboarding Guide")
    assert _move(client, rohan, inner["id"], docs=[guide["id"]])["moved"] == 1

    assert client.delete(f"/api/library/folders/{inner['id']}", headers=rohan).status_code == 204
    folders = _folders(client, rohan)
    assert "Inner" not in folders and folders["Kept"]["parent_id"] == outer["id"] and kept["id"] == folders["Kept"]["id"]
    assert _doc(client, rohan, "Backend Onboarding Guide")["folder_id"] == outer["id"]


def test_only_the_person_who_made_a_folder_or_an_admin_can_change_it(client, login):
    rohan, priya, admin = login("rohan"), login("priya"), login("dev")
    folder = _folder(client, rohan, "Team notes")
    guide = _doc(client, rohan, "Backend Onboarding Guide")
    _move(client, rohan, folder["id"], docs=[guide["id"]])  # now Priya sees the folder too

    assert _folders(client, priya)["Team notes"]["can_edit"] is False
    assert client.patch(f"/api/library/folders/{folder['id']}", headers=priya, json={"name": "Mine"}).status_code == 403
    assert client.delete(f"/api/library/folders/{folder['id']}", headers=priya).status_code == 403
    assert client.patch(f"/api/library/folders/{folder['id']}", headers=admin, json={"name": "Team wiki"}).status_code == 200


# ===================================================================== what a folder shows, and to whom


def test_a_folder_is_seen_only_by_its_maker_and_people_who_see_something_in_it(client, login):
    meera, aarav, karan, admin = login("meera"), login("aarav"), login("karan"), login("dev")
    secret = _folder(client, meera, "Project Falcon")
    plans = _folder(client, meera, "Layoffs 2027", secret["id"])
    falcon = _doc(client, meera, "Project Falcon: Acquisition Evaluation")  # restricted: nobody else even sees its title
    _move(client, meera, plans["id"], docs=[falcon["id"]])

    for other in (aarav, karan):
        assert _folders(client, other) == {}
        assert client.get(f"/api/documents?folder_id={plans['id']}", headers=other).status_code == 404
        assert client.post("/api/library/folders", headers=other, json={"name": "x", "parent_id": plans["id"]}).status_code == 404
        assert client.put("/api/library/pins", headers=other, json={"folder_id": plans["id"]}).status_code == 404
    assert set(_folders(client, admin)) == {"Project Falcon", "Layoffs 2027"}

    # A document Karan can see makes its folder, and the folders above it, appear for him.
    handbook = _doc(client, admin, "Employee Handbook 2026")
    _move(client, admin, plans["id"], docs=[handbook["id"]])
    seen = _folders(client, karan)
    assert set(seen) == {"Project Falcon", "Layoffs 2027"} and seen["Layoffs 2027"]["documents"] == 1
    assert [d["title"] for d in client.get(f"/api/documents?folder_id={plans['id']}", headers=karan).json()] == [
        "Employee Handbook 2026"
    ]


def test_moving_a_document_changes_where_it_is_and_never_who_can_open_it(client, login):
    rohan, aarav = login("rohan"), login("aarav")
    folder = _folder(client, rohan, "Runbooks")
    runbook = _doc(client, rohan, "Incident Response Runbook")  # Associate and above: an intern can't open it
    before = client.get(f"/api/documents/{runbook['id']}", headers=aarav).json()["access"]
    assert before["allowed"] is False
    _move(client, rohan, folder["id"], docs=[runbook["id"]])
    assert client.get(f"/api/documents/{runbook['id']}", headers=aarav).json()["access"] == before
    assert client.get(f"/api/documents/{runbook['id']}/download", headers=aarav).status_code == 403


def test_moving_or_arranging_a_document_does_not_make_it_newer(client, login):
    rohan = login("rohan")
    before = {d["title"]: d["updated_at"] for d in client.get("/api/documents", headers=login("dev")).json()}
    folder = _folder(client, rohan, "Shelf")
    guide = _doc(client, rohan, "Backend Onboarding Guide")
    _move(client, rohan, folder["id"], docs=[guide["id"]])
    assert client.delete(f"/api/library/folders/{folder['id']}", headers=rohan).status_code == 204
    with SessionLocal() as db:
        seed_demo_folders(db)
    after = {d["title"]: d["updated_at"] for d in client.get("/api/documents", headers=login("dev")).json()}
    assert after == before  # so "Travel Policy 2024" is still old, and still flagged as old


def test_moving_needs_the_right_to_change_the_document(client, login):
    rohan, priya = login("rohan"), login("priya")
    folder = _folder(client, priya, "Priya's shelf")
    guide = _doc(client, priya, "Backend Onboarding Guide")  # uploaded by Rohan; Priya is not a manager
    out = _move(client, priya, folder["id"], docs=[guide["id"], 99999])
    assert out["moved"] == 0 and [s["reason"] for s in out["skipped"]] == [
        "Only its owner, a manager of its team or an admin can move it",
        "Document not found",
    ]
    # Rohan may move it, but not into a folder he can't see: Priya's is empty and hers alone.
    r = client.post("/api/library/move", headers=rohan, json={"document_ids": [guide["id"]], "to": folder["id"]})
    assert r.status_code == 404
    assert _move(client, login("dev"), folder["id"], docs=[guide["id"]])["moved"] == 1
    assert _doc(client, rohan, "Backend Onboarding Guide")["folder_id"] == folder["id"]  # and now he sees it there


def test_a_folder_lists_what_is_in_it_and_a_search_also_looks_inside_its_folders(client, login):
    rohan = login("rohan")
    eng = _folder(client, rohan, "Engineering")
    ops = _folder(client, rohan, "Operations", eng["id"])
    guide, runbook = _doc(client, rohan, "Backend Onboarding Guide"), _doc(client, rohan, "Incident Response Runbook")
    _move(client, rohan, eng["id"], docs=[guide["id"]])
    _move(client, rohan, ops["id"], docs=[runbook["id"]])

    def titles(query):
        return [d["title"] for d in client.get(f"/api/documents?{query}", headers=rohan).json()]

    assert titles(f"folder_id={eng['id']}") == ["Backend Onboarding Guide"]
    assert titles(f"folder_id={ops['id']}") == ["Incident Response Runbook"]
    inside = titles(f"folder_id={eng['id']}&q=incident")
    assert inside[0] == "Incident Response Runbook" and set(inside) <= {"Incident Response Runbook", "Backend Onboarding Guide"}
    assert "Backend Onboarding Guide" not in titles(f"folder_id={ops['id']}&q=onboarding")  # nor above the folder
    assert "Expense Reimbursement Policy" in titles("q=reimbursement")
    assert titles(f"folder_id={eng['id']}&q=reimbursement") == []
    assert len(titles("")) > 10  # no folder: everything, as before


def test_an_upload_can_go_straight_into_a_folder(client, login):
    rohan = login("rohan")
    folder = _folder(client, rohan, "Offsites")
    doc = _upload(
        client, rohan, "Team offsite in Coorg, 12 to 14 March. Bring walking shoes.", "Offsite plan", folder_id=folder["id"]
    )
    assert doc["folder_id"] == folder["id"]
    files = {"file": ("x.txt", io.BytesIO(b"Another plan for another offsite."), "text/plain")}
    r = client.post("/api/documents", headers=rohan, files=files, data={"title": "X", "folder_id": 9999})
    assert r.status_code == 404


def test_the_demo_folders_are_arranged_once(client, login):
    with SessionLocal() as db:
        assert seed_demo_folders(db) == 9
        assert seed_demo_folders(db) == 0
    folders = _folders(client, login("priya"))
    assert folders["Operations"]["parent_id"] == folders["Engineering"]["id"]
    # An intern sees "Reviews and pay" because he may ask for what's in it, and counts only that.
    intern, admin = _folders(client, login("aarav")), _folders(client, login("dev"))
    assert set(intern) <= set(admin) and all(intern[name]["documents"] <= admin[name]["documents"] for name in intern)
    assert _doc(client, login("priya"), "API Design Guidelines")["folder_id"] == folders["Engineering"]["id"]


# ===================================================================== pins and recent


def test_pins_and_recently_opened_documents_make_the_home_view(client, login):
    priya = login("priya")
    guide, api = _doc(client, priya, "Backend Onboarding Guide"), _doc(client, priya, "API Design Guidelines")
    folder = _folder(client, priya, "Reading list")
    assert client.put("/api/library/pins", headers=priya, json={"document_id": guide["id"]}).status_code == 204
    assert client.put("/api/library/pins", headers=priya, json={"document_id": guide["id"]}).status_code == 204  # twice is fine
    assert client.put("/api/library/pins", headers=priya, json={"folder_id": folder["id"]}).status_code == 204
    assert client.put("/api/library/pins", headers=priya, json={}).status_code == 422
    client.get(f"/api/documents/{api['id']}", headers=priya)
    client.get(f"/api/documents/{guide['id']}", headers=priya)

    home = client.get("/api/library/home", headers=priya).json()
    assert [d["title"] for d in home["pinned_documents"]] == ["Backend Onboarding Guide"]
    assert home["pinned_documents"][0]["pinned"] is True
    assert [f["name"] for f in home["pinned_folders"]] == ["Reading list"] and home["pinned_folders"][0]["pinned"] is True
    assert [d["title"] for d in home["recent"]][:2] == ["Backend Onboarding Guide", "API Design Guidelines"]
    assert _doc(client, priya, "Backend Onboarding Guide")["pinned"] is True
    assert _doc(client, login("rohan"), "Backend Onboarding Guide")["pinned"] is False  # pins are each person's own

    assert client.delete(f"/api/library/pins?document_id={guide['id']}", headers=priya).status_code == 204
    assert client.get("/api/library/home", headers=priya).json()["pinned_documents"] == []


def test_recent_only_lists_documents_the_person_can_still_open(client, login):
    karan, admin = login("karan"), login("dev")
    playbook = _doc(client, karan, "Sales Playbook")
    client.get(f"/api/documents/{playbook['id']}", headers=karan)
    assert "Sales Playbook" in [d["title"] for d in client.get("/api/library/home", headers=karan).json()["recent"]]
    r = client.patch(f"/api/documents/{playbook['id']}", headers=admin, json={"classification": "restricted"})
    assert r.status_code == 200
    assert "Sales Playbook" not in [d["title"] for d in client.get("/api/library/home", headers=karan).json()["recent"]]


# ===================================================================== recycle bin


def _bin(client, headers):
    return client.get("/api/library/bin", headers=headers).json()


def test_a_deleted_document_is_gone_everywhere_and_waits_in_the_recycle_bin(client, login):
    rohan = login("rohan")
    doc = _upload(client, rohan, "The quarterly zeppelin budget was approved by the airship committee.", "Zeppelin budget")
    assert client.delete(f"/api/documents/{doc['id']}", headers=rohan).status_code == 204

    assert client.get(f"/api/documents/{doc['id']}", headers=rohan).status_code == 404
    assert client.get("/api/documents?q=zeppelin", headers=rohan).json() == []
    reply = client.post("/api/assistant/chat", headers=rohan, json={"message": "What is the zeppelin budget?"}).json()["reply"]
    assert "airship committee" not in reply["content"]
    items = _bin(client, rohan)
    assert [(i["title"], i["original_id"], i["deleted_by"]) for i in items] == [("Zeppelin budget", doc["id"], "Rohan Iyer")]
    with SessionLocal() as db:
        item = db.scalar(select(DeletedDocument))
        assert storage.get(item.storage_key)  # the file is kept while the bin points at it
        assert (
            timedelta(days=29) < item.expires_at.replace(tzinfo=None) - item.deleted_at.replace(tzinfo=None) <= timedelta(days=30)
        )


def test_the_bin_shows_a_deleted_document_only_to_people_who_could_have_deleted_it(client, login):
    priya = login("priya")
    doc = _upload(client, priya, "Notes from the caching guild, with the eviction policy we agreed.", "Caching guild notes")
    client.delete(f"/api/documents/{doc['id']}", headers=priya)
    item_id = _bin(client, priya)[0]["id"]
    for who, sees in (("priya", True), ("rohan", True), ("dev", True), ("aarav", False), ("karan", False), ("ananya", False)):
        headers = login(who)
        assert bool(_bin(client, headers)) is sees, who
        if not sees:
            assert client.post(f"/api/library/bin/{item_id}/restore", headers=headers).status_code == 404
            assert client.delete(f"/api/library/bin/{item_id}", headers=headers).status_code == 404


def test_restoring_brings_a_document_back_as_it_was(client, login):
    rohan, karan = login("rohan"), login("karan")
    sales = next(t["id"] for t in client.get("/api/meta", headers=rohan).json()["teams"] if t["name"] == "Sales")
    folder = _folder(client, rohan, "Launches")
    doc = _upload(
        client,
        rohan,
        "Launch plan for the gyroscope feature: beta on 3 March.",
        "Gyroscope launch",
        share_team_ids=str(sales),
        folder_id=folder["id"],
    )
    files = {"file": ("v2.txt", io.BytesIO(b"Launch plan for the gyroscope feature: beta on 17 March."), "text/plain")}
    assert client.post(f"/api/documents/{doc['id']}/versions", headers=rohan, files=files).status_code == 200
    with SessionLocal() as db:
        db.add(
            AccessGrant(
                document_id=doc["id"],
                user_id=db.scalar(select(User.id).where(User.email.like("aarav@%"))),
                expires_at=utcnow() + timedelta(days=5),
            )
        )
        db.commit()
    client.put("/api/library/pins", headers=karan, json={"document_id": doc["id"]})

    client.delete(f"/api/documents/{doc['id']}", headers=rohan)
    item = _bin(client, rohan)[0]
    assert item["folder"] == "Launches"
    r = client.post(f"/api/library/bin/{item['id']}/restore", headers=rohan)
    assert r.status_code == 200, r.text
    back = r.json()
    assert (back["id"], back["title"], back["version"], back["folder_id"]) == (doc["id"], "Gyroscope launch", 2, folder["id"])
    assert back["shared_with"] == ["Sales"] and back["created_at"] == doc["created_at"]
    assert _bin(client, rohan) == []

    assert [d["title"] for d in client.get("/api/documents?q=gyroscope", headers=karan).json()] == ["Gyroscope launch"]
    versions = client.get(f"/api/documents/{doc['id']}/versions", headers=rohan).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert "17 March" in client.get(f"/api/documents/{doc['id']}", headers=rohan).json()["preview"]
    with SessionLocal() as db:  # temporary access and pins are not brought back
        assert db.scalar(select(AccessGrant).where(AccessGrant.document_id == doc["id"])) is None
        assert db.scalar(select(Pin).where(Pin.document_id == doc["id"])) is None
        assert db.scalar(
            select(DocumentVersion.text).where(DocumentVersion.document_id == doc["id"], DocumentVersion.version == 1)
        )


def test_a_restored_document_gets_a_new_number_if_its_old_one_was_taken(client, login):
    rohan = login("rohan")
    doc = _upload(client, rohan, "Seating chart for the fourth floor, by team.", "Seating chart")
    client.delete(f"/api/documents/{doc['id']}", headers=rohan)
    with SessionLocal() as db:  # something else now has that number
        item = db.scalar(select(DeletedDocument))
        other = db.scalar(select(Document).order_by(Document.id))
        db.add(
            Document(
                id=item.original_id,
                title="Squatter",
                filename="s.txt",
                sha256="0" * 64,
                storage_key=other.storage_key,
                owner_team_id=other.owner_team_id,
            )
        )
        db.commit()
    back = client.post(f"/api/library/bin/{_bin(client, rohan)[0]['id']}/restore", headers=rohan).json()
    assert back["id"] != doc["id"] and back["title"] == "Seating chart"


def test_deleting_for_good_removes_the_file_unless_something_else_uses_it(client, login):
    rohan = login("rohan")
    doc = _upload(client, rohan, "Parking allocation for the basement, level B3 only.", "Parking B3")
    client.delete(f"/api/documents/{doc['id']}", headers=rohan)
    with SessionLocal() as db:
        key = db.scalar(select(DeletedDocument.storage_key))
    assert client.delete(f"/api/library/bin/{_bin(client, rohan)[0]['id']}", headers=rohan).status_code == 204
    assert _bin(client, rohan) == []
    try:
        storage.get(key)
        raise AssertionError("the stored file should be gone")
    except FileNotFoundError:
        pass

    # The same file uploaded again while a deleted copy is in the bin: emptying the bin keeps it.
    first = _upload(client, rohan, "Fire drill assembly point is the east gate.", "Fire drill")
    client.delete(f"/api/documents/{first['id']}", headers=rohan)
    second = _upload(client, rohan, "Fire drill assembly point is the east gate.", "Fire drill again")
    assert client.delete("/api/library/bin", headers=rohan).status_code == 204
    assert client.get(f"/api/documents/{second['id']}/download", headers=rohan).status_code == 200


def test_the_bin_empties_itself_after_thirty_days(client, login):
    rohan = login("rohan")
    old = _upload(client, rohan, "Old notes about the stapler rota.", "Stapler rota")
    new = _upload(client, rohan, "New notes about the kettle rota.", "Kettle rota")
    for d in (old, new):
        client.delete(f"/api/documents/{d['id']}", headers=rohan)
    with SessionLocal() as db:
        item = db.scalar(select(DeletedDocument).where(DeletedDocument.title == "Stapler rota"))
        item.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert empty_old_bin() == 1
    assert [i["title"] for i in _bin(client, rohan)] == ["Kettle rota"]


# ===================================================================== several files at once


def test_a_zip_holds_only_the_documents_the_person_can_open(client, login):
    aarav = login("aarav")
    docs = client.get("/api/documents", headers=aarav).json()
    openable = [d for d in docs if d["access"]["allowed"]][:3]
    locked = next(d for d in docs if not d["access"]["allowed"])
    r = client.post("/api/library/zip", headers=aarav, json={"ids": [d["id"] for d in openable] + [locked["id"], 99999]})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip" and r.headers["cache-control"] == "no-store"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert sorted(names) == sorted(d["filename"] for d in openable)
    assert client.post("/api/library/zip", headers=aarav, json={"ids": [locked["id"]]}).status_code == 404
    assert client.post("/api/library/zip", headers=aarav, json={"ids": []}).status_code == 422


def test_two_files_with_one_name_both_fit_in_a_zip(client, login):
    rohan = login("rohan")
    a = _upload(client, rohan, "First notes about the spring picnic.", "notes")
    with SessionLocal() as db:
        doc = db.get(Document, a["id"])
        other = db.scalar(select(Document).where(Document.id != a["id"]).order_by(Document.id))
        other.filename = doc.filename
        other_id = other.id
        db.commit()
    r = client.post("/api/library/zip", headers=rohan, json={"ids": [a["id"], other_id]})
    assert sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == ["notes (2).txt", "notes.txt"]
