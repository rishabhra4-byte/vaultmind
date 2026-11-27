"""What the search does beyond matching words: amounts inside a stated band, the kind of answer
a question asks for, two words written as one, ordinary words the documents don't use, and
when the evidence isn't enough."""

import gzip
import json

from sqlalchemy import select

from app import quantities, retrieval, thesaurus
from app.database import SessionLocal
from app.models import User
from app.services import access_view


def _ask(client, headers, message):
    r = client.post("/api/assistant/chat", headers=headers, json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()["reply"]


def _rank(who: str, question: str):
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email.like(f"{who}@%")))
        return retrieval.rank(db, access_view(db, user), question)


# ===================================================================== amounts


def test_amounts_are_read_with_their_kind():
    def found(text):
        return [(q.value, q.kind) for q in quantities.find(text)]

    assert found("Who can approve a 25% discount?") == [(25.0, "percent")]
    assert found("a 60k laptop order") == [(60000.0, "money")]
    assert found("a 30,000 rupee claim") == [(30000.0, "money")]
    assert found("INR 2.5 lakh per month") == [(250000.0, "money")]
    assert found("for six weeks") == [(42.0, "duration")]
    assert found("Can I work abroad for a month?") == [(30.0, "duration")]
    assert found("within 2 business days") == [(2.0, "duration")]
    assert found("a rating of 2") == [(2.0, "count")]


def test_names_years_and_times_are_not_amounts():
    for text in ("the salary band for SDE-2", "roles approved for H1 2027", "revenue in Q3 2026", "What counts as a SEV1?"):
        assert quantities.find(text) == [], text
    assert quantities.find("Core hours are 11:00 to 16:00 IST and doors open at 8am") == []


def test_a_sentence_states_bands():
    def bands(text):
        return [(b.low, b.high, b.kind) for b in quantities.bands(text)]

    inf = float("inf")
    assert bands("Sales Managers up to 20%.") == [(0.0, 20.0, "percent")]
    assert bands("Discounts above 20% need Finance Director approval, and above 30% need CEO approval.") == [
        (20.0, inf, "percent"),
        (30.0, inf, "percent"),
    ]
    assert bands("Purchase orders are required for any spend above INR 50,000.") == [(50000.0, inf, "money")]
    assert bands("feedback from 3 to 5 colleagues") == [(3.0, 5.0, "count")]
    assert bands("at least one week ahead") == [(7.0, inf, "duration")]
    assert bands("limited to 30 days a year") == [(0.0, 30.0, "duration")]
    assert bands("Keep pull requests under 400 lines") == [(0.0, 400.0, "count")]
    assert bands("at 4 or above") == [(4.0, inf, "count")]


def test_an_amount_fits_the_band_that_holds_it_and_only_its_own_kind():
    asked = quantities.find("a 25% discount")[0]
    assert quantities.fit(asked, quantities.bands("Discounts above 20% need Finance Director approval")) == quantities.FITS
    assert quantities.fit(asked, quantities.bands("Account Executives may offer up to 10%.")) == quantities.NEAR
    # INR 25,000 has the same digits and is no answer to a question about 25%.
    assert quantities.fit(asked, quantities.bands("List prices: Starter INR 25,000 per month")) == 0.0
    bare = quantities.find("a gift worth 5,000")[0]  # no currency: may still be money
    assert quantities.fit(bare, quantities.bands("Gifts above INR 2,000 in value must be declared")) == quantities.FITS


def test_a_question_about_an_amount_is_answered_from_the_band_it_falls_in(client, login):
    reply = _ask(client, login("meera"), "Who can approve a 25% discount?")
    assert reply["meta"]["evidence"]["enough"] is True
    assert "Finance Director approval" in reply["content"]
    assert "INR 25,000 per month" not in reply["content"].split("Sources")[0].split("\n")[0]
    reply = _ask(client, login("ananya"), "Do I need a PO for a 60k laptop order?")
    assert reply["meta"]["evidence"]["enough"] is True and "above INR 50,000" in reply["content"]


# ===================================================================== the kind of answer


def test_how_often_is_answered_by_a_sentence_that_states_a_frequency(client, login):
    reply = _ask(client, login("aarav"), "How often do interns meet their mentor?")
    assert reply["meta"]["evidence"]["enough"] is True and "every week" in reply["content"]


def test_a_sentence_with_no_frequency_or_number_is_not_an_answer_to_how_often_or_how_much(client, login):
    assert retrieval.answers_kind("How often is the VPN password rotated?", "Connect through the company VPN.") is False
    assert retrieval.answers_kind("How often does the shift rotate?", "A shift is one week, Monday 10am to Monday 10am.") is True
    assert retrieval.answers_kind("How much is the referral bonus?", "Referrals earn a bonus after probation.") is False
    assert retrieval.answers_kind("Who approves expenses?", "Your manager approves claims.") is True
    reply = _ask(client, login("aarav"), "How often is the VPN password rotated?")
    assert reply["meta"]["evidence"]["enough"] is False


def test_how_long_wants_a_length_of_time_not_whatever_number_is_there():
    # "every intern owns one scoped project" has a number in it, but says nothing about how long.
    assert retrieval.answers_kind("How long is the internship?", "Every intern owns one scoped project.") is False
    assert retrieval.answers_kind("How long is an on-call shift?", "A shift is one week.") is True
    assert retrieval.answers_kind("How late can I stay in the office?", "The doors close at 9pm.") is True
    # "How fast is it growing" is answered by a rate.
    assert retrieval.answers_kind("How fast are cloud costs growing?", "They grew 18% quarter on quarter.") is True


def test_how_soon_is_answered_by_a_time_that_names_no_length():
    asked = "How soon do I have to enter a client meeting into the CRM?"
    assert retrieval.answers_kind(asked, "Log every customer interaction in the CRM the same day.") is True
    assert retrieval.answers_kind(asked, "Page the on-call engineer immediately.") is True
    assert retrieval.answers_kind(asked, "Log every customer interaction in the CRM.") is False


def test_how_many_and_how_much_want_the_kind_of_amount_asked_for():
    # A number that is a label, or a length of time, does not answer "how much" or "how many".
    assert retrieval.answers_kind("How much is the parking fee?", "Parking is on levels B1 and B2.") is False
    assert retrieval.answers_kind("How much is the parking fee?", "Parking costs INR 2,000 a month.") is True
    headcount = "How many people are on the security team?"
    assert retrieval.answers_kind(headcount, "The team responds within one business hour.") is False
    assert retrieval.answers_kind(headcount, "The security team is four engineers.") is True
    # ...but "how much leave" is a length of time, and a count of days answers "how many days".
    assert retrieval.answers_kind("How much paternity leave can I take?", "Paternity leave is 6 weeks.") is True
    assert retrieval.answers_kind("How many leave days carry over?", "Up to 10 unused earned leave days carry over.") is True
    # A count the amount reader can't put a unit on ("one approving review") still answers.
    assert retrieval.answers_kind("How many approvals does a change need?", "Every change needs one approving review.") is True


def test_who_is_not_answered_by_repeating_the_role_it_asked_about():
    asked = "Who is the incident commander this week?"
    assert retrieval.answers_kind(asked, "Assign an incident commander for SEV1 and SEV2.") is False
    assert retrieval.answers_kind(asked, "The incident commander this week is Priya Sharma.") is True
    assert retrieval.answers_kind("Who approves expense claims?", "Your manager approves claims.") is True
    # A sentence that answers in words this doesn't know is left alone.
    assert retrieval.answers_kind("Who is our ideal customer?", "Our ideal customers are mid-size online retailers.") is True


# ===================================================================== words


def test_two_words_a_document_writes_as_one_still_match(client, login):
    ranking = _rank("dev", "How are deployments rolled out to production?")
    rolled = next(c for c in ranking.concepts if c.word == "rolled")
    assert "rollout" in rolled.terms
    reply = _ask(client, login("dev"), "How are deployments rolled out to production?")
    assert "canary release to 5%" in reply["content"]
    ranking = _rank("rohan", "How quickly must a post mortem be written?")
    assert all("postmortem" in c.terms and c.known for c in ranking.concepts if c.word in ("post", "mortem"))


def test_a_word_no_document_uses_falls_back_on_ordinary_english(client, login):
    assert ("meal", thesaurus.RELATED) in thesaurus.stand_ins("dinners")
    assert ("invoice", thesaurus.SAME) in thesaurus.stand_ins("bill")
    assert thesaurus.stand_ins("northwindish") == []
    ranking = _rank("rohan", "Do team dinners need approval?")
    dinners = next(c for c in ranking.concepts if c.word == "dinners")
    assert dinners.known and dinners.terms.get("meal") == thesaurus.RELATED
    reply = _ask(client, login("rohan"), "Do team dinners need approval?")
    assert reply["meta"]["evidence"]["enough"] is True and "prior manager approval" in reply["content"]


def test_a_word_the_documents_do_use_is_never_widened():
    # "manager" has synonyms like "director" in any thesaurus; using them would answer the wrong question.
    ranking = _rank("rohan", "Up to what amount can a manager approve expense claims?")
    for concept in ranking.concepts:
        if concept.known and concept.quantity is None:
            assert all(weight >= 0.6 for weight in concept.terms.values()), concept


def test_stand_ins_only_come_from_documents_the_person_may_open():
    # "discount" could stand in for "rebate", but only the confidential pricing matrix uses it:
    # an intern's search must not learn that word from a file they can't open.
    intern = _rank("aarav", "Can a customer get a rebate?")
    sales = _rank("meera", "Can a customer get a rebate?")
    assert "discount" in next(c for c in sales.concepts if c.word == "rebate").terms
    assert "discount" not in next(c for c in intern.concepts if c.word == "rebate").terms
    assert "discount" in sales.expanded and "discount" not in intern.expanded


def test_the_thesaurus_file_is_small_and_well_formed():
    assert thesaurus.FILE.stat().st_size < 1_000_000
    with gzip.open(thesaurus.FILE, "rt", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) > 20_000 and all(entry.count("|") == 1 for entry in data.values())
    assert (thesaurus.FILE.parent / "WORDNET-LICENSE").read_text().startswith("WordNet Release 3.0")


def test_the_search_works_without_the_thesaurus_file(client, login, tmp_path, monkeypatch):
    monkeypatch.setattr(thesaurus, "FILE", tmp_path / "missing.json.gz")
    thesaurus._data.cache_clear()
    try:
        assert thesaurus.stand_ins("dinners") == []
        reply = _ask(client, login("karan"), "How many days of earned leave do I get a year?")
        assert reply["meta"]["evidence"]["enough"] is True and "24 days" in reply["content"]
    finally:
        thesaurus._data.cache_clear()


# ===================================================================== enough evidence


def test_a_question_about_two_things_is_not_answered_by_a_sentence_about_one(client, login):
    reply = _ask(client, login("aarav"), "How do I change my mentor?")
    assert reply["meta"]["evidence"]["enough"] is False
    reply = _ask(client, login("dev"), "Which cloud provider do we run on?")
    assert reply["meta"]["evidence"]["enough"] is False


def test_the_document_with_the_answering_sentence_comes_first(client, login, doc_id):
    # The Holiday Calendar shares more words with the question; the Handbook has the sentence that answers it.
    ranking = _rank("neha", "How far in advance should I apply for planned leave?")
    assert ranking.hits[0].doc_id == doc_id("Employee Handbook 2026")
    assert ranking.enough and "one week ahead" in ranking.evidence[ranking.hits[0].doc_id].text


def test_a_line_that_only_repeats_the_title_is_never_the_answer(client, login):
    reply = _ask(client, login("priya"), "The printer is broken, who do I tell?")
    assert "#it-help" in reply["content"] or "IT ticket" in reply["content"]
