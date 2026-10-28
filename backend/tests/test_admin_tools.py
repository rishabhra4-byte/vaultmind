"""Admin tools: people (including CSV import), teams, groups and insights."""

import io

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditEvent, Group, GroupMember, User


def _team_id(client, headers, name: str) -> int:
    return next(t["id"] for t in client.get("/api/teams", headers=headers).json() if t["name"] == name)


def test_admins_add_people_who_sign_in_with_their_company_account_or_a_password(client, login):
    dev = login("dev")
    sales = _team_id(client, dev, "Sales")
    person = {"email": "Zara@northwind.example", "full_name": "Zara Ali", "title": "Account Executive", "team_id": sales}
    assert client.post("/api/admin/users", headers=login("rohan"), json=person).status_code == 403
    r = client.post("/api/admin/users", headers=dev, json=person)
    assert r.status_code == 201, r.text
    zara = r.json()
    assert zara["email"] == "zara@northwind.example" and not zara["has_password"] and zara["onboarding"]
    assert client.post("/api/admin/users", headers=dev, json=person).status_code == 409
    assert (
        client.post("/api/admin/users", headers=dev, json={**person, "email": "x@y.example", "team_id": 999}).status_code == 422
    )
    assert client.post("/api/auth/login", json={"email": "zara@northwind.example", "password": "!"}).status_code == 401

    # A password set by an admin works, and isn't written to the audit log.
    r = client.patch(f"/api/admin/users/{zara['id']}", headers=dev, json={"password": "a-long-password", "onboarding": False})
    assert r.json()["has_password"] and not r.json()["onboarding"]
    assert (
        client.post("/api/auth/login", json={"email": "zara@northwind.example", "password": "a-long-password"}).status_code == 200
    )
    with SessionLocal() as db:
        event = db.scalars(select(AuditEvent).where(AuditEvent.action == "user.update")).all()[-1]
        assert "a-long-password" not in str(event.detail) and event.detail["after"]["password"] == "reset"


CSV = """email,name,title,team,position,admin
zara@northwind.example,Zara Ali,Account Executive,Sales,Associate,no
priya@northwind.example,Priya Sharma,Staff Backend Engineer,Engineering,Senior,
omar@northwind.example,Omar Khan,Designer,Design,2,no
"""


def _import(client, headers, text: str, **form):
    files = {"file": ("people.csv", io.BytesIO(text.encode()), "text/csv")}
    return client.post("/api/admin/users/import", headers=headers, files=files, data={k: str(v).lower() for k, v in form.items()})


def test_import_people_from_a_spreadsheet(client, login):
    dev = login("dev")
    preview = _import(client, dev, CSV).json()
    assert preview["dry_run"] and preview["errors"] == 1  # there's no Design team yet
    assert [r["action"] for r in preview["rows"]] == ["create", "update", "error"]
    assert "Design" in preview["rows"][2]["detail"]
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == "zara@northwind.example")) is None  # nothing saved

    assert _import(client, dev, CSV, dry_run=False).status_code == 422  # errors must be fixed first
    done = _import(client, dev, CSV, dry_run=False, create_teams=True).json()
    assert (done["created"], done["updated"], done["errors"], done["new_teams"]) == (2, 1, 0, ["Design"])
    with SessionLocal() as db:
        assert db.scalar(select(User.title).where(User.email == "priya@northwind.example")) == "Staff Backend Engineer"
        omar = db.scalar(select(User).where(User.email == "omar@northwind.example"))
        assert omar.team.name == "Design" and omar.position == 2 and omar.onboarding_until is not None

    again = _import(client, dev, CSV).json()
    assert [r["action"] for r in again["rows"]] == ["unchanged", "unchanged", "unchanged"]
    bad = _import(client, dev, "email,name\nnot-an-email,X\ndev@northwind.example,Dev Patel\n,\n").json()
    assert [r["action"] for r in bad["rows"]] == ["error", "unchanged"]
    assert _import(client, dev, "name\nX\n").status_code == 422
    demote_self = _import(client, dev, "email,admin\ndev@northwind.example,no\n").json()
    assert demote_self["rows"][0]["action"] == "error"


def test_teams(client, login):
    dev = login("dev")
    r = client.post("/api/admin/teams", headers=dev, json={"name": "Design", "description": "Product design"})
    assert r.status_code == 201 and r.json()["members"] == 0
    assert client.post("/api/admin/teams", headers=dev, json={"name": "design"}).status_code == 409
    team = r.json()["id"]
    assert (
        client.patch(f"/api/admin/teams/{team}", headers=dev, json={"name": "Product Design"}).json()["name"] == "Product Design"
    )
    assert client.delete(f"/api/admin/teams/{_team_id(client, dev, 'Sales')}", headers=dev).status_code == 409
    assert client.delete(f"/api/admin/teams/{team}", headers=dev).status_code == 204
    listing = {t["name"]: t for t in client.get("/api/admin/teams", headers=dev).json()}
    assert listing["Engineering"]["members"] >= 4 and listing["Engineering"]["documents"] > 0


def test_groups_give_access_to_documents_shared_with_them(client, login, doc_id):
    dev, rohan, karan = login("dev"), login("rohan"), login("karan")
    with SessionLocal() as db:
        karan_id = db.scalar(select(User.id).where(User.email == "karan@northwind.example"))
    r = client.post("/api/admin/groups", headers=dev, json={"name": "Launch crew", "member_ids": [karan_id]})
    assert r.status_code == 201 and r.json()["members"] == 1
    group = r.json()["id"]
    assert any(g["name"] == "Launch crew" for g in client.get("/api/groups", headers=karan).json())

    guide = doc_id("Backend Onboarding Guide")  # Rohan uploaded it
    assert client.get(f"/api/documents/{guide}", headers=karan).json()["access"]["allowed"] is False
    assert client.patch(f"/api/documents/{guide}", headers=rohan, json={"share_group_ids": [group]}).status_code == 200
    assert client.get(f"/api/documents/{guide}", headers=karan).json()["access"]["allowed"] is True
    client.patch(f"/api/admin/groups/{group}", headers=dev, json={"member_ids": []})
    assert client.get(f"/api/documents/{guide}", headers=karan).json()["access"]["allowed"] is False

    with SessionLocal() as db:
        synced = Group(name="all-staff@northwind.example", provider="google", external_id="all-staff@northwind.example")
        synced.members = [GroupMember(user_id=karan_id)]
        db.add(synced)
        db.commit()
        synced_id = synced.id
    assert client.patch(f"/api/admin/groups/{synced_id}", headers=dev, json={"name": "x"}).status_code == 409
    assert client.delete(f"/api/admin/groups/{synced_id}", headers=dev).status_code == 409
    assert client.delete(f"/api/admin/groups/{group}", headers=dev).status_code == 204


def test_insights_show_what_people_ask_and_what_needs_attention(client, login):
    neha = login("neha")
    for q in ["How many days of leave?", "how many days of leave", "zebra unicorn"]:
        client.post("/api/assistant/chat", headers=neha, json={"message": q})
    assert client.get("/api/admin/insights", headers=neha).status_code == 403
    data = client.get("/api/admin/insights", headers=login("dev")).json()
    assert data["questions"] == 3 and data["unanswered"] == 1
    assert data["top_questions"][0]["count"] == 2
    assert data["unanswered_questions"][0]["question"] == "zebra unicorn"
    assert "neha" not in str(data).lower()  # who asked is never shown
    drive = next(s for s in data["sources"] if s["name"] == "Shared drive (demo)")
    assert drive["status"] == "ok" and drive["documents"] == 6 and not drive["overdue"]
    assert data["ai"]["mode"] == "offline"
