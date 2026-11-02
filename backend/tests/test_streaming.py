"""Answers streamed as they're written (server-sent events), and everything that can go wrong."""

import json

from conftest import FakeLLM

from app import llm
from app.llm import LLMError

LEAVE = "How many days of earned leave do I get?"


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def _stream(client, headers, **body):
    r = client.post("/api/assistant/chat/stream", headers=headers, json={"message": LEAVE, **body})
    return r


def test_an_ai_answer_arrives_piece_by_piece_then_whole(client, login, doc_id):
    handbook = doc_id("Employee Handbook 2026")
    llm.set_llm(FakeLLM(f"Every full-time employee gets 24 days of earned leave a year [D{handbook}] [D9999]."))
    r = _stream(client, login("priya"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    deltas = [d["text"] for kind, d in events if kind == "delta"]
    assert len(deltas) > 1, "more than one piece"
    assert "".join(deltas).startswith("Every full-time employee gets 24 days")
    kind, done = events[-1]
    assert kind == "done"
    reply = done["reply"]
    assert reply["meta"]["mode"] == "ai" and "[D9999]" not in reply["content"], "the kept answer has its citations checked"
    assert reply["content"].endswith(f"a year [D{handbook}].") and " ." not in reply["content"]
    convo = client.get(f"/api/assistant/conversations/{done['conversation']['id']}", headers=login("priya")).json()
    assert convo["messages"][-1]["id"] == reply["id"]  # saved like any other answer


def test_offline_answers_arrive_whole(client, login):
    events = _events(_stream(client, login("priya")).text)
    assert [kind for kind, _ in events] == ["done"]
    assert events[0][1]["reply"]["meta"]["mode"] == "offline" and "24 days" in events[0][1]["reply"]["content"]


def test_refusals_come_back_as_ordinary_errors(client, login):
    priya = login("priya")
    assert _stream(client, priya, message="  ").status_code == 422
    convo = _events(_stream(client, priya).text)[-1][1]["conversation"]["id"]
    assert _stream(client, login("rohan"), conversation_id=convo).status_code == 404


class FailingMidway(FakeLLM):
    def stream(self, system, messages, max_tokens=1024):
        self.calls.append({"system": system, "messages": messages})
        yield "Every full-time employee "
        raise LLMError("timeout", "slow")


def test_a_model_failing_midway_falls_back_to_an_offline_answer(client, login):
    llm.set_llm(FailingMidway())
    events = _events(_stream(client, login("priya")).text)
    assert events[0] == ("delta", {"text": "Every full-time employee "})
    reply = events[-1][1]["reply"]
    assert reply["meta"]["mode"] == "offline" and reply["meta"]["fallback"]["reason"] == "timeout"
    assert "24 days" in reply["content"]


def test_streaming_follows_the_daily_limits(client, login):
    from app.ai_budget import BudgetedLLM, DailyBudget

    llm.set_llm(BudgetedLLM(FakeLLM("You get 24 days."), DailyBudget(1)))
    headers = login("priya")
    assert _events(_stream(client, headers).text)[-1][1]["reply"]["meta"]["mode"] == "ai"
    last = _events(_stream(client, headers).text)[-1][1]["reply"]
    assert last["meta"]["mode"] == "offline" and last["meta"]["fallback"]["reason"] == "budget"


def test_the_ordinary_chat_endpoint_does_not_stream(client, login):
    fake = FakeLLM("You get 24 days.")
    llm.set_llm(fake)
    client.post("/api/assistant/chat", headers=login("priya"), json={"message": LEAVE})
    assert not any(c.get("streamed") for c in fake.calls)
