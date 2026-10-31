"""The smarter assistant: passage citations, asking about one document, what changed, comparing,
the glossary, AI read controls and redaction, and actions that only run when confirmed."""

import io
import re

from conftest import FakeLLM
from sqlalchemy import select

from app import llm
from app.database import SessionLocal
from app.models import AccessGrant, AccessRequest, AuditEvent, Notification, User

LEAVE = "How many days of earned leave do I get?"


def _ask(client, headers, message="", **extra):
    r = client.post("/api/assistant/chat", headers=headers, json={"message": message, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _uid(key: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == f"{key}@northwind.example"))


def _passage_with(client, headers, doc: int, text: str) -> int:
    detail = client.get(f"/api/documents/{doc}", headers=headers).json()
    return next(p["ordinal"] for p in detail["passages"] if text in p["text"])


# ---------------------------------------------------------------- citations to paragraphs


def test_offline_answers_cite_the_paragraph(client, login, doc_id):
    neha = login("neha")
    handbook = doc_id("Employee Handbook 2026")
    reply = _ask(client, neha, LEAVE)["reply"]
    n = _passage_with(client, neha, handbook, "24 days of earned leave")
    assert f"[D{handbook}.{n}]" in reply["content"]
    card = next(c for c in reply["meta"]["citations"] if c["id"] == handbook)
    assert card["passages"] == [n]


def test_the_model_can_only_cite_passages_it_was_given(client, login, doc_id, fake_llm):
    neha = login("neha")
    handbook, falcon = doc_id("Employee Handbook 2026"), doc_id("Project Falcon: Acquisition Evaluation")
    n = _passage_with(client, neha, handbook, "24 days of earned leave")
    fake_llm.reply = f"It's 24 days [D{handbook}.{n}]. See also [D{handbook}.999] and [D{falcon}.1]."
    reply = _ask(client, neha, LEAVE)["reply"]
    assert f'<passage id="D{handbook}.{n}">' in fake_llm.last_prompt
    assert reply["content"] == f"It's 24 days [D{handbook}.{n}]. See also [D{handbook}] and ."
    assert [(c["id"], c.get("passages")) for c in reply["meta"]["citations"]] == [(handbook, [n])]


# ---------------------------------------------------------------- glossary and better searches


def test_company_words_find_the_right_document(client, login, doc_id):
    reply = _ask(client, login("neha"), "how much pto do I get?")["reply"]
    assert "leave" in reply["meta"]["search"]["expanded"]
    assert doc_id("Employee Handbook 2026") in [c["id"] for c in reply["meta"]["citations"]]


def test_admins_manage_the_glossary(client, login, doc_id):
    dev, neha = login("dev"), login("neha")
    term = {"term": "Spend policy", "synonyms": ["expense", "reimbursement"], "note": "What Finance calls it"}
    assert client.post("/api/admin/glossary", headers=neha, json=term).status_code == 403
    r = client.post("/api/admin/glossary", headers=dev, json=term)
    assert r.status_code == 201, r.text
    assert client.post("/api/admin/glossary", headers=dev, json=term).status_code == 409
    assert client.post("/api/admin/glossary", headers=dev, json={"term": "x", "synonyms": ["X"]}).status_code == 422

    terms = client.get("/api/glossary", headers=neha).json()
    assert any(t["term"] == "Spend policy" and not t["builtin"] for t in terms)
    assert any(t["term"] == "leave" and t["builtin"] for t in terms)

    reply = _ask(client, login("karan"), "what does the spend policy say about hotels?")["reply"]
    assert "reimbursement" in reply["meta"]["search"]["expanded"]
    assert reply["meta"]["citations"][0]["id"] == doc_id("Expense Reimbursement Policy")

    term_id = r.json()["id"]
    assert client.patch(f"/api/admin/glossary/{term_id}", headers=dev, json={"synonyms": ["claims"]}).json()["synonyms"] == [
        "claims"
    ]
    assert client.delete(f"/api/admin/glossary/{term_id}", headers=dev).status_code == 204


class RewritingLLM(FakeLLM):
    def complete(self, system, messages, max_tokens=1024, *, timeout=None):
        self.calls.append({"system": system, "messages": messages})
        return "expense reimbursement hotel" if "keyword search" in system else "Here you go."


def test_weak_searches_are_rephrased_by_the_model_without_sending_documents(client, login):
    fake = RewritingLLM()
    llm.set_llm(fake)
    reply = _ask(client, login("karan"), "zqx where do I put the thing for the place I stayed")["reply"]
    assert reply["meta"]["search"]["rewritten"] == "expense reimbursement hotel"
    rewrite_call = next(c for c in fake.calls if "keyword search" in c["system"])
    assert "<documents>" not in rewrite_call["messages"][0]["content"]
    assert "Expense Reimbursement Policy" in fake.last_prompt  # the answer then uses what the better search found


# ---------------------------------------------------------------- AI read controls and redaction


def test_documents_with_ai_reading_off_never_reach_the_model(client, login, doc_id, fake_llm):
    handbook = doc_id("Employee Handbook 2026")
    r = client.patch(f"/api/documents/{handbook}", headers=login("dev"), json={"ai_enabled": False})
    assert r.status_code == 200 and r.json()["ai_enabled"] is False
    neha = login("neha")
    reply = _ask(client, neha, LEAVE)["reply"]
    assert "24 days of earned leave" not in fake_llm.last_prompt
    assert handbook in [d["id"] for d in reply["meta"]["ai_blocked"]]

    # Asking about that document directly: answered from its passages, without the model.
    calls = len(fake_llm.calls)
    scoped = _ask(client, neha, "How many leave days?", document_id=handbook)["reply"]
    assert len(fake_llm.calls) == calls
    assert scoped["meta"]["mode"] == "offline" and "AI reading is turned off" in scoped["meta"]["notice"]


def test_identity_numbers_and_secrets_are_masked_before_the_model_sees_them(client, login, fake_llm):
    rohan = login("rohan")
    text = (
        "Contractor onboarding record. Aadhaar 2345 6789 0123 belongs to the contractor. "
        "Contact ravi@vendor.example for building access. Deploy key sk-abcdefghijklmnopqrstuv1234."
    )
    files = {"file": ("contractor.txt", io.BytesIO(text.encode()), "text/plain")}
    # Confidential (with the scanner's warning acknowledged): restricted documents never reach
    # the AI service at all by default (see test_ai_policy.py).
    form = {"classification": "confidential", "title": "Contractor record", "acknowledge_sensitivity": "true"}
    up = client.post("/api/documents", headers=rohan, files=files, data=form)
    assert up.status_code == 201, up.text
    _ask(client, rohan, "contractor onboarding record building access")
    prompt = fake_llm.last_prompt
    assert "2345 6789 0123" not in prompt and "sk-abcdefghijklmnopqrstuv1234" not in prompt
    assert "[aadhaar number removed]" in prompt and "[api key or secret token removed]" in prompt
    assert "ravi@vendor.example" in prompt  # contact details stay


# ---------------------------------------------------------------- one document ("open and ask")


def test_ask_about_one_document(client, login, doc_id, fake_llm):
    neha = login("neha")
    handbook, conduct = doc_id("Employee Handbook 2026"), doc_id("Code of Conduct")
    llm.set_llm(None)
    out = _ask(client, neha, document_id=handbook, intent="summary")
    convo, reply = out["conversation"], out["reply"]
    assert out["user_message"]["content"] == "Summarise this document"
    assert convo["document_id"] == handbook and convo["title"] == "About “Employee Handbook 2026”"
    assert reply["content"].startswith(f"**Employee Handbook 2026** [D{handbook}] in short:")
    assert reply["meta"]["intent"] == "summary" and re.search(rf"\[D{handbook}\.\d+\]", reply["content"])

    # Follow-ups stay about that document, and the model only gets its passages.
    llm.set_llm(fake_llm)
    _ask(client, neha, "and sick leave?", conversation_id=convo["id"])
    ids = set(re.findall(r'<doc id="D(\d+)"', fake_llm.last_prompt))
    assert ids == {str(handbook)}
    assert "is reading “Employee Handbook 2026”" in fake_llm.calls[-1]["system"]
    assert [c["id"] for c in client.get(f"/api/assistant/conversations?document_id={handbook}", headers=neha).json()] == [
        convo["id"]
    ]
    assert client.get(f"/api/assistant/conversations?document_id={conduct}", headers=neha).json() == []
    with SessionLocal() as db:
        event = db.scalars(select(AuditEvent).where(AuditEvent.action == "assistant.query").order_by(AuditEvent.id)).all()[-2]
        assert event.detail["intent"] == "summary" and event.detail["document_id"] == handbook


def test_cant_ask_about_documents_you_cant_open(client, login, doc_id):
    aarav = login("aarav")
    r = client.post("/api/assistant/chat", headers=aarav, json={"message": "summary", "document_id": doc_id("Salary Bands 2026")})
    assert r.status_code == 403
    r = client.post(
        "/api/assistant/chat", headers=aarav, json={"message": "summary", "document_id": doc_id("Board Meeting Notes: Q3 2026")}
    )
    assert r.status_code == 404


def test_what_it_means_for_me(client, login, doc_id):
    reply = _ask(client, login("aarav"), document_id=doc_id("Intern Starter Pack"), intent="for_me")["reply"]
    assert reply["meta"]["intent"] == "for_me" and reply["meta"]["mode"] == "offline"
    assert "mention" in reply["content"] and reply["meta"]["citations"][0]["passages"]


# ---------------------------------------------------------------- what changed and comparing


def test_what_changed_in_a_document(client, login, doc_id):
    expense = doc_id("Expense Reimbursement Policy")
    reply = _ask(client, login("karan"), "What changed in the expense reimbursement policy?")["reply"]
    assert reply["meta"]["mode"] == "direct" and reply["meta"]["intent"] == "changes"
    assert f"**Expense Reimbursement Policy** [D{expense}] was updated 2 days ago (version 2)" in reply["content"]
    assert "INR 7,000 per night" in reply["content"] and "INR 6,000 per night" in reply["content"]
    assert reply["meta"]["changes"] == [{"id": expense, "title": "Expense Reimbursement Policy", "version": 2, "from_version": 1}]
    # The same through the quick action on the document's page.
    quick = _ask(client, login("karan"), document_id=expense, intent="changes")["reply"]
    assert quick["meta"]["changes"][0]["version"] == 2


def test_whats_new_lists_recent_changes_you_can_open(client, login):
    reply = _ask(client, login("karan"), "What's new this month?")["reply"]
    assert "Expense Reimbursement Policy" in reply["content"] and "Office Guide" in reply["content"]
    assert "Release Checklist" not in reply["content"]  # Engineering's synced file: Karan can't open it


def test_compare_two_documents(client, login, doc_id):
    neha = login("neha")
    handbook, conduct = doc_id("Employee Handbook 2026"), doc_id("Code of Conduct")
    reply = _ask(client, neha, document_id=handbook, intent="compare", compare_with=conduct)["reply"]
    assert reply["meta"]["intent"] == "compare" and {c["id"] for c in reply["meta"]["citations"]} == {handbook, conduct}
    by_name = _ask(client, neha, "Compare the employee handbook with the code of conduct")["reply"]
    assert {c["id"] for c in by_name["meta"]["citations"]} == {handbook, conduct}
    # Can't compare with a document you can't open.
    r = client.post(
        "/api/assistant/chat",
        headers=neha,
        json={"message": "", "intent": "compare", "document_id": handbook, "compare_with": doc_id("Salary Bands 2026")},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------- actions, only after confirming


def _action(reply: dict, kind: str) -> dict:
    return next(a for a in reply["meta"]["actions"] if a["kind"] == kind)


def test_request_access_from_a_locked_match(client, login, doc_id):
    aarav = login("aarav")
    out = _ask(client, aarav, "What are the salary bands for SDE-2?")
    action = _action(out["reply"], "request_access")
    assert action["status"] == "proposed" and action["payload"]["document_id"] == doc_id("Salary Bands 2026")
    with SessionLocal() as db:
        assert db.scalar(select(AccessRequest).where(AccessRequest.requester_id == _uid("aarav"))) is None  # nothing yet

    assert client.post(f"/api/assistant/actions/{action['id']}/confirm", headers=login("karan"), json={}).status_code == 404
    r = client.post(
        f"/api/assistant/actions/{action['id']}/confirm",
        headers=aarav,
        json={"reason": "Preparing for my internship review", "duration_days": 3},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"
    with SessionLocal() as db:
        req = db.scalar(select(AccessRequest).where(AccessRequest.requester_id == _uid("aarav")))
        assert req.reason == "Preparing for my internship review" and req.duration_days == 3
    assert client.post(f"/api/assistant/actions/{action['id']}/confirm", headers=aarav, json={}).status_code == 409
    detail = client.get(f"/api/assistant/conversations/{out['conversation']['id']}", headers=aarav).json()
    assert _action(detail["messages"][1], "request_access")["status"] == "done"


def test_share_with_a_team_needs_a_manager_and_a_confirmation(client, login, doc_id):
    api_guide = doc_id("API Design Guidelines")
    karan = login("karan")
    assert client.get(f"/api/documents/{api_guide}", headers=karan).json()["access"]["allowed"] is False

    intern = _ask(client, login("aarav"), "Share the API Design Guidelines with Sales")["reply"]
    assert "can't change who" in intern["content"] and "actions" not in intern["meta"]

    rohan = login("rohan")
    reply = _ask(client, rohan, "Share the API Design Guidelines with Sales")["reply"]
    action = _action(reply, "share")
    assert action["payload"]["target"]["kind"] == "team"
    assert client.get(f"/api/documents/{api_guide}", headers=karan).json()["access"]["allowed"] is False  # not yet
    assert client.post(f"/api/assistant/actions/{action['id']}/confirm", headers=rohan, json={}).status_code == 200
    assert client.get(f"/api/documents/{api_guide}", headers=karan).json()["access"]["allowed"] is True


def test_give_one_person_access(client, login, doc_id):
    runbook = doc_id("Incident Response Runbook")
    rohan = login("rohan")
    action = _action(_ask(client, rohan, "Give Aarav access to the incident response runbook")["reply"], "share")
    assert action["payload"]["target"]["name"] == "Aarav Mehta" and action["payload"]["duration_days"] == 30
    r = client.post(f"/api/assistant/actions/{action['id']}/confirm", headers=rohan, json={"duration_days": 5})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/documents/{runbook}", headers=login("aarav")).json()["access"]["allowed"] is True
    with SessionLocal() as db:
        grant = db.scalar(select(AccessGrant).where(AccessGrant.user_id == _uid("aarav")))
        assert grant.granted_by == _uid("rohan")
        assert db.scalar(select(Notification).where(Notification.kind == "access.granted")).user_id == _uid("aarav")


def test_save_an_answer_as_a_private_document(client, login):
    neha = login("neha")
    first = _ask(client, neha, LEAVE)
    convo = first["conversation"]["id"]
    reply = _ask(client, neha, "save this", conversation_id=convo)["reply"]
    action = _action(reply, "save_summary")
    assert reply["meta"]["mode"] == "direct" and action["payload"]["message_id"] == first["reply"]["id"]
    r = client.post(f"/api/assistant/actions/{action['id']}/confirm", headers=neha, json={"title": "My leave notes"})
    assert r.status_code == 200, r.text
    new_id = int(r.json()["result"]["link"].rsplit("/", 1)[1])
    doc = client.get(f"/api/documents/{new_id}", headers=neha).json()
    assert doc["title"] == "My leave notes" and doc["classification"] == "restricted"
    assert "24 days of earned leave" in doc["preview"] and "## Sources" in doc["preview"] and "[1]" in doc["preview"]
    assert client.get(f"/api/documents/{new_id}", headers=login("karan")).status_code == 404

    # The Save button under an answer does the same, for your own answers only.
    r = client.post("/api/assistant/actions", headers=neha, json={"kind": "save_summary", "message_id": first["reply"]["id"]})
    assert r.status_code == 201 and r.json()["status"] == "proposed"
    other = client.post(
        "/api/assistant/actions", headers=login("karan"), json={"kind": "save_summary", "message_id": first["reply"]["id"]}
    )
    assert other.status_code == 404


def test_draft_a_message(client, login):
    reply = _ask(client, login("priya"), "Draft an email to Rohan about the on-call rotation")["reply"]
    assert reply["meta"]["mode"] == "direct" and reply["meta"]["intent"] == "draft"
    action = _action(reply, "draft_message")
    assert action["payload"]["to"] == "Rohan Iyer" and action["payload"]["email"] == "rohan@northwind.example"
    assert action["payload"]["body"].startswith("Hi Rohan,") and "on-call rotation" in action["payload"]["body"]


def test_dismissed_actions_cannot_run(client, login):
    aarav = login("aarav")
    action = _action(_ask(client, aarav, "What are the salary bands for SDE-2?")["reply"], "request_access")
    assert client.post(f"/api/assistant/actions/{action['id']}/dismiss", headers=aarav).json()["status"] == "dismissed"
    assert client.post(f"/api/assistant/actions/{action['id']}/confirm", headers=aarav, json={}).status_code == 409
