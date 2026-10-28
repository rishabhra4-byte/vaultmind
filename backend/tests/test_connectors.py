"""Connected sources: shared folders, Google Drive and Microsoft 365, synced end to end against fakes."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fakeserver import GDOC, GFOLDER, FakeGoogle, FakeGraph, tiny_pdf
from sqlalchemy import select

from app import sync
from app.config import get_settings
from app.connectors.base import Http
from app.database import SessionLocal
from app.models import Document, Group, Source, Team, User, UserIdentity


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(Http, "_sleep", staticmethod(lambda *a, **k: None))


def _team_id(name: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(Team.id).where(Team.name == name))


def _docs(source_id: int) -> dict[str, Document]:
    with SessionLocal() as db:
        docs = db.scalars(select(Document).where(Document.source_id == source_id)).all()
        for d in docs:
            _ = d.principals, d.owner_team  # load before the session closes
        return {d.title: d for d in docs}


def _titles(client, headers, q="") -> set[str]:
    """Titles of documents this person can open."""
    return {d["title"] for d in client.get(f"/api/documents?q={q}", headers=headers).json() if d["access"]["allowed"]}


# ===================================================================== shared folders


@pytest.fixture
def share(tmp_path, monkeypatch):
    root = tmp_path / "shares"
    (root / "company" / "Engineering" / "Guides").mkdir(parents=True)
    (root / "company" / "Finance").mkdir()
    (root / "company" / "Engineering" / "Guides" / "Deploys.md").write_text("# Deploys\n\nWe deploy on weekdays after review.")
    (root / "company" / "Finance" / "Payouts.txt").write_text("Contractor payouts. Ravi Kumar, PAN ABCDE1234F.")
    (root / "company" / "Welcome.txt").write_text("Welcome to the company drive. Ask IT for help.")
    (root / "company" / "notes.tmp").write_text("scratch")
    (root / "company" / "logo.bin").write_bytes(b"\x00\x01binary")
    (root / "outside.txt").write_text("Secret server file that must never be synced.")
    os.symlink(root / "outside.txt", root / "company" / "sneaky.txt")
    monkeypatch.setattr(get_settings(), "folder_roots", [str(root / "company")])
    return root / "company"


def _create_folder_source(client, login, path, **extra):
    body = {
        "kind": "folder",
        "name": "Company share",
        "config": {
            "path": str(path),
            "rules": [
                {"prefix": "Engineering", "team": "Engineering", "classification": "internal"},
                {"prefix": "Finance", "team": "Finance", "classification": "internal"},
            ],
            "exclude": ["*.tmp"],
        },
        "owner_team_id": _team_id("Human Resources"),
        "classification": "public",
        "enabled": False,  # the tests run syncs themselves
        **extra,
    }
    return client.post("/api/admin/sources", headers=login("dev"), json=body)


def test_a_shared_folder_syncs_with_folder_rules(client, login, share):
    r = _create_folder_source(client, login, share)
    assert r.status_code == 201, r.text
    source_id = r.json()["id"]
    report = sync.run_sync(source_id)
    assert (report.added, report.skipped) == (3, 1), report.errors  # logo.bin isn't readable text
    assert any("logo.bin" in e for e in report.errors)

    docs = _docs(source_id)
    assert set(docs) == {"Deploys", "Payouts", "Welcome"}  # notes.tmp excluded, the symlink out skipped
    assert docs["Deploys"].owner_team.name == "Engineering" and docs["Deploys"].classification == "internal"
    assert docs["Deploys"].source_path == "Engineering/Guides"
    assert docs["Welcome"].classification == "public"
    # The scanner found a PAN, so the rule's "internal" was raised to restricted.
    assert docs["Payouts"].classification == "restricted" and report.raised == 1

    assert "Deploys" in _titles(client, login("priya"))  # Engineering
    assert "Deploys" not in _titles(client, login("karan"))  # Sales
    assert "Welcome" in _titles(client, login("karan"))  # public
    assert "Payouts" not in _titles(client, login("ananya"))  # restricted: not even Finance
    status = client.get(f"/api/admin/sources/{source_id}", headers=login("dev")).json()
    assert status["status"] == "ok" and status["document_count"] == 3 and status["stats"]["added"] == 3


def test_folder_changes_renames_and_deletions_follow_on_the_next_sync(client, login, share):
    source_id = _create_folder_source(client, login, share).json()["id"]
    sync.run_sync(source_id)
    deploys = _docs(source_id)["Deploys"]
    client.get(f"/api/documents/{deploys.id}", headers=login("priya"))  # Priya reads it

    guide = share / "Engineering" / "Guides" / "Deploys.md"
    guide.write_text("# Deploys\n\nWe deploy on weekdays after review.\n\nNo deploys after 4pm.")
    os.utime(guide, (guide.stat().st_atime, guide.stat().st_mtime + 10))
    (share / "Welcome.txt").rename(share / "Welcome to Northwind.txt")
    (share / "Finance" / "Payouts.txt").unlink()

    report = sync.run_sync(source_id)
    assert (report.updated, report.removed, report.added) == (1, 2, 1)  # a rename is a remove + add here
    docs = _docs(source_id)
    assert docs["Deploys"].version == 2 and "Payouts" not in docs and "Welcome to Northwind" in docs
    notes = client.get("/api/notifications", headers=login("priya")).json()["items"]
    assert any(n["kind"] == "document.changed" and "Deploys" in n["title"] for n in notes)

    unchanged = sync.run_sync(source_id)
    assert (unchanged.unchanged, unchanged.updated, unchanged.added) == (2, 0, 0)  # nothing re-downloaded


def test_folders_outside_the_allowed_locations_are_refused(client, login, share):
    assert _create_folder_source(client, login, share.parent).status_code == 422  # the parent of the root
    assert _create_folder_source(client, login, "/etc").status_code == 422
    r = _create_folder_source(client, login, share, permission_mode="mirror")
    assert r.status_code == 422  # folders can't mirror sharing


def test_rules_naming_unknown_teams_are_refused(client, login, share):
    r = client.post(
        "/api/admin/sources",
        headers=login("dev"),
        json={
            "kind": "folder",
            "name": "Bad rules",
            "config": {"path": str(share), "rules": [{"prefix": "X", "team": "Nonexistent"}]},
            "owner_team_id": _team_id("Engineering"),
        },
    )
    assert r.status_code == 422 and "Nonexistent" in r.json()["detail"]


def test_only_admins_manage_sources(client, login, share):
    assert client.get("/api/admin/sources", headers=login("rohan")).status_code == 403
    assert _create_folder_source(client, login, share).status_code == 201
    listing = client.get("/api/admin/sources", headers=login("dev")).json()
    assert listing["available"]["folder"] is True and listing["available"]["google_drive"] is False


def test_disconnecting_a_source_removes_its_files(client, login, share):
    source_id = _create_folder_source(client, login, share).json()["id"]
    sync.run_sync(source_id)
    assert client.delete(f"/api/admin/sources/{source_id}", headers=login("dev")).status_code == 204
    assert _docs(source_id) == {}
    assert "Deploys" not in _titles(client, login("priya"))


def test_the_demo_drive_is_synced_with_rules(client, login):
    titles = _titles(client, login("priya"))
    assert {"On-call Rotation", "Release Checklist", "Office Guide"} <= titles
    assert "Q4 Territory Plan" not in titles  # Sales folder
    assert "Q4 Territory Plan" in _titles(client, login("karan"))


# ===================================================================== Google Drive


@pytest.fixture
def google(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fake = FakeGoogle(key.public_key(), "vaultmind@project.iam.example", "it-admin@northwind.example")
    s = get_settings()
    sa = {"client_email": fake.sa_email, "private_key": pem.decode(), "private_key_id": "k1"}
    monkeypatch.setattr(s, "google_service_account_json", json.dumps(sa))
    monkeypatch.setattr(s, "google_admin_email", fake.admin_email)
    monkeypatch.setattr(s, "google_api_base", fake.url)
    monkeypatch.setattr(s, "google_admin_api_base", fake.url)
    monkeypatch.setattr(s, "google_token_url", fake.url + "/token")
    yield fake
    fake.server.close()


def _drive(fake: FakeGoogle):
    fake.add("folderRoot01", "Launch", mime=GFOLDER)
    fake.add("folderSub001", "Plans", parent="folderRoot01", mime=GFOLDER)
    fake.add(
        "docLaunch01",
        "Launch Plan",
        parent="folderSub001",
        mime=GDOC,
        content=b"The payments launch is on 3 March. Pilot customers get early access.",
        perms=[
            {"type": "user", "role": "writer", "emailAddress": "Karan@northwind.example"},
            {"type": "group", "role": "reader", "emailAddress": "launch-crew@northwind.example", "displayName": "Launch crew"},
        ],
    )
    fake.add(
        "pdfPricing01",
        "Pricing Sheet.pdf",
        parent="folderRoot01",
        mime="application/pdf",
        content=tiny_pdf("Enterprise pricing starts at 40 lakh a year"),
        perms=[{"type": "domain", "role": "reader", "domain": "northwind.example"}],
    )
    fake.add(
        "txtFaq00001",
        "FAQ.txt",
        parent="folderRoot01",
        content=b"Launch FAQ: ask in #launch.",
        perms=[{"type": "anyone", "role": "reader"}],
    )
    fake.add("txtBoard001", "Board Brief.txt", parent="folderRoot01", content=b"Board brief for the launch.", perms=[])
    fake.add("formSurvey01", "Survey", parent="folderRoot01", mime="application/vnd.google-apps.form")
    fake.users = [
        {"id": "g1", "primaryEmail": "karan@northwind.example", "name": {"fullName": "Karan Singh"}, "organizations": []},
        {
            "id": "g2",
            "primaryEmail": "zoya@northwind.example",
            "name": {"fullName": "Zoya Khan"},
            "organizations": [{"primary": True, "title": "Senior Product Manager", "department": "Product"}],
        },
        {"id": "g3", "primaryEmail": "old@northwind.example", "name": {"fullName": "Gone"}, "suspended": True},
    ]
    fake.groups = {
        "launch-crew@northwind.example": {"name": "Launch crew", "members": ["neha@northwind.example", "zoya@northwind.example"]}
    }


def _google_source(client, login, **extra) -> int:
    r = client.post(
        "/api/admin/sources",
        headers=login("dev"),
        json={
            "kind": "google_drive",
            "name": "Launch drive",
            "config": {"folders": ["https://drive.google.com/drive/folders/folderRoot01"]},
            "owner_team_id": _team_id("Sales"),
            "permission_mode": "mirror",
            "sync_directory": True,
            "enabled": False,
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_google_drive_files_keep_their_sharing(client, login, google):
    _drive(google)
    source_id = _google_source(client, login)
    report = sync.run_sync(source_id)
    assert report.added == 4 and report.skipped == 1, report.errors  # the Google Form can't be read
    assert report.directory == {"created": 1, "updated": 0, "deactivated": 0, "groups": 1}
    assert "drive.readonly" in " ".join(google.scopes) and "admin.directory.group.member.readonly" in " ".join(google.scopes)

    karan, neha, rohan, priya = login("karan"), login("neha"), login("rohan"), login("priya")
    assert {"Launch Plan", "Pricing Sheet", "FAQ"} <= _titles(client, karan)  # direct share, domain, anyone
    assert "Launch Plan" in _titles(client, neha)  # through the Launch crew group
    assert "Launch Plan" not in _titles(client, rohan) and "Pricing Sheet" in _titles(client, rohan)
    assert "Board Brief" not in _titles(client, priya)  # shared with nobody
    assert "Board Brief" in _titles(client, login("dev"))  # admins see everything

    plan = next(d for d in client.get("/api/documents", headers=karan).json() if d["title"] == "Launch Plan")
    assert plan["source"]["label"] == "Google Drive" and plan["source_path"] == "Launch/Plans"
    assert plan["external_url"] == "https://drive.example/docLaunch01"
    detail = client.get(f"/api/documents/{plan['id']}", headers=karan).json()
    assert "3 March" in detail["preview"]
    pricing = client.get(f"/api/documents/{_docs(source_id)['Pricing Sheet'].id}", headers=karan).json()
    assert "40 lakh" in pricing["preview"]  # read from the PDF

    with SessionLocal() as db:
        zoya = db.scalar(select(User).where(User.email == "zoya@northwind.example"))
        assert zoya.team.name == "Product" and zoya.position == 3 and zoya.auth_provider == "google"
        assert zoya.directory_managed and zoya.onboarding_until is None  # the first import isn't "new joiners"
        assert db.scalar(select(User).where(User.email == "old@northwind.example")) is None  # suspended
        crew = db.scalar(select(Group).where(Group.external_id == "launch-crew@northwind.example"))
        assert {m.user.email for m in crew.members} == {"neha@northwind.example", "zoya@northwind.example"}


def test_google_sharing_changes_and_removals_follow_on_the_next_sync(client, login, google):
    _drive(google)
    source_id = _google_source(client, login)
    sync.run_sync(source_id)
    google.files["docLaunch01"]["permissions"] = [{"type": "user", "role": "reader", "emailAddress": "rohan@northwind.example"}]
    google.files["txtFaq00001"]["trashed"] = True
    google.groups["launch-crew@northwind.example"]["members"] = ["zoya@northwind.example"]
    google.users[1]["suspended"] = True  # Zoya leaves

    report = sync.run_sync(source_id)
    assert report.sharing_changed == 1 and report.removed == 1 and report.unchanged == 3
    assert report.directory["deactivated"] == 1
    assert "Launch Plan" in _titles(client, login("rohan"))
    assert "Launch Plan" not in _titles(client, login("karan")) and "Launch Plan" not in _titles(client, login("neha"))
    assert "FAQ" not in _titles(client, login("dev"))


def test_google_problems_are_reported_not_crashed(client, login, google):
    _drive(google)
    source_id = _google_source(client, login)
    google.fail_token = True
    assert sync.run_sync(source_id) is not None
    status = client.get(f"/api/admin/sources/{source_id}", headers=login("dev")).json()
    assert status["status"] == "error" and "domain-wide delegation" in status["last_error"]
    notes = client.get("/api/notifications", headers=login("dev")).json()["items"]
    assert any(n["kind"] == "source.error" for n in notes)
    # Nothing was removed because listing failed.
    google.fail_token = False
    sync.run_sync(source_id)
    assert len(_docs(source_id)) == 4


def test_google_needs_server_credentials(client, login):
    r = client.post(
        "/api/admin/sources",
        headers=login("dev"),
        json={"kind": "google_drive", "name": "X", "config": {"folders": ["abc1234567"]}, "owner_team_id": 1},
    )
    assert r.status_code == 422 and "isn't set up" in r.json()["detail"]


# ===================================================================== Microsoft 365


@pytest.fixture
def graph(monkeypatch):
    fake = FakeGraph("contoso-tenant", "app-123", "s3cret")
    s = get_settings()
    monkeypatch.setattr(s, "microsoft_tenant_id", fake.tenant)
    monkeypatch.setattr(s, "microsoft_client_id", fake.client_id)
    monkeypatch.setattr(s, "microsoft_client_secret", fake.secret)
    monkeypatch.setattr(s, "microsoft_graph_base", fake.graph)
    monkeypatch.setattr(s, "microsoft_login_base", fake.url)
    yield fake
    fake.server.close()


def _library(fake: FakeGraph):
    fake.add("f-pol", "Policies")
    fake.add(
        "i-leave",
        "Leave Policy.docx.txt",
        parent="f-pol",
        content=b"Parental leave is 26 weeks for birth parents.",
        perms=[{"id": "1", "roles": ["read"], "grantedToV2": {"user": {"id": "aad-neha", "displayName": "Neha Kapoor"}}}],
    )
    fake.add(
        "i-deck",
        "Benefits Deck.pptx",
        parent="f-pol",
        content=b"Health insurance covers parents from January",
        perms=[
            {"id": "2", "roles": ["read"], "link": {"scope": "organization", "type": "view"}},
            {"id": "3", "roles": ["read"], "grantedToV2": {"siteGroup": {"id": "5", "displayName": "HR Members"}}},
        ],
    )
    fake.add(
        "i-pay",
        "Payroll Calendar.txt",
        parent="f-pol",
        content=b"Payroll runs on the 28th.",
        perms=[{"id": "4", "roles": ["read"], "grantedToV2": {"group": {"id": "grp-fin", "displayName": "Finance team"}}}],
    )
    fake.add("i-zip", "Archive.zip", parent="f-pol", content=b"PK")
    fake.users = [
        {
            "id": "aad-neha",
            "displayName": "Neha Kapoor",
            "mail": "neha@northwind.example",
            "jobTitle": "HR Associate",
            "accountEnabled": True,
        },
        {"id": "aad-ana", "displayName": "Ananya Gupta", "mail": "ananya@northwind.example", "accountEnabled": True},
        {"id": "aad-guest", "displayName": "Guest", "userPrincipalName": "guest_x#EXT#@contoso.example", "accountEnabled": True},
    ]
    fake.groups = {"grp-fin": {"displayName": "Finance team", "members": ["aad-ana"]}}


def _graph_source(client, login, **config) -> int:
    r = client.post(
        "/api/admin/sources",
        headers=login("dev"),
        json={
            "kind": "microsoft_365",
            "name": "HR site",
            "config": {"site_url": "https://contoso.sharepoint.example/sites/hr", "folder_path": "Policies", **config},
            "owner_team_id": _team_id("Human Resources"),
            "permission_mode": "mirror",
            "sync_directory": True,
            "enabled": False,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_microsoft_365_files_keep_their_sharing(client, login, graph):
    _library(graph)
    graph.throttle_next = 2  # Graph says "slow down" twice; the connector waits and retries
    source_id = _graph_source(client, login)
    report = sync.run_sync(source_id)
    assert report.added == 3 and report.skipped == 1, report.errors  # the zip isn't readable
    assert report.unresolved == 1  # the SharePoint site group isn't a person or directory group
    assert report.directory["groups"] == 1

    neha, ananya, karan = login("neha"), login("ananya"), login("karan")
    assert "Leave Policy.docx" in _titles(client, neha)  # matched through her directory id
    assert "Leave Policy.docx" not in _titles(client, ananya)
    assert "Payroll Calendar" in _titles(client, ananya)  # via the Finance group
    assert "Payroll Calendar" not in _titles(client, neha)
    assert "Benefits Deck.pptx" in _titles(client, karan)  # an organisation-wide link
    deck = _docs(source_id)["Benefits Deck.pptx"]
    assert "Health insurance" in client.get(f"/api/documents/{deck.id}", headers=karan).json()["preview"]  # via PDF
    with SessionLocal() as db:
        assert db.scalar(select(UserIdentity.user_id).where(UserIdentity.subject == "aad-neha")) is not None
        assert db.scalar(select(User).where(User.email.like("%ext%"))) is None  # guests aren't imported


def test_microsoft_library_must_exist(client, login, graph):
    _library(graph)
    source_id = _graph_source(client, login, library="Missing")
    sync.run_sync(source_id)
    status = client.get(f"/api/admin/sources/{source_id}", headers=login("dev")).json()
    assert status["status"] == "error" and "No library called" in status["last_error"]


# ===================================================================== scheduling


def test_sources_are_due_after_their_interval():
    with SessionLocal() as db:
        team = db.scalar(select(Team.id))
        fresh = Source(kind="folder", name="a", config={}, owner_team_id=team, sync_interval_minutes=60, stats={})
        recent = Source(
            kind="folder",
            name="b",
            config={},
            owner_team_id=team,
            sync_interval_minutes=60,
            stats={},
            last_synced_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        stale = Source(
            kind="folder",
            name="c",
            config={},
            owner_team_id=team,
            sync_interval_minutes=60,
            stats={},
            last_synced_at=datetime.now(UTC) - timedelta(minutes=61),
        )
        off = Source(kind="folder", name="d", config={}, owner_team_id=team, enabled=False, stats={})
        db.add_all([fresh, recent, stale, off])
        db.commit()
        due = set(sync.due_sources(db))
        assert fresh.id in due and stale.id in due and recent.id not in due and off.id not in due


def test_a_sync_already_running_is_not_started_twice(client, login, share):
    source_id = _create_folder_source(client, login, share).json()["id"]
    lock = sync._lock(source_id)
    lock.acquire()
    try:
        assert sync.run_sync(source_id) is None
        assert client.post(f"/api/admin/sources/{source_id}/sync", headers=login("dev")).status_code == 409
    finally:
        lock.release()
    assert client.post(f"/api/admin/sources/{source_id}/sync", headers=login("dev")).status_code == 202


def test_position_from_title():
    from app.models import Position

    cases = {
        "Chief Financial Officer": Position.EXECUTIVE,
        "VP Engineering": Position.EXECUTIVE,
        "Director of Sales": Position.DIRECTOR,
        "Engineering Manager": Position.MANAGER,
        "Senior Product Manager": Position.SENIOR,  # usually not a people manager
        "Account Manager": Position.ASSOCIATE,
        "Senior Designer": Position.SENIOR,
        "Summer Intern": Position.INTERN,
        "Accountant": Position.ASSOCIATE,
    }
    assert {t: sync.position_from_title(t) for t in cases} == cases


def test_folder_paths_cannot_escape(tmp_path, monkeypatch):
    from app.connectors.base import ConnectorError
    from app.connectors.folder import resolve_folder

    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    monkeypatch.setattr(get_settings(), "folder_roots", [str(root)])
    assert resolve_folder(str(root / "a")) == (root / "a").resolve()
    for bad in [str(root / "a" / ".." / ".."), str(Path("/")), "", "x\x00y"]:
        with pytest.raises(ConnectorError):
            resolve_folder(bad)
