"""Answers people can check: the sentence an answer rests on, saying so when the documents don't
hold enough, documents that disagree, out-of-date documents, and the filters the library and the
assistant share."""

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Team, User


def _ask(client, headers, message, **extra):
    r = client.post("/api/assistant/chat", headers=headers, json={"message": message, **extra})
    assert r.status_code == 200, r.text
    return r.json()["reply"]


def _uid(key: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == f"{key}@northwind.example"))


def _team(name: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(Team.id).where(Team.name == name))


def _titles(docs: list[dict]) -> list[str]:
    return [d["title"] for d in docs]


# ---------------------------------------------------------------- evidence


def test_an_answer_leads_with_the_sentence_that_answers_and_says_where_it_is(client, login, doc_id):
    reply = _ask(client, login("priya"), "What is the hotel limit per night in metro cities?")
    meta = reply["meta"]
    assert meta["evidence"]["enough"] is True
    expense = doc_id("Expense Reimbursement Policy")
    assert reply["content"].startswith("From **Expense Reimbursement Policy** [D")
    assert "INR 7,000 per night in metro cities" in reply["content"]
    card = meta["citations"][0]
    assert card["id"] == expense
    # What a source card tells the person about where the answer came from.
    assert card["passages"] and card["updated_at"] and card["version"] >= 1
    assert card["owner"] == "Ananya Gupta"
    assert card["access"]  # why this person may open it, in words
    assert "hotel" in card["matched"]


def test_not_enough_evidence_is_said_plainly_with_the_words_no_document_mentions(client, login):
    reply = _ask(client, login("priya"), "What is the dental insurance cover for parents?")
    meta = reply["meta"]
    assert meta["evidence"]["enough"] is False
    assert "dental" in meta["evidence"]["missing"]
    assert reply["content"].startswith("I couldn't find enough evidence to answer that in the documents you can open.")
    assert "“dental”" in reply["content"]
    # Whatever is listed is labelled as the closest matches, not as sources of an answer.
    if meta["citations"]:
        assert "The closest matches, which may not answer it:" in reply["content"]


def test_documents_that_disagree_are_both_shown_newest_first(client, login, doc_id):
    reply = _ask(client, login("priya"), "What is the hotel limit per night in metro cities?")
    conflicts = reply["meta"]["conflicts"]
    assert len(conflicts) == 1
    newer, older = conflicts[0]["a"], conflicts[0]["b"]
    assert (newer["id"], older["id"]) == (doc_id("Expense Reimbursement Policy"), doc_id("Travel Policy 2024"))
    assert any("7,000" in s for s in newer["says"]) and any("6,000" in s for s in older["says"])
    assert newer["updated_at"] > older["updated_at"]
    cited = {c["id"]: c for c in reply["meta"]["citations"]}
    assert older["id"] in cited, "both sides of a disagreement are listed as sources"
    assert cited[older["id"]]["outdated"]
    assert "Travel Policy 2024" in reply["content"]


def test_no_conflict_is_claimed_when_documents_agree(client, login):
    reply = _ask(client, login("priya"), "How many days of earned leave do I get?")
    assert reply["meta"]["evidence"]["enough"] is True
    assert "24 days of earned leave" in reply["content"]
    assert "conflicts" not in reply["meta"] and "disagree" not in reply["content"]


def test_old_documents_are_marked_possibly_out_of_date(client, login, doc_id):
    reply = _ask(client, login("priya"), "How much does the company pay towards home internet?")
    card = next(c for c in reply["meta"]["citations"] if c["id"] == doc_id("Remote Work Guidelines"))
    assert card["outdated"].startswith("Last updated 1 year")  # 420 days old, nothing newer disagrees
    assert "may be out of date" in reply["content"]


# ---------------------------------------------------------------- filters in the library


def test_library_filters_by_owner_and_recency(client, login):
    headers = login("priya")
    ananya = _uid("ananya")
    mine = client.get("/api/documents", headers=headers, params={"owner_id": ananya}).json()
    assert mine and all(d["uploaded_by_id"] == ananya for d in mine)
    assert "Expense Reimbursement Policy" in _titles(mine)

    everything = client.get("/api/documents", headers=headers).json()
    recent = client.get("/api/documents", headers=headers, params={"updated_within_days": 90}).json()
    assert 0 < len(recent) < len(everything)
    assert "Travel Policy 2024" not in _titles(recent) and "Remote Work Guidelines" not in _titles(recent)

    r = client.get("/api/documents", headers=headers, params={"updated_within_days": 0})
    assert r.status_code == 422


def test_library_search_finds_misspelt_words_and_respects_filters(client, login):
    headers = login("priya")
    found = client.get("/api/documents", headers=headers, params={"q": "reimbursment"}).json()
    assert _titles(found)[0] == "Expense Reimbursement Policy"
    recent = client.get("/api/documents", headers=headers, params={"q": "hotel limit", "updated_within_days": 90}).json()
    assert "Expense Reimbursement Policy" in _titles(recent) and "Travel Policy 2024" not in _titles(recent)


def test_library_search_never_matches_the_contents_of_locked_documents(client, login):
    # The salary bands are confidential; an intern sees the title but not what's inside.
    hits = client.get("/api/documents", headers=login("aarav"), params={"q": "lakh"}).json()
    assert "Salary Bands 2026" not in _titles(hits)


# ---------------------------------------------------------------- filters in the assistant


def test_assistant_answers_only_from_the_filtered_documents(client, login, doc_id):
    headers = login("priya")
    question = "What is the hotel limit per night in metro cities?"
    recent = _ask(client, headers, question, filters={"updated_within_days": 90})
    cited = {c["id"] for c in recent["meta"]["citations"]}
    assert doc_id("Expense Reimbursement Policy") in cited
    assert doc_id("Travel Policy 2024") not in cited
    assert "conflicts" not in recent["meta"], "the older policy was filtered out"
    assert recent["meta"]["search"]["scope"] == "documents updated in the last 90 days"


def test_a_filter_that_excludes_the_answer_says_so(client, login):
    question = "What is the hotel limit per night in metro cities?"
    reply = _ask(client, login("priya"), question, filters={"team_id": _team("Engineering")})
    assert reply["meta"]["evidence"]["enough"] is False
    assert reply["content"].startswith("I couldn't find enough evidence to answer that among Engineering documents.")
    assert "Removing a filter may help." in reply["content"]
    assert all(c["team"] == "Engineering" for c in reply["meta"]["citations"])


def test_filters_never_widen_access(client, login, doc_id):
    narrow = {"team_id": _team("Human Resources"), "classification": "confidential"}
    reply = _ask(client, login("aarav"), "What is the SDE-2 salary band?", filters=narrow)
    salary = doc_id("Salary Bands 2026")
    assert salary not in {c["id"] for c in reply["meta"]["citations"]}
    assert "34" not in reply["content"]


def test_bad_filters_are_rejected(client, login):
    r = client.post(
        "/api/assistant/chat",
        headers=login("priya"),
        json={"message": "hotel limit", "filters": {"source": "drop table"}},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------- corrections never come from locked documents


def test_spelling_corrections_and_acronyms_only_come_from_documents_you_can_open(client, login):
    import io

    rohan = login("rohan")  # Engineering manager: uploads a confidential Engineering note
    text = b"Project Quillfeather: the Zephyrine migration. The OKR (objectives and key results) review is on Friday."
    files = {"file": ("quill.txt", io.BytesIO(text), "text/plain")}
    form = {"classification": "confidential", "title": "Engineering leadership note", "discoverable": "false"}
    assert client.post("/api/documents", headers=rohan, files=files, data=form).status_code == 201

    def search_notes(headers, question):
        r = client.post("/api/assistant/chat", headers=headers, json={"message": question})
        return r.json()["reply"]["meta"].get("search", {})

    # Rohan can open it: his misspelling is corrected from it, and its acronym is understood.
    assert {"from": "zepyrine", "to": "zephyrine"} in search_notes(rohan, "When is the zepyrine migration?")["corrected"]
    assert any(a["acronym"] == "OKR" for a in search_notes(rohan, "When is the OKR review?").get("acronyms", []))
    # Priya (Senior, below the confidential threshold) can't: nothing from it shows up, not even a spelling.
    priya = login("priya")
    notes = search_notes(priya, "When is the zepyrine migration?")
    assert "zephyrine" not in str(notes)
    assert not any(a["acronym"] == "OKR" for a in search_notes(priya, "When is the OKR review?").get("acronyms", []))
