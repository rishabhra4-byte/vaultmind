"""The AI data policy, enforced in code: what may be sent to the AI service, and what never is."""

import io

from app.config import get_settings

RECORD = (
    "Contractor onboarding record for the Pune office. Aadhaar 2345 6789 0123 belongs to the contractor. "
    "Contact ravi@vendor.example for building access."
)


def _upload(client, headers, classification: str, text: str = RECORD, title: str = "Contractor record"):
    files = {"file": ("record.txt", io.BytesIO(text.encode()), "text/plain")}
    form = {"classification": classification, "title": title, "acknowledge_sensitivity": "true"}
    r = client.post("/api/documents", headers=headers, files=files, data=form)
    assert r.status_code == 201, r.text
    return r.json()["document"]


def _sent(fake_llm) -> str:
    return "\n".join(c["system"] + "\n".join(m["content"] for m in c["messages"]) for c in fake_llm.calls)


def test_restricted_documents_never_reach_the_ai_service_by_default(client, login, fake_llm):
    rohan = login("rohan")
    doc = _upload(client, rohan, "restricted")
    assert doc["ai_allowed"] is False
    assert doc["ai_blocked_reason"] == "Restricted documents are never sent to the AI service (a company setting)."
    reply = client.post(
        "/api/assistant/chat", headers=rohan, json={"message": "contractor onboarding record Pune building access"}
    ).json()["reply"]
    assert "Pune office" not in _sent(fake_llm), "not for the summary at upload, nor for the answer"
    assert doc["id"] in {d["id"] for d in reply["meta"]["ai_blocked"]}  # listed for the person to open themselves

    asked = client.post(
        "/api/assistant/chat", headers=rohan, json={"message": "Summarise this", "document_id": doc["id"]}
    ).json()["reply"]
    assert asked["meta"]["mode"] == "offline"
    assert asked["meta"]["notice"].startswith("Restricted documents are never sent to the AI service")
    assert "Pune office" not in _sent(fake_llm)


def test_the_ceiling_can_be_raised_or_lowered(client, login, fake_llm, monkeypatch):
    rohan = login("rohan")
    monkeypatch.setattr(get_settings(), "ai_max_classification", "restricted")
    doc = _upload(client, rohan, "restricted")
    assert doc["ai_allowed"] is True
    client.post("/api/assistant/chat", headers=rohan, json={"message": "contractor onboarding record Pune building access"})
    assert "Pune office" in _sent(fake_llm)
    assert "2345 6789 0123" not in _sent(fake_llm), "identity numbers are still masked"

    monkeypatch.setattr(get_settings(), "ai_max_classification", "internal")
    fake_llm.calls.clear()
    confidential = _upload(client, rohan, "confidential", RECORD.replace("Pune", "Nagpur"), "Nagpur record")
    assert confidential["ai_allowed"] is False
    client.post("/api/assistant/chat", headers=rohan, json={"message": "contractor onboarding record Nagpur building access"})
    assert "Nagpur office" not in _sent(fake_llm)  # the question goes out; the document does not


def test_upload_summaries_mask_identity_numbers_before_the_ai_sees_them(client, login, fake_llm):
    _upload(client, login("rohan"), "confidential")
    summary_call = fake_llm.calls[0]
    sent = summary_call["messages"][0]["content"]
    assert "Pune office" in sent  # the summary was written by the AI...
    assert "2345 6789 0123" not in sent and "[aadhaar number removed]" in sent  # ...without the number


def test_a_new_version_of_a_document_kept_from_the_ai_isnt_sent_to_describe_the_change(client, login, fake_llm, doc_id):
    admin = login("dev")
    handbook = doc_id("Employee Handbook 2026")
    assert client.patch(f"/api/documents/{handbook}", headers=admin, json={"ai_enabled": False}).status_code == 200
    fake_llm.calls.clear()
    new_text = b"# Employee Handbook 2026\n\nEveryone gets 30 days of earned leave."
    files = {"file": ("handbook.md", io.BytesIO(new_text), "text/markdown")}
    r = client.post(f"/api/documents/{handbook}/versions", headers=admin, files=files, data={"acknowledge_sensitivity": "true"})
    assert r.status_code == 200, r.text
    assert fake_llm.calls == [], "neither the change description nor the new summary went to the AI"
    assert r.json()["change_summary"]  # described from the diff instead


def test_search_rewrites_send_only_the_question(client, login, fake_llm):
    client.post("/api/assistant/chat", headers=login("priya"), json={"message": "zqx flurble widgets"})
    rewrite = next((c for c in fake_llm.calls if "keyword search" in c["system"]), None)
    if rewrite is not None:  # only when the first search found too little
        assert "<document" not in rewrite["messages"][0]["content"] and "[D" not in rewrite["messages"][0]["content"]
