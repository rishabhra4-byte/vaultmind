"""Feedback on answers: giving it, changing and removing it, privacy, the admin review list,
and exporting it as benchmark drafts."""

import json

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import AnswerFeedback, AuditEvent

LEAVE = "How many days of earned leave do I get?"


def _ask(client, headers, message=LEAVE):
    r = client.post("/api/assistant/chat", headers=headers, json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()


def _rate(client, headers, message_id, **body):
    return client.post(f"/api/assistant/messages/{message_id}/feedback", headers=headers, json=body)


def test_feedback_is_saved_shown_again_and_can_be_changed_or_removed(client, login):
    priya = login("priya")
    chat = _ask(client, priya)
    answer = chat["reply"]["id"]
    assert _rate(client, priya, answer, rating="up").json() == {"feedback": {"rating": "up", "reason": "", "note": ""}}

    convo = client.get(f"/api/assistant/conversations/{chat['conversation']['id']}", headers=priya).json()
    assert convo["messages"][-1]["meta"]["feedback"]["rating"] == "up"  # shown when the conversation reopens

    r = _rate(client, priya, answer, rating="down", reason="missing", note="It didn't say how leave accrues.")
    assert r.json()["feedback"] == {"rating": "down", "reason": "missing", "note": "It didn't say how leave accrues."}
    assert _rate(client, priya, answer, rating=None).json() == {"feedback": None}
    with SessionLocal() as db:
        assert db.scalar(select(func.count(AnswerFeedback.id))) == 0
        assert db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.action == "assistant.feedback")) == 2


def test_only_your_own_answers_can_be_rated(client, login):
    priya, rohan = login("priya"), login("rohan")
    chat = _ask(client, priya)
    assert _rate(client, rohan, chat["reply"]["id"], rating="down").status_code == 404
    assert _rate(client, priya, chat["user_message"]["id"], rating="up").status_code == 404  # a question, not an answer
    assert _rate(client, priya, 999999, rating="up").status_code == 404
    assert _rate(client, priya, chat["reply"]["id"], rating="meh").status_code == 422
    assert _rate(client, priya, chat["reply"]["id"], rating="down", note="x" * 501).status_code == 422


def test_deleting_a_conversation_deletes_its_feedback(client, login):
    priya = login("priya")
    chat = _ask(client, priya)
    _rate(client, priya, chat["reply"]["id"], rating="down", reason="wrong")
    client.delete(f"/api/assistant/conversations/{chat['conversation']['id']}", headers=priya)
    with SessionLocal() as db:
        assert db.scalar(select(func.count(AnswerFeedback.id))) == 0


def test_admins_review_unhelpful_answers_without_seeing_who_gave_them(client, login):
    priya, neha = login("priya"), login("neha")
    _rate(client, priya, _ask(client, priya)["reply"]["id"], rating="up")
    bad = _ask(client, neha, "What dental insurance do we have?")
    _rate(client, neha, bad["reply"]["id"], rating="down", reason="should_abstain", note="We do have dental cover.")

    review = client.get("/api/admin/feedback", headers=login("dev")).json()
    assert review["counts"] == {"up": 1, "down": 1}
    [item] = review["items"]
    assert item["question"] == "What dental insurance do we have?"
    assert item["reason_label"] == "It should have said it doesn't know" and item["note"] == "We do have dental cover."
    assert item["mode"] == "offline" and item["enough"] is False
    assert "neha" not in json.dumps(item).lower()
    assert client.get("/api/admin/feedback", headers=priya).status_code == 403


def test_unhelpful_answers_export_as_benchmark_drafts(client, login, tmp_path, capsys):
    from app.cli import main

    neha = login("neha")
    chat = _ask(client, neha, "What dental insurance do we have?")
    _rate(client, neha, chat["reply"]["id"], rating="down", reason="missing")
    out = tmp_path / "feedback.jsonl"
    assert main(["export-feedback", "--out", str(out)]) == 0
    assert "Wrote 1 answer" in capsys.readouterr().out
    [draft] = [json.loads(line) for line in out.read_text().splitlines()]
    assert draft["who"] == "neha@northwind.example" and draft["question"] == "What dental insurance do we have?"
    assert draft["expect"] is None and draft["feedback"]["reason"] == "missing"
    assert draft["got"]["mode"] == "offline"


def test_feedback_is_counted_for_metrics(client, login, monkeypatch):
    from app import metrics

    metrics.registry.reset()
    priya = login("priya")
    _rate(client, priya, _ask(client, priya)["reply"]["id"], rating="up")
    assert metrics.registry.value("vaultmind_answer_feedback_total", {"rating": "up", "mode": "offline"}) == 1
