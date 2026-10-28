"""Groups, mirrored sharing, document versions, notifications and safe file clean-up."""

import io

from sqlalchemy import select

from app import storage
from app.database import SessionLocal
from app.models import (
    AclMode,
    AuditEvent,
    Classification,
    Document,
    Group,
    GroupMember,
    Notification,
    Source,
    SourceKind,
    Team,
    User,
)
from app.policy import can_approve, can_manage, can_view, is_discoverable_to
from app.services import ingest_document, remove_document, viewer


def _user(db, key: str) -> User:
    return db.scalar(select(User).where(User.email == f"{key}@northwind.example"))


def _team(db, name: str) -> Team:
    return db.scalar(select(Team).where(Team.name == name))


def _upload(client, headers, name, data, **form):
    form = {"classification": "internal", **form}
    return client.post("/api/documents", headers=headers, files={"file": (name, io.BytesIO(data), "text/plain")}, data=form)


# ---------------------------------------------------------------- groups


def test_sharing_with_a_group_opens_a_document_to_its_members_only(client, login):
    with SessionLocal() as db:
        squad = Group(name="Payments squad", description="Cross-team project")
        squad.members = [GroupMember(user_id=_user(db, "karan").id)]
        db.add(squad)
        db.commit()
        squad_id = squad.id

    rohan = login("rohan")
    r = _upload(client, rohan, "payments-plan.txt", b"Plan for the payments launch in March.", share_group_ids=str(squad_id))
    assert r.status_code == 201, r.text
    doc = r.json()["document"]
    assert doc["shared_with_groups"] == ["Payments squad"]

    karan = client.get(f"/api/documents/{doc['id']}", headers=login("karan")).json()  # Sales, but in the group
    assert karan["access"] == {**karan["access"], "allowed": True, "code": "group"}
    neha = client.get(f"/api/documents/{doc['id']}", headers=login("neha")).json()  # HR, not in the group
    assert neha["access"]["allowed"] is False


def test_unknown_groups_are_refused(client, login):
    r = _upload(client, login("rohan"), "x.txt", b"Some notes about the offsite.", share_group_ids="9999")
    assert r.status_code == 422


# ---------------------------------------------------------------- mirrored sharing (synced files)


def _mirrored_doc(db, principals: list[tuple[str, str]], title="Launch plan") -> Document:
    source = Source(kind=SourceKind.GOOGLE, name="Company Drive", owner_team_id=_team(db, "Engineering").id)
    db.add(source)
    db.flush()
    result = ingest_document(
        db,
        uploader=None,
        filename="launch.txt",
        data=f"{title}: we launch in March. Budget is approved.".encode(),
        mime_type="text/plain",
        title=title,
        owner_team_id=source.owner_team_id,
        classification=Classification.INTERNAL,
        source_id=source.id,
        external_id=f"file-{title}",
        external_url="https://drive.example/file",
        acl_mode=AclMode.MIRROR,
        principals=principals,
    )
    db.commit()
    return result.document


def test_mirrored_files_follow_the_sharing_from_their_source():
    with SessionLocal() as db:
        group = Group(name="Launch crew", provider="google", external_id="launch@northwind.example")
        group.members = [GroupMember(user_id=_user(db, "neha").id)]
        db.add(group)
        db.flush()
        doc = _mirrored_doc(db, [("user", "Karan@Northwind.example"), ("group", str(group.id))])
        doc = db.get(Document, doc.id)

        def allowed(key):
            u = _user(db, key)
            v = viewer(db, u)
            return can_view(u, doc, v.grants, v.groups)

        assert allowed("karan").code == "source" and allowed("karan").allowed  # shared by email, any case
        assert allowed("neha").allowed  # via the group
        rohan = allowed("rohan")  # the owning team's manager isn't on the file in Drive
        assert not rohan.allowed and "Google Drive" in rohan.reason
        assert not allowed("meera").allowed  # executives get no special access to mirrored files
        assert allowed("dev").allowed  # admins still can
        assert not is_discoverable_to(_user(db, "rohan"), doc)  # invisible, like in Drive
        assert not can_approve(_user(db, "rohan"), doc, _user(db, "aarav").id)
        assert not can_manage(_user(db, "rohan"), doc) and can_manage(_user(db, "dev"), doc)


def test_domain_and_company_wide_sharing():
    with SessionLocal() as db:
        domain_doc = _mirrored_doc(db, [("domain", "northwind.example")], title="All hands")
        company_doc = _mirrored_doc(db, [("company", "*")], title="Office map")
        other_domain = _mirrored_doc(db, [("domain", "partner.example")], title="Partner brief")
        aarav = _user(db, "aarav")
        assert can_view(aarav, db.get(Document, domain_doc.id)).allowed
        assert can_view(aarav, db.get(Document, company_doc.id)).allowed
        assert not can_view(aarav, db.get(Document, other_domain.id)).allowed


def test_people_without_access_cannot_see_mirrored_files_at_all(client, login):
    with SessionLocal() as db:
        doc = _mirrored_doc(db, [("user", "karan@northwind.example")], title="Quarterly launch plan")
    rohan = login("rohan")
    assert client.get(f"/api/documents/{doc.id}", headers=rohan).status_code == 404
    titles = {d["title"] for d in client.get("/api/documents?q=launch", headers=rohan).json()}
    assert "Quarterly launch plan" not in titles
    detail = client.get(f"/api/documents/{doc.id}", headers=login("karan")).json()
    assert detail["access"]["allowed"] and detail["source"]["label"] == "Google Drive"
    assert detail["external_url"] == "https://drive.example/file"
    # Synced files can't be deleted or re-shared from VaultMind; their source decides.
    dev = login("dev")
    assert client.delete(f"/api/documents/{doc.id}", headers=dev).status_code == 409
    assert client.patch(f"/api/documents/{doc.id}", headers=dev, json={"share_team_ids": [1]}).status_code == 409
    assert client.patch(f"/api/documents/{doc.id}", headers=dev, json={"ai_enabled": False}).json()["ai_enabled"] is False


def test_only_web_links_are_kept_as_source_links():
    from app.services import safe_external_url

    assert safe_external_url("javascript:alert(1)") is None
    assert safe_external_url("data:text/html,hi") is None
    assert safe_external_url(" https://drive.google.com/file/d/1 ") == "https://drive.google.com/file/d/1"


# ---------------------------------------------------------------- versions and "what changed"


def test_every_document_starts_with_version_1(client, login, doc_id):
    versions = client.get(f"/api/documents/{doc_id('Code of Conduct')}/versions", headers=login("karan")).json()
    assert [v["version"] for v in versions] == [1]
    changes = client.get(f"/api/documents/{doc_id('Code of Conduct')}/changes", headers=login("karan")).json()
    assert changes["from_version"] is None and changes["diff"] is None


def test_the_demo_expense_policy_has_a_second_version_and_karan_was_told(client, login, doc_id):
    karan = login("karan")
    expense = doc_id("Expense Reimbursement Policy")
    changes = client.get(f"/api/documents/{expense}/changes", headers=karan).json()
    assert (changes["from_version"], changes["to_version"]) == (1, 2)
    befores = [c["before"] for c in changes["diff"]["changed"]]
    assert any("6,000" in b for b in befores) and "7,000" in changes["summary"]
    notes = client.get("/api/notifications", headers=karan).json()["items"]
    assert any(n["kind"] == "document.changed" and n["link"].startswith(f"/library/{expense}") for n in notes)


def test_a_new_version_records_what_changed_and_tells_recent_readers(client, login):
    rohan, priya = login("rohan"), login("priya")
    v1 = b"# Travel\n\nThe hotel limit is 5,000 rupees a night.\n\nBook through the travel desk."
    doc = _upload(client, rohan, "travel.md", v1).json()["document"]
    client.get(f"/api/documents/{doc['id']}", headers=priya)  # Priya reads it

    v2 = b"# Travel\n\nThe hotel limit is 7,000 rupees a night.\n\nBook through the travel desk.\n\nTrains need no approval."
    r = client.post(
        f"/api/documents/{doc['id']}/versions", headers=rohan, files={"file": ("travel.md", io.BytesIO(v2), "text/markdown")}
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and "7,000" in r.json()["change_summary"]

    changes = client.get(f"/api/documents/{doc['id']}/changes", headers=priya).json()
    assert (changes["from_version"], changes["to_version"]) == (1, 2)
    assert changes["diff"]["changed"][0]["after"] == "The hotel limit is 7,000 rupees a night."
    assert changes["diff"]["added"] == ["Trains need no approval."]

    notes = client.get("/api/notifications", headers=priya).json()
    assert notes["unread"] == 1 and notes["items"][0]["kind"] == "document.changed"
    assert "7,000" in notes["items"][0]["body"]
    rohan_notes = client.get("/api/notifications", headers=rohan).json()["items"]
    assert all(n["kind"] != "document.changed" for n in rohan_notes)  # not told about his own edit

    # The search index follows the new text.
    hits = client.get("/api/documents?q=trains approval", headers=priya).json()
    assert hits and hits[0]["id"] == doc["id"]


def test_new_versions_are_checked_like_uploads(client, login):
    rohan = login("rohan")
    doc = _upload(client, rohan, "team.txt", b"Team lunch is on Friday at noon.").json()["document"]
    same = client.post(
        f"/api/documents/{doc['id']}/versions",
        headers=rohan,
        files={"file": ("team.txt", io.BytesIO(b"Team lunch is on Friday at noon."), "text/plain")},
    )
    assert same.status_code == 409
    sensitive = b"Team lunch is on Friday. Reimburse Ravi, PAN ABCDE1234F."
    blocked = client.post(
        f"/api/documents/{doc['id']}/versions", headers=rohan, files={"file": ("team.txt", io.BytesIO(sensitive), "text/plain")}
    )
    assert blocked.status_code == 409 and blocked.json()["detail"]["suggested_classification"] == "restricted"
    # Someone who can't manage it can't replace it.
    other = client.post(
        f"/api/documents/{doc['id']}/versions",
        headers=login("priya"),
        files={"file": ("team.txt", io.BytesIO(b"Changed by someone else."), "text/plain")},
    )
    assert other.status_code == 403


def test_document_pages_return_numbered_passages(client, login, doc_id):
    detail = client.get(f"/api/documents/{doc_id('Backend Onboarding Guide')}", headers=login("priya")).json()
    ordinals = [p["ordinal"] for p in detail["passages"]]
    assert ordinals == list(range(len(ordinals))) and len(ordinals) >= 2
    locked = client.get(f"/api/documents/{doc_id('Salary Bands 2026')}", headers=login("aarav")).json()
    assert locked["passages"] == []


# ---------------------------------------------------------------- notifications


def test_access_requests_notify_approvers_and_then_the_requester(client, login, doc_id):
    bands = doc_id("Salary Bands 2026")
    r = client.post(
        "/api/access-requests",
        headers=login("aarav"),
        json={"document_id": bands, "reason": "Preparing for my conversion conversation."},
    )
    assert r.status_code == 201
    vikram = client.get("/api/notifications", headers=login("vikram")).json()  # HR director owns it
    assert any("Aarav Mehta asked for access" in n["title"] for n in vikram["items"])
    assert all("Aarav" not in n["title"] for n in client.get("/api/notifications", headers=login("rohan")).json()["items"])

    client.post(f"/api/access-requests/{r.json()['id']}/approve", headers=login("vikram"), json={"duration_days": 3})
    aarav = client.get("/api/notifications", headers=login("aarav")).json()
    assert aarav["items"][0]["kind"] == "access.approved" and "for 3 days" in aarav["items"][0]["title"]

    marked = client.post("/api/notifications/read", headers=login("aarav"), json={}).json()
    assert marked["unread"] == 0


def test_notifications_are_private_and_deduplicated(client, login):
    from app.notify import notify

    with SessionLocal() as db:
        karan = _user(db, "karan").id
        notify(db, [karan], kind="test", title="Hello", dedupe_key="once")
        notify(db, [karan], kind="test", title="Hello again", dedupe_key="once")
        db.commit()
        mine = db.scalars(select(Notification).where(Notification.user_id == karan, Notification.dedupe_key == "once"))
        assert len(mine.all()) == 1
    assert client.get("/api/notifications", headers=login("neha")).json()["items"] == []
    before = client.get("/api/notifications", headers=login("karan")).json()
    other_id = before["items"][0]["id"]
    client.post("/api/notifications/read", headers=login("neha"), json={"ids": [other_id]})  # not hers: no effect
    assert client.get("/api/notifications", headers=login("karan")).json()["unread"] == before["unread"]


# ---------------------------------------------------------------- stored files and transactions


def test_a_file_is_only_deleted_once_the_deletion_is_committed():
    with SessionLocal() as db:
        team = _team(db, "Engineering").id
        doc = ingest_document(
            db,
            uploader=None,
            filename="keep.txt",
            data=b"A file that must survive a failed delete.",
            mime_type="text/plain",
            title="Keep me",
            owner_team_id=team,
            classification=Classification.INTERNAL,
        ).document
        db.commit()
        key = doc.storage_key
        remove_document(db, doc)
        db.rollback()  # the delete never happened...
        assert storage.get(key)  # ...so the file is still there
        doc = db.get(Document, doc.id)
        remove_document(db, doc)
        db.commit()
    try:
        storage.get(key)
        raise AssertionError("file should be gone")
    except FileNotFoundError:
        pass


def test_views_are_audited_for_change_notifications(client, login, doc_id):
    client.get(f"/api/documents/{doc_id('Code of Conduct')}", headers=login("aarav"))
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "document.view").order_by(AuditEvent.id.desc()))
