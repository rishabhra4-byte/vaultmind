from app.models import Classification as C
from app.search import BM25Index
from app.sensitivity import redact, scan
from app.text import best_snippet, chunk_text, stem, tokenize


def test_tokenize_and_stem():
    assert stem("policies") == stem("policy")
    assert stem("reviews") == stem("review")
    # Porter stemming relates the forms of a word, which the old suffix stripper missed.
    assert stem("required") == stem("require") == stem("requirement")
    assert stem("approval") == stem("approved") == stem("approver")
    assert stem("rating") == stem("ratings") == stem("rated")
    assert stem("travelling") == stem("travel")
    assert stem("sent") == stem("send") and stem("paid") == stem("pay")  # irregular forms
    assert "s" not in tokenize("What's the policy?")  # contractions split into stop words
    assert "the" not in tokenize("What is the leave policy?")
    assert tokenize("Leave Policies") == tokenize("leave policy")


def test_chunking_keeps_paragraphs_together():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(6))
    chunks = chunk_text(text, target_words=140)
    assert 2 <= len(chunks) <= 4
    assert all(c.startswith("Paragraph") for c in chunks)


def _index() -> BM25Index:
    idx = BM25Index()
    idx.add_document(1, "Leave policy", ["hr"], [(11, "Employees get 24 days of earned leave per year.")])
    idx.add_document(2, "API guidelines", ["engineering"], [(21, "Use plural nouns and cursor pagination.")])
    idx.add_document(3, "Salary bands", ["compensation"], [(31, "SDE-2 salary band is 22 to 34 lakh.")])
    return idx


def test_bm25_ranks_the_relevant_document_first():
    hits = _index().search("how many leave days do I get")
    assert hits[0].doc_id == 1


def test_search_only_scores_allowed_documents():
    hits = _index().search("salary band", allowed_doc_ids={1, 2})
    assert all(h.doc_id != 3 for h in hits)


def test_remove_document():
    idx = _index()
    idx.remove_document(3)
    assert idx.search("salary") == []
    assert len(idx) == 2


def test_scanner_flags_identity_numbers_and_secrets_as_restricted():
    assert scan("Employee PAN: ABCDE1234F").suggested == C.RESTRICTED
    assert scan("Aadhaar 2345 6789 0123").suggested == C.RESTRICTED
    assert scan("key = sk-abcdefghijklmnopqrstuvwx").suggested == C.RESTRICTED
    assert "card" in scan("Card 4111 1111 1111 1111").flags
    assert "card" not in scan("Order 1234 5678 9012 3456").flags, "fails the Luhn check"


def test_scanner_flags_compensation_as_confidential():
    result = scan("The salary band for SDE-2 is 22 to 34 lakh.")
    assert result.suggested == C.CONFIDENTIAL
    assert result.findings[0]["label"] == "Salary or compensation details"


def test_scanner_leaves_ordinary_text_public():
    assert scan("Our office is closed on public holidays.").suggested == C.PUBLIC


def test_confidential_marker_needs_a_document_marking_itself():
    """The word in passing doesn't make a document confidential: the office Wi-Fi guide says
    print jobs wait for your badge "so nothing confidential sits in the tray"."""
    for marked in (
        "# Salary Bands 2026 (confidential)\n\nBands...",
        "Q3 report (confidential, board use)",
        "CONFIDENTIAL\nPayroll for October",
        "Confidential: do not forward",
        "This document is confidential.",
        "Strictly confidential. Leadership only.",
        "Shared under NDA with the vendor.",
    ):
        assert "confidential_marker" in scan(marked).flags, marked
    for ordinary in (
        "Print jobs are held until you tap your badge, so nothing confidential sits in the tray.",
        "Never discuss confidential matters in the cafeteria.",
        "Treat customer data as confidential.",
    ):
        assert "confidential_marker" not in scan(ordinary).flags, ordinary


def test_redact_masks_identifiers_but_keeps_context():
    out = redact("Ravi, PAN ABCDE1234F, salary 85,000, mail ravi@example.com")
    assert "ABCDE1234F" not in out and "ravi@example.com" not in out
    assert "salary 85,000" in out


def test_level_names_like_sde_2_match_exactly():
    assert "sde2" in tokenize("What is the SDE-2 band?")
    idx = BM25Index()
    idx.add_document(1, "Bands", [], [(1, "SDE-1: 14 to 22."), (2, "SDE-2: 22 to 34.")])
    assert idx.search("SDE-2 band", chunks_per_doc=1)[0].chunks[0].chunk_id == 2


def test_snippet_prefers_the_sentence_with_the_rarest_matching_words():
    text = (
        "Travel: book flights and hotels through the company travel desk. "
        "Economy class for flights under six hours. "
        "Hotel limit is INR 7,000 per night in metro cities. "
        "Meals while travelling are reimbursed."
    )
    assert best_snippet(text, "what is the hotel limit for travel").startswith("Hotel limit is INR 7,000")
