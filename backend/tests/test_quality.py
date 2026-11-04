"""The fix-it queue (real questions the assistant got wrong, and whether it gets them right now)
and verified answers (approved answers to common questions, shown first)."""

import io
import json
from datetime import timedelta

from conftest import FakeLLM
from sqlalchemy import select

from app import llm, verified
from app.database import SessionLocal
from app.models import Notification, ReviewCase, Team, User, VerifiedAnswer, utcnow

LEAVE = "How many days of earned leave do I get?"


def _ask(client, headers, message, **extra):
    r = client.post("/api/assistant/chat", headers=headers, json={"message": message, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _team(name: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(Team.id).where(Team.name == name))


def _verify(client, login, question, answer, docs, team="Human Resources", **extra):
    body = {"question": question, "answer": answer, "doc_ids": docs, "owner_team_id": _team(team), **extra}
    r = client.post("/api/admin/verified", headers=login("dev"), json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ===================================================================== verified answers


def test_a_verified_answer_comes_first_and_says_who_stands_behind_it(client, login, doc_id):
    handbook = doc_id("Employee Handbook 2026")
    va = _verify(
        client,
        login,
        LEAVE,
        f"Everyone full-time gets **24 days** of earned leave a year [D{handbook}].",
        [handbook],
        phrasings=["How much annual leave do I get?"],
    )
    assert va["status"] == "current" and va["by"] == "Dev Patel" and va["team"] == "Human Resources"
    for question in (LEAVE, "how many earned leave days do i get", "How much annual leave do I get?"):
        reply = _ask(client, login("priya"), question)["reply"]
        assert reply["meta"]["mode"] == "verified", question
        assert reply["meta"]["verified"]["by"] == "Dev Patel" and reply["meta"]["verified"]["team"] == "Human Resources"
        assert [c["id"] for c in reply["meta"]["citations"]] == [handbook]
        assert reply["content"].startswith("Everyone full-time gets **24 days**")


def test_a_different_question_with_shared_words_gets_an_ordinary_answer(client, login, doc_id):
    handbook = doc_id("Employee Handbook 2026")
    _verify(client, login, LEAVE, f"24 days a year [D{handbook}].", [handbook])
    for question in ("How many days of sick leave do I get?", "How many days of parental leave do I get?"):
        assert _ask(client, login("priya"), question)["reply"]["meta"]["mode"] == "offline", question


def test_people_who_cant_open_every_source_never_see_it(client, login, doc_id):
    salary = doc_id("Salary Bands 2026")
    _verify(client, login, "What is the SDE-2 salary band?", f"SDE-2 is INR 22 to 34 lakh [D{salary}].", [salary])
    intern = _ask(client, login("aarav"), "What is the SDE-2 salary band?")["reply"]
    assert intern["meta"]["mode"] != "verified" and "22 to 34" not in intern["content"]
    manager = _ask(client, login("rohan"), "What is the SDE-2 salary band?")["reply"]
    assert manager["meta"]["mode"] == "verified"


def test_a_changed_source_or_a_passed_review_date_stops_it_and_reminds_the_owners(client, login, doc_id):
    handbook = doc_id("Employee Handbook 2026")
    va = _verify(client, login, LEAVE, f"24 days a year [D{handbook}].", [handbook])
    files = {
        "file": (
            "handbook.txt",
            io.BytesIO(b"# Leave\n\nEvery full-time employee receives 26 days of earned leave."),
            "text/plain",
        )
    }
    assert client.post(f"/api/documents/{handbook}/versions", headers=login("dev"), files=files).status_code in (200, 201)

    assert _ask(client, login("priya"), LEAVE)["reply"]["meta"]["mode"] != "verified", "an old answer isn't shown as verified"
    with SessionLocal() as db:
        assert verified.remind(db) >= 1
        db.commit()
        assert verified.remind(db) == 0, "once per change"
        titles = db.scalars(select(Notification.title).where(Notification.kind == "verified_review")).all()
    assert titles and "earned leave" in titles[0]
    listing = client.get("/api/admin/verified", headers=login("dev")).json()
    assert listing[0]["status"] == "changed" and listing[0]["sources"][0]["changed"]

    assert client.post(f"/api/admin/verified/{va['id']}/reviewed", headers=login("dev")).json()["status"] == "current"
    assert _ask(client, login("priya"), LEAVE)["reply"]["meta"]["mode"] == "verified"

    with SessionLocal() as db:
        row = db.get(VerifiedAnswer, va["id"])
        row.verified_at = utcnow() - timedelta(days=200)
        db.commit()
    assert _ask(client, login("priya"), LEAVE)["reply"]["meta"]["mode"] != "verified", "past its review date"
    assert client.get("/api/admin/verified", headers=login("dev")).json()[0]["status"] == "due"


def test_filters_that_exclude_its_sources_skip_it(client, login, doc_id):
    handbook = doc_id("Employee Handbook 2026")
    _verify(client, login, LEAVE, f"24 days a year [D{handbook}].", [handbook])
    reply = _ask(client, login("priya"), LEAVE, filters={"team_id": _team("Engineering")})["reply"]
    assert reply["meta"]["mode"] != "verified"


def test_verified_answers_use_no_ai(client, login, doc_id):
    fake = FakeLLM("should not be called")
    llm.set_llm(fake)
    handbook = doc_id("Employee Handbook 2026")
    _verify(client, login, LEAVE, f"24 days a year [D{handbook}].", [handbook])
    assert _ask(client, login("priya"), LEAVE)["reply"]["meta"]["mode"] == "verified"
    assert not fake.calls


def test_bad_verified_answers_are_refused(client, login, doc_id):
    handbook, expense = doc_id("Employee Handbook 2026"), doc_id("Expense Reimbursement Policy")
    base = {"question": LEAVE, "answer": "24 days.", "doc_ids": [handbook], "owner_team_id": _team("Human Resources")}
    dev = login("dev")
    r = client.post("/api/admin/verified", headers=dev, json={**base, "answer": f"24 days [D{expense}]."})
    assert r.status_code == 422 and "isn't one of its sources" in r.json()["detail"]
    r = client.post("/api/admin/verified", headers=dev, json={**base, "question": "Leave?"})
    assert r.status_code == 422 and "at least two words" in r.json()["detail"]
    assert client.post("/api/admin/verified", headers=dev, json={**base, "doc_ids": []}).status_code == 422
    assert client.post("/api/admin/verified", headers=login("rohan"), json=base).status_code == 403


# ===================================================================== the fix-it queue


def _feedback_down(client, headers, message, reason="wrong", note=""):
    reply = _ask(client, headers, message)["reply"]
    r = client.post(
        f"/api/assistant/messages/{reply['id']}/feedback",
        headers=headers,
        json={"rating": "down", "reason": reason, "note": note},
    )
    assert r.status_code == 200, r.text
    return reply


def _queue(client, login):
    r = client.get("/api/admin/review", headers=login("dev"))
    assert r.status_code == 200, r.text
    return r.json()


def test_unhelpful_and_unanswered_questions_are_queued_without_who_asked(client, login):
    _feedback_down(client, login("priya"), "What is the hotel limit per night in metro cities?", "source", "Used the old policy")
    _ask(client, login("aarav"), "What is the dental insurance cover for parents?")
    _ask(client, login("karan"), "what is the dental insurance cover for parents")  # the same question, asked again
    _ask(client, login("aarav"), "zxqv blorptastic")  # nothing matches at all
    q = _queue(client, login)
    by_q = {row["key"]: row for row in q["open"]}
    hotel = by_q["what is the hotel limit per night in metro cities"]
    assert [w["kind"] for w in hotel["why"]] == ["unhelpful"]
    assert hotel["notes"] == [{"reason": "It used the wrong document", "note": "Used the old policy"}]
    assert hotel["answer"]["cited"], "what the answer used"
    dental = by_q["what is the dental insurance cover for parents"]
    assert dental["asked"] == 2 and dental["why"][0]["kind"] == "not_enough"
    assert by_q["zxqv blorptastic"]["why"][0]["kind"] == "no_match"
    text = json.dumps(q)
    assert "Aarav" not in text and "Priya" not in text and "@northwind" not in text


def test_a_decision_is_checked_as_the_person_who_asked(client, login, doc_id):
    expense, travel = doc_id("Expense Reimbursement Policy"), doc_id("Travel Policy 2024")
    _feedback_down(client, login("priya"), "What is the hotel limit per night in metro cities?")
    dev = login("dev")
    body = {"question": "What is the hotel limit per night in metro cities?", "doc_ids": [expense], "says": ["7,000"]}
    case = client.post("/api/admin/review", headers=dev, json=body).json()
    assert case["check"]["passed"] is True, case["check"]
    assert expense in [c["id"] for c in case["check"]["got"]["cited"]]
    # A decision the assistant doesn't meet is reported with what went wrong.
    wrong = client.post("/api/admin/review", headers=dev, json={**body, "doc_ids": [travel], "says": ["5,500"]}).json()
    assert wrong["check"]["passed"] is False
    assert "It didn't say “5,500”." in wrong["check"]["problems"]
    q = _queue(client, login)
    assert "what is the hotel limit per night in metro cities" not in {r["key"] for r in q["open"]}, "decided: off the queue"
    assert q["summary"]["resolved"] == 1


def test_nothing_answers_this_passes_when_it_says_so(client, login):
    _ask(client, login("aarav"), "What is the dental insurance cover for parents?")
    case = client.post(
        "/api/admin/review",
        headers=login("dev"),
        json={"question": "What is the dental insurance cover for parents?", "expect": "abstain"},
    ).json()
    assert case["check"]["passed"] is True and case["check"]["got"]["enough"] is False


def test_a_document_the_asker_cant_open_should_be_offered_not_used(client, login, doc_id):
    salary = doc_id("Salary Bands 2026")
    _feedback_down(client, login("aarav"), "What is the SDE-2 salary band?", "missing")
    case = client.post(
        "/api/admin/review", headers=login("dev"), json={"question": "What is the SDE-2 salary band?", "doc_ids": [salary]}
    ).json()
    assert case["check"]["passed"] is True, case["check"]
    assert salary in [l["id"] for l in case["check"]["got"]["locked"]] and salary not in [
        c["id"] for c in case["check"]["got"]["cited"]
    ]


def test_a_verified_answer_can_fix_a_case(client, login, doc_id):
    handbook = doc_id("Employee Handbook 2026")
    question = "How long is the notice period for interns?"
    _feedback_down(client, login("aarav"), question, "missing")
    case = client.post(
        "/api/admin/review", headers=login("dev"), json={"question": question, "doc_ids": [handbook], "says": ["30 days"]}
    ).json()
    _verify(client, login, question, f"Interns give **30 days** notice [D{handbook}].", [handbook])
    again = client.post(f"/api/admin/review/{case['id']}/check", headers=login("dev")).json()
    assert again["check"]["passed"] is True and again["check"]["got"]["mode"] == "verified"


def test_dismiss_reopen_check_all_and_export(client, login, doc_id):
    expense = doc_id("Expense Reimbursement Policy")
    dev = login("dev")
    _ask(client, login("aarav"), "zxqv blorptastic")
    _feedback_down(client, login("priya"), "What is the hotel limit per night in metro cities?")
    client.post("/api/admin/review", headers=dev, json={"question": "zxqv blorptastic", "status": "dismissed"})
    case = client.post(
        "/api/admin/review",
        headers=dev,
        json={"question": "What is the hotel limit per night in metro cities?", "doc_ids": [expense]},
    ).json()
    q = _queue(client, login)
    assert q["open"] == [] and q["summary"]["dismissed"] == 1
    assert client.post("/api/admin/review/check-all", headers=dev).json() == {"checked": 1, "passing": 1}
    lines = [json.loads(line) for line in client.get("/api/admin/review/export", headers=dev).text.splitlines()]
    assert lines == [
        {
            "id": f"case-{case['id']}",
            "asked_by": "Senior, Engineering",
            "question": "What is the hotel limit per night in metro cities?",
            "expect": "answer",
            "docs": ["Expense Reimbursement Policy"],
            "says": [],
            "last_check": True,
        }
    ]
    assert client.delete(f"/api/admin/review/{case['id']}", headers=dev).status_code == 204
    assert "what is the hotel limit per night in metro cities" in {r["key"] for r in _queue(client, login)["open"]}
    with SessionLocal() as db:
        assert db.scalar(select(ReviewCase.id).where(ReviewCase.id == case["id"])) is None


def test_only_admins(client, login):
    rohan = login("rohan")
    assert client.get("/api/admin/review", headers=rohan).status_code == 403
    assert client.post("/api/admin/review/check-all", headers=rohan).status_code == 403
    assert client.get("/api/admin/verified", headers=rohan).status_code == 403


def test_the_demo_seeds_two_verified_answers(client, login):
    from app.seed import seed_verified_answers

    with SessionLocal() as db:
        assert seed_verified_answers(db) == 2
        assert seed_verified_answers(db) == 0
        assert (
            db.scalar(select(User.full_name).where(User.id == db.scalars(select(VerifiedAnswer.verified_by_id)).first()))
            == "Neha Kapoor"
        )
    assert _ask(client, login("aarav"), "What is the notice period?")["reply"]["meta"]["mode"] == "verified"
