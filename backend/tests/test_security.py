"""Regression tests for the production-readiness audit (security and data integrity)."""

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app import storage
from app.config import ConfigError, Settings
from app.database import SessionLocal
from app.models import AccessRequest, Document, RequestStatus, User


def _locked_titles(client, headers, q):
    docs = client.get("/api/documents", params={"q": q}, headers=headers).json()
    return [d["title"] for d in docs if not d["access"]["allowed"]]


# ---------------------------------------------------------------- information leaks


def test_search_cannot_probe_the_contents_of_locked_documents(client, login):
    intern = login("aarav")
    # "34" and "crore" only appear inside documents the intern can't open.
    assert _locked_titles(client, intern, "34") == []
    assert _locked_titles(client, intern, "crore") == []
    # Locked documents are still discoverable by their title.
    assert "Salary Bands 2026" in _locked_titles(client, intern, "salary bands")


def test_assistant_locked_hints_match_titles_not_contents(client, login, fake_llm):
    intern = login("aarav")
    r = client.post("/api/assistant/chat", headers=intern, json={"message": "Is anything paid 22 to 34 lakh per annum?"}).json()
    assert all(lock["title"] != "Salary Bands 2026" for lock in r["reply"]["meta"]["locked"])
    r = client.post("/api/assistant/chat", headers=intern, json={"message": "Where are the salary bands?"}).json()
    assert any(lock["title"] == "Salary Bands 2026" for lock in r["reply"]["meta"]["locked"])


def test_locked_documents_hide_content_derived_metadata(client, login, doc_id):
    detail = client.get(f"/api/documents/{doc_id('Salary Bands 2026')}", headers=login("aarav")).json()
    assert detail["access"]["allowed"] is False
    assert detail["tags"] == [] and detail["sensitivity_flags"] == [] and detail["summary"] == ""


def test_team_list_needs_sign_in(client, login):
    assert client.get("/api/teams").status_code == 401
    assert client.get("/api/meta").json()["teams"] == []
    assert len(client.get("/api/meta", headers=login("neha")).json()["teams"]) == 5


# ---------------------------------------------------------------- authentication


def test_login_is_rate_limited_per_account(client):
    codes = [
        client.post("/api/auth/login", json={"email": "dev@northwind.example", "password": f"guess{i}"}).status_code
        for i in range(11)
    ]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429
    # The correct password is refused too while the account is cooling down.
    r = client.post("/api/auth/login", json={"email": "dev@northwind.example", "password": "demo1234"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_successful_sign_ins_do_not_use_up_the_limit(client):
    # e.g. an office where everyone signs in from one address
    for _ in range(55):  # more than both limits
        assert client.post("/api/auth/login", json={"email": "neha@northwind.example", "password": "demo1234"}).status_code == 200


def test_login_is_rate_limited_per_address(client):
    codes = [
        client.post("/api/auth/login", json={"email": f"guess{i}@northwind.example", "password": "x"}).status_code
        for i in range(51)
    ]
    assert codes[:50] == [401] * 50 and codes[50] == 429
    r = client.post("/api/auth/login", json={"email": "neha@northwind.example", "password": "demo1234"})
    assert r.status_code == 429


def test_login_gives_the_same_answer_for_unknown_accounts(client):
    unknown = client.post("/api/auth/login", json={"email": "nobody@northwind.example", "password": "x"})
    wrong = client.post("/api/auth/login", json={"email": "neha@northwind.example", "password": "x"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_login_rejects_oversized_input(client):
    r = client.post("/api/auth/login", json={"email": "a@b.c", "password": "x" * 10_000})
    assert r.status_code == 422


def test_tampered_token_is_rejected(client, login):
    token = login("neha")["Authorization"]
    assert client.get("/api/auth/me", headers={"Authorization": token[:-2] + "xx"}).status_code == 401


# ---------------------------------------------------------------- configuration


def test_production_refuses_a_missing_or_weak_secret():
    for secret in ["", "change-me", "dev-only-secret-change-me-in-production-0000", "short"]:
        with pytest.raises(ConfigError):
            Settings(env="production", jwt_secret=secret, _env_file=None).validate_for_startup()
    Settings(env="production", jwt_secret="x" * 40, _env_file=None).validate_for_startup()


def test_development_without_a_secret_gets_a_random_one():
    a = Settings(env="development", jwt_secret="", _env_file=None)
    b = Settings(env="development", jwt_secret="", _env_file=None)
    a.validate_for_startup()
    b.validate_for_startup()
    assert len(a.jwt_secret) >= 32 and a.jwt_secret != b.jwt_secret


def test_demo_data_is_off_in_production_unless_asked_for():
    assert Settings(env="production", seed_demo_data=None, _env_file=None).demo_enabled is False
    assert Settings(env="production", seed_demo_data=True, _env_file=None).demo_enabled is True
    assert Settings(env="development", seed_demo_data=None, _env_file=None).demo_enabled is True


# ---------------------------------------------------------------- HTTP hardening


def test_unknown_api_routes_are_json_404s(client):
    for method in ("get", "post", "delete"):
        r = getattr(client, method)("/api/no-such-thing")
        assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_security_headers_on_api_responses(client):
    h = client.get("/api/health").headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert h["cache-control"] == "no-store"


def test_frontend_gets_a_strict_csp_and_long_lived_asset_caching(tmp_path: Path):
    from app.main import CSP, mount_frontend, security_headers

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>t</title>")
    (tmp_path / "assets" / "app-123.js").write_text("console.log(1)")
    app = FastAPI()
    app.middleware("http")(security_headers)
    mount_frontend(app, tmp_path)
    with TestClient(app) as c:
        page = c.get("/library/5")
        assert page.status_code == 200 and "<title>" in page.text
        assert page.headers["content-security-policy"] == CSP
        assert "'unsafe-inline'" not in CSP and "script-src 'self'" in CSP
        assert page.headers["cache-control"] == "no-cache"
        asset = c.get("/assets/app-123.js")
        assert "immutable" in asset.headers["cache-control"]
        assert c.get("/../../etc/passwd").text.startswith("<!doctype html>")


def test_request_bodies_over_the_limit_are_refused_before_parsing(client, login):
    r = client.post(
        "/api/documents",
        headers={**login("rohan"), "Content-Length": str(500 * 1024 * 1024), "Content-Type": "multipart/form-data; boundary=x"},
        content=b"--x--",
    )
    assert r.status_code == 413


def test_readiness_checks_the_database(client):
    assert client.get("/api/ready").json() == {"status": "ok", "database": "ok"}


# ---------------------------------------------------------------- uploads and downloads


def _upload(client, headers, name, data, content_type="text/plain", **form):
    form = {"classification": "internal", **form}
    return client.post("/api/documents", headers=headers, files={"file": (name, io.BytesIO(data), content_type)}, data=form)


def test_hostile_file_names_are_sanitised(client, login):
    rohan = login("rohan")
    r = _upload(client, rohan, '../../evil"\r\nX-Injected: yes.txt', b"Quarterly offsite notes for the team.")
    assert r.status_code == 201, r.text
    doc = r.json()["document"]
    assert "/" not in doc["filename"] and "\r" not in doc["filename"] and '"' not in doc["filename"]
    dl = client.get(f"/api/documents/{doc['id']}/download", headers=rohan)
    assert "x-injected" not in {k.lower() for k in dl.headers}
    assert dl.headers["content-disposition"].startswith("attachment;")
    assert dl.headers["content-type"].startswith("text/plain")


def test_files_named_in_any_language_download(client, login):
    rohan = login("rohan")
    r = _upload(client, rohan, "टीम योजना.txt", b"Plans for the team offsite in December.")
    assert r.status_code == 201, r.text
    dl = client.get(f"/api/documents/{r.json()['document']['id']}/download", headers=rohan)
    assert dl.status_code == 200  # a header built from the raw name used to fail outside Latin-1
    assert "filename*=UTF-8''" in dl.headers["content-disposition"]


def test_downloads_never_use_the_uploaders_content_type(client, login):
    rohan = login("rohan")
    r = _upload(client, rohan, "page.html", b"<script>alert(1)</script><p>Team lunch on Friday.</p>", content_type="text/html")
    assert r.status_code == 201, r.text
    dl = client.get(f"/api/documents/{r.json()['document']['id']}/download", headers=rohan)
    assert dl.headers["content-type"] == "application/octet-stream"
    assert dl.headers["x-content-type-options"] == "nosniff"


def test_docx_zip_bomb_is_rejected(client, login):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"<w:p><w:t>" + b"A" * (31 * 1024 * 1024) + b"</w:t></w:p>")
    assert len(buf.getvalue()) < 200_000  # tiny on the wire, huge when inflated
    r = _upload(client, login("rohan"), "bomb.docx", buf.getvalue())
    assert r.status_code == 415 and "too large" in r.json()["detail"]


def test_password_protected_pdf_gets_a_clear_message(client, login):
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.encrypt("secret")
    buf = io.BytesIO()
    w.write(buf)
    r = _upload(client, login("rohan"), "locked.pdf", buf.getvalue(), content_type="application/pdf")
    assert r.status_code == 415 and "password-protected" in r.json()["detail"]


def test_damaged_pdf_does_not_leak_parser_internals(client, login):
    r = _upload(client, login("rohan"), "broken.pdf", b"%PDF-1.7 this is not really a pdf", content_type="application/pdf")
    assert r.status_code in (415, 422)
    assert "Traceback" not in r.text and "pypdf" not in r.text.lower()


def test_deleting_the_last_copy_removes_the_stored_file(client, login):
    rohan = login("rohan")
    r = _upload(client, rohan, "temp.txt", b"Temporary note about the hackathon schedule.")
    doc_id = r.json()["document"]["id"]
    with SessionLocal() as db:
        key = db.get(Document, doc_id).storage_key
    assert storage.get(key)
    assert client.delete(f"/api/documents/{doc_id}", headers=rohan).status_code == 204
    with pytest.raises(FileNotFoundError):
        storage.get(key)


def test_shared_blob_survives_deleting_one_of_two_documents(client, login):
    # Two teams who can't see each other's internal files upload the same bytes: one blob.
    data = b"Shared runbook text used by two teams."
    rohan, ananya = login("rohan"), login("ananya")
    a = _upload(client, rohan, "a.txt", data).json()["document"]["id"]
    b = _upload(client, ananya, "b.txt", data).json()["document"]["id"]
    with SessionLocal() as db:
        key = db.get(Document, a).storage_key
        assert db.get(Document, b).storage_key == key
    assert client.delete(f"/api/documents/{a}", headers=rohan).status_code == 204
    assert storage.get(key) == data


# ---------------------------------------------------------------- data integrity


def test_database_allows_only_one_pending_request_per_person_and_document(doc_id):
    runbook = doc_id("Incident Response Runbook")
    with SessionLocal() as db:
        aarav = db.scalar(select(User).where(User.email == "aarav@northwind.example"))
        db.add(AccessRequest(document_id=runbook, requester_id=aarav.id, reason="first request", duration_days=3))
        db.commit()
        db.add(AccessRequest(document_id=runbook, requester_id=aarav.id, reason="second request", duration_days=3))
        with pytest.raises(IntegrityError):
            db.commit()


def test_two_approvers_cannot_both_decide_the_same_request(client, login, doc_id):
    from app.routers.access import _decide
    from app.schemas import DecisionIn

    runbook = doc_id("Incident Response Runbook")
    req_id = client.post(
        "/api/access-requests", headers=login("aarav"), json={"document_id": runbook, "reason": "Shadowing on-call next week"}
    ).json()["id"]
    with SessionLocal() as stale:  # a second approver who loaded the request before the first decided
        rohan = stale.scalar(select(User).where(User.email == "rohan@northwind.example"))
        assert stale.get(AccessRequest, req_id).status == RequestStatus.PENDING
        assert client.post(f"/api/access-requests/{req_id}/approve", headers=login("rohan"), json={}).status_code == 200
        with pytest.raises(HTTPException) as exc:
            _decide(stale, rohan, req_id, False, DecisionIn())
        assert exc.value.status_code == 409
    grants = client.get("/api/grants", headers=login("aarav")).json()
    assert len([g for g in grants if g["document_id"] == runbook]) == 1


def test_audit_filter_treats_wildcards_literally(client, login):
    admin = login("dev")
    assert client.get("/api/admin/audit", params={"action": "%"}, headers=admin).json() == []
    assert client.get("/api/admin/audit", params={"action": "auth."}, headers=admin).json()


# ---------------------------------------------------------------- migrations


def test_migrations_build_the_same_schema_as_the_models(empty_database):
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.database import Base
    from app.migrations import alembic_config

    engine = empty_database
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff


def test_databases_from_version_1_are_upgraded_in_place(empty_database, monkeypatch):
    """A 1.0 database (made with create_all, so no migration history) keeps its data, and its
    documents get their current text recorded as version 1."""
    from alembic import command

    from app import migrations

    engine = empty_database
    cfg = migrations.alembic_config()
    with engine.begin() as conn:  # the 1.0 schema...
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001")
    with engine.begin() as conn:  # ...without its migration history, as 1.0 made it
        conn.execute(text("DROP TABLE alembic_version"))
        now = datetime.now(UTC)
        conn.execute(text("INSERT INTO teams (id, name, description) VALUES (1, 'Eng', '')"))
        conn.execute(
            text(
                "INSERT INTO users (id, email, full_name, title, password_hash, team_id, position,"
                " is_admin, is_active, created_at) VALUES (1, 'a@x', 'A', '', 'h', 1, 1, false, true, :now)"
            ),
            {"now": now},
        )
        conn.execute(
            text(
                "INSERT INTO documents (id, title, filename, mime_type, size, sha256, storage_key, owner_team_id,"
                " classification, min_position, discoverable, summary, tags, sensitivity_flags, uploaded_by,"
                " created_at, updated_at) VALUES (7, 'Guide', 'g.md', 'text/markdown', 10, 'abc', 'ab/abc', 1,"
                " 'internal', 1, true, '', '[]', '[]', 1, :now, :now)"
            ),
            {"now": now},
        )
        conn.execute(text("INSERT INTO document_chunks (document_id, ordinal, text) VALUES (7, 0, 'First part.')"))
        conn.execute(text("INSERT INTO document_chunks (document_id, ordinal, text) VALUES (7, 1, 'Second part.')"))
    monkeypatch.setattr(migrations, "engine", engine)
    migrations.upgrade_database()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0004"
        assert conn.execute(text("SELECT email FROM users")).scalar() == "a@x"
        row = conn.execute(text("SELECT version, text FROM document_versions WHERE document_id = 7")).one()
        assert row == (1, "First part.\n\nSecond part.")
        assert conn.execute(text("SELECT acl_mode, ai_enabled, version FROM documents")).one() == ("native", 1, 1)
    assert "uq_one_pending_request" in {i["name"] for i in inspect(engine).get_indexes("access_requests")}


def test_create_admin_cli_bootstraps_a_production_install(client, monkeypatch, capsys):
    from app.cli import main

    monkeypatch.setenv("VAULTMIND_ADMIN_PASSWORD", "short")
    assert main(["create-admin", "--email", "ops@acme.example", "--name", "Ops"]) == 1
    assert "at least 12" in capsys.readouterr().err

    monkeypatch.setenv("VAULTMIND_ADMIN_PASSWORD", "a-long-and-random-password")
    assert main(["create-admin", "--email", "Ops@Acme.example", "--name", "Ops Lead", "--team", "IT"]) == 0
    assert main(["create-admin", "--email", "ops@acme.example", "--name", "Again"]) == 1  # duplicates refused

    r = client.post("/api/auth/login", json={"email": "ops@acme.example", "password": "a-long-and-random-password"})
    assert r.status_code == 200 and r.json()["user"]["is_admin"] is True


def test_cli_creates_employees_and_resets_passwords(client, monkeypatch, capsys):
    from app.cli import main

    monkeypatch.setenv("VAULTMIND_PASSWORD", "first-long-password")
    args = ["create-user", "--email", "ravi@acme.example", "--name", "Ravi Kumar", "--team", "Platform"]
    assert main([*args, "--position", "senior", "--title", "Backend Engineer"]) == 0
    assert main([*args, "--position", "senior"]) == 1  # already exists
    with pytest.raises(SystemExit):
        main([*args, "--position", "wizard"])  # not a position
    capsys.readouterr()

    r = client.post("/api/auth/login", json={"email": "ravi@acme.example", "password": "first-long-password"})
    me = r.json()["user"]
    assert r.status_code == 200 and me["position_label"] == "Senior" and me["team_name"] == "Platform"
    assert me["title"] == "Backend Engineer" and me["is_admin"] is False

    monkeypatch.setenv("VAULTMIND_PASSWORD", "second-long-password")
    assert main(["set-password", "--email", "RAVI@acme.example"]) == 0
    assert main(["set-password", "--email", "nobody@acme.example"]) == 1
    old = client.post("/api/auth/login", json={"email": "ravi@acme.example", "password": "first-long-password"})
    new = client.post("/api/auth/login", json={"email": "ravi@acme.example", "password": "second-long-password"})
    assert old.status_code == 401 and new.status_code == 200


def test_deactivating_someone_ends_their_signed_in_session(client, login):
    neha = login("neha")
    assert client.get("/api/documents", headers=neha).status_code == 200
    users = client.get("/api/admin/users", headers=login("dev")).json()
    neha_id = next(u["id"] for u in users if u["email"] == "neha@northwind.example")
    r = client.patch(f"/api/admin/users/{neha_id}", headers=login("dev"), json={"is_active": False})
    assert r.status_code == 200
    assert client.get("/api/documents", headers=neha).status_code == 401  # the same token, refused at once
    again = client.post("/api/auth/login", json={"email": "neha@northwind.example", "password": "demo1234"})
    assert again.status_code == 403


def test_scanning_a_file_before_upload_never_sends_it_to_the_model(client, login, fake_llm):
    body = b"Contractor payouts\n\nRavi Kumar, PAN ABCDE1234F, payout INR 85,000.\n"
    r = client.post("/api/documents/scan", headers=login("rohan"), files={"file": ("payouts.txt", body, "text/plain")})
    assert r.status_code == 200
    assert fake_llm.calls == []  # nothing leaves the server until the person chooses to upload
    assert r.json()["suggested_classification"] == "restricted"
    assert "ABCDE1234F" not in r.json()["summary"]


def test_rate_limiter_forgets_idle_keys():
    from app.deps import RateLimiter

    limiter = RateLimiter(limit=5, window_seconds=0.01)
    for i in range(999):
        limiter.check(f"attacker-{i}@example.com")
    import time

    time.sleep(0.02)
    limiter.check("one-more")  # the 1000th call triggers a sweep of keys idle past the window
    assert len(limiter) <= 1
