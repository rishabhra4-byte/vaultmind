"""Search by meaning (optional embeddings), against a stand-in embedding service: off by
default, follows the AI data policy, never widens access, and falls back to words."""

import io

import pytest
from fakeserver import FakeEmbeddings
from office import xlsx
from sqlalchemy import func, select

from app import embeddings, metrics, retrieval
from app.config import get_settings
from app.database import SessionLocal
from app.models import ChunkEmbedding, User
from app.services import access_view

QUESTION = "Who has to sign off when spending goes over 2 lakh?"
APPROVALS = xlsx(
    {"Approvers": [["Amount up to (INR)", "Approver"], [50000, "Manager"], [200000, "Department head"], ["Above", "CFO"]]}
)


@pytest.fixture
def emb(monkeypatch):
    fake = FakeEmbeddings()
    s = get_settings()
    monkeypatch.setattr(s, "embeddings_url", fake.base)
    monkeypatch.setattr(s, "embeddings_model", "fake-embed-1")
    monkeypatch.setattr(s, "embeddings_api_key", fake.key)
    embeddings.reset()
    yield fake
    embeddings.reset()
    embeddings.vectors.clear()
    fake.close()


def _upload(client, headers, name, data, **form):
    form = {"classification": "internal", **form}
    r = client.post(
        "/api/documents", headers=headers, files={"file": (name, io.BytesIO(data), "application/octet-stream")}, data=form
    )
    assert r.status_code == 201, r.text
    return r.json()["document"]["id"]


def _embed_all() -> int:
    with SessionLocal() as db:
        return embeddings.embed_pending(db, limit=None)


def _ranking(who: str, question: str = QUESTION) -> retrieval.Ranking:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == f"{who}@northwind.example"))
        return retrieval.rank(db, access_view(db, user), question, top_k=get_settings().retrieval_top_k)


def test_off_unless_configured(client, login):
    assert not embeddings.enabled()
    with SessionLocal() as db:
        assert embeddings.embed_pending(db) == 0
    assert "meaning" not in _ranking("rohan").info


def test_a_passage_worded_differently_is_found_by_meaning(client, login, emb):
    approvals = _upload(client, login("rohan"), "Approval limits.xlsx", APPROVALS)
    assert approvals not in [h.doc_id for h in _ranking("rohan").hits], "no word of the question is in it"

    assert _embed_all() > 0
    ranking = _ranking("rohan")
    assert ranking.info.get("meaning") is True
    assert approvals in [h.doc_id for h in ranking.hits[: get_settings().retrieval_top_k]]


def test_the_ai_reads_the_passage_closest_in_meaning(client, login, emb, fake_llm):
    approvals = _upload(client, login("rohan"), "Approval limits.xlsx", APPROVALS)
    _embed_all()
    fake_llm.reply = f"Above 2 lakh, the CFO signs off [D{approvals}]."
    reply = client.post("/api/assistant/chat", headers=login("rohan"), json={"message": QUESTION}).json()["reply"]
    assert "Approver: Department head" in fake_llm.last_prompt and "Approver: CFO" in fake_llm.last_prompt
    assert reply["meta"]["mode"] == "ai" and approvals in [c["id"] for c in reply["meta"]["citations"]]
    assert reply["meta"]["search"]["meaning"] is True


def test_offline_answers_still_need_the_words(client, login, emb):
    # Meaning brings a passage in, but never on its own makes an offline answer claim enough
    # evidence: no sentence says the question's words, so that check (on words) still decides.
    _upload(client, login("rohan"), "Approval limits.xlsx", APPROVALS)
    _embed_all()
    reply = client.post("/api/assistant/chat", headers=login("rohan"), json={"message": QUESTION}).json()["reply"]
    assert reply["meta"]["mode"] == "offline" and reply["meta"]["evidence"]["enough"] is False


def test_meaning_never_reaches_documents_the_person_cannot_open(client, login, emb):
    secret = _upload(
        client, login("ananya"), "Board approvals.xlsx", APPROVALS, classification="confidential", title="Board approvals"
    )
    _embed_all()
    for who in ("aarav", "priya"):  # an intern and a senior engineer: below the confidential threshold
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == f"{who}@northwind.example"))
            view = access_view(db, user)
            assert secret not in view.allowed
            ranking = retrieval.rank(db, view, QUESTION)
        assert secret not in [h.doc_id for h in ranking.hits]
        assert all(h.doc_id in view.allowed for h in ranking.hits)


def test_only_what_the_ai_may_read_is_sent_and_secrets_are_masked(client, login, emb):
    rohan = login("rohan")
    off = _upload(client, rohan, "Vendor notes.txt", b"Vendor Zephyrine invoices are approved by the CFO.")
    client.patch(f"/api/documents/{off}", headers=rohan, json={"ai_enabled": False})
    _upload(client, rohan, "Restricted plan.txt", b"Project Quillfeather budget is 9 crore.", classification="restricted")
    _upload(
        client,
        rohan,
        "Payouts.txt",
        b"Contractor Ravi, PAN ABCDE1234F, payout approved.",
        classification="confidential",
        acknowledge_sensitivity="true",
    )
    _embed_all()
    sent = "\n".join(emb.inputs)
    assert "Zephyrine" not in sent, "AI reading turned off for that document"
    assert "Quillfeather" not in sent, "restricted documents are above the AI ceiling"
    assert "Contractor Ravi" in sent and "ABCDE1234F" not in sent, "ID numbers are masked first"

    emb.inputs.clear()
    _ranking("rohan", "What is my PAN ABCDE1234F used for?")
    assert emb.inputs and "ABCDE1234F" not in emb.inputs[-1], "questions are masked too"


def test_turning_ai_off_for_a_document_drops_its_vectors(client, login, emb):
    rohan = login("rohan")
    approvals = _upload(client, rohan, "Approval limits.xlsx", APPROVALS)
    _embed_all()
    assert embeddings.vectors.has_doc(approvals)
    client.patch(f"/api/documents/{approvals}", headers=rohan, json={"ai_enabled": False})
    assert not embeddings.vectors.has_doc(approvals), "gone from search at once"
    _embed_all()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ChunkEmbedding)) == embeddings.vectors.__len__()
        rows = db.scalars(select(ChunkEmbedding.chunk_id)).all()
    assert not any(embeddings.vectors._doc.get(c) == approvals for c in rows)


def test_a_new_version_gets_new_vectors(client, login, emb):
    rohan = login("rohan")
    approvals = _upload(client, rohan, "Approval limits.xlsx", APPROVALS)
    _embed_all()
    before = set(embeddings.vectors._by_doc[approvals])
    newer = xlsx({"Approvers": [["Amount up to (INR)", "Approver"], [100000, "Manager"], ["Above", "CFO"]]})
    r = client.post(
        f"/api/documents/{approvals}/versions",
        headers=rohan,
        files={"file": ("Approval limits.xlsx", io.BytesIO(newer), "application/octet-stream")},
    )
    assert r.status_code in (200, 201), r.text
    assert not embeddings.vectors.has_doc(approvals), "old passages are gone; the new ones wait for the background job"
    assert _embed_all() > 0
    assert embeddings.vectors._by_doc[approvals] and not (embeddings.vectors._by_doc[approvals] & before)


def test_a_new_model_replaces_the_vectors(client, login, emb, monkeypatch):
    _embed_all()
    n = len(embeddings.vectors)
    assert n > 0
    monkeypatch.setattr(get_settings(), "embeddings_model", "fake-embed-2")
    embeddings.reset()
    with SessionLocal() as db:
        assert embeddings.load(db) == 0, "vectors from another model aren't used"
    assert _embed_all() == n
    with SessionLocal() as db:
        assert set(db.scalars(select(ChunkEmbedding.model))) == {"fake-embed-2"}


def test_when_the_service_fails_search_uses_words_alone(client, login, emb):
    rohan = login("rohan")
    _upload(client, rohan, "Approval limits.xlsx", APPROVALS)
    _embed_all()
    emb.fail = 503
    before = metrics.registry.value("vaultmind_embedding_failures_total", {"reason": "error", "stage": "question"})
    r = client.post("/api/assistant/chat", headers=rohan, json={"message": "How many days of earned leave do I get?"})
    assert r.status_code == 200
    reply = r.json()["reply"]
    assert "24 days" in reply["content"] and "meaning" not in reply["meta"].get("search", {})
    after = metrics.registry.value("vaultmind_embedding_failures_total", {"reason": "error", "stage": "question"})
    assert after == before + 1


def test_a_failing_service_doesnt_stop_uploads(client, login, emb):
    emb.fail = 401
    _upload(client, login("rohan"), "Approval limits.xlsx", APPROVALS)  # uploads never wait for vectors
    assert _embed_all() == 0
    assert metrics.registry.value("vaultmind_embedding_failures_total", {"reason": "auth", "stage": "passages"}) >= 1


def test_prefixes_for_models_that_want_them(client, login, emb, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "embeddings_query_prefix", "search_query: ")
    monkeypatch.setattr(s, "embeddings_document_prefix", "search_document: ")
    _upload(client, login("rohan"), "Approval limits.xlsx", APPROVALS)
    _embed_all()
    assert emb.inputs and all(t.startswith("search_document: ") for t in emb.inputs)
    emb.inputs.clear()
    _ranking("rohan")
    assert emb.inputs == ["search_query: " + QUESTION]


def test_the_embed_command_fills_every_vector(client, login, emb, capsys):
    from app.cli import main

    assert main(["embed"]) == 0
    out = capsys.readouterr().out
    with SessionLocal() as db:
        status = embeddings.status(db)
    assert status["with_vectors"] == status["passages"] > 0
    assert f"{status['passages']} of {status['passages']}" in out


def test_admins_see_how_many_passages_are_ready(client, login, emb, monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_token", "metrics-token-for-tests")

    def meaning():
        return client.get("/api/admin/operations", headers=login("dev")).json()["ai"]["meaning"]

    before = meaning()
    assert before["enabled"] and before["model"] == "fake-embed-1" and before["with_vectors"] == 0 < before["passages"]
    _embed_all()
    after = meaning()
    assert after["with_vectors"] == after["passages"] == before["passages"]
    body = client.get("/metrics", headers={"Authorization": "Bearer metrics-token-for-tests"}).text
    assert f"vaultmind_embedding_vectors {after['passages']}" in body
