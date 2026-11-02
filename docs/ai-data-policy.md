# AI data policy

What VaultMind sends to an AI service, when, and what it never sends. Everything here is
enforced in code ([`backend/app/ai_policy.py`](../backend/app/ai_policy.py) and the assistant)
and covered by tests named below. Your company still decides whether to use an AI service at
all: VaultMind works fully without one.

## Without an AI service

With no `VAULTMIND_ANTHROPIC_API_KEY`, nothing leaves your servers. Answers are built from the
matching passages themselves ("offline mode"), with the same permission checks, and every
answer says how it was made.

## With an AI service

With a key set, VaultMind uses Anthropic's API to write answers, summaries and descriptions
of changes. A document's text is sent only when **all** of these are true:

1. **The person asking may open the document.** Search runs only over documents they can open,
   before anything is sent.
2. **Its owners haven't turned AI reading off**, for the document or for the whole source it
   was synced from.
3. **Its classification is at or below `VAULTMIND_AI_MAX_CLASSIFICATION`.** The default is
   `confidential`, so **restricted documents never go to the AI service**. Set it to `internal`
   to keep confidential documents local too, or to `restricted` to allow everything people can
   open.

Before any text is sent, **identity numbers (Aadhaar, PAN), card numbers, API keys and
passwords are masked** (`[aadhaar number removed]`). Names, email addresses and phone numbers
are kept, because "who do I contact?" is one of the most common questions, and the person
asking can open the document anyway.

### What is sent, case by case

| When | What is sent | What is never sent |
|---|---|---|
| Answering a question (web or Slack) | The question; the person's name, job title, team and level; the best-matching passages (masked) of up to six documents (`VAULTMIND_RETRIEVAL_TOP_K`) allowed by the rules above, with their titles, teams, classifications and dates; the last six messages of the same conversation (masked); titles of matching documents the person can't open but may know exist (never their contents) | Documents the person can't open, documents kept from the AI, restricted documents (by default), earlier answers that quoted such documents |
| A weak search, rephrased | Only the question and the company's own glossary terms | Any document text |
| Summary at upload or sync | The title and the first 12,000 characters, masked, if the document may be read by the AI | Anything, for documents kept from the AI: their summary is their own first sentences |
| "What changed" in a new version | The changed, added and removed sentences, masked, if the document may be read by the AI | Anything, for documents kept from the AI: the change is described from the comparison alone |
| Ask about one document, compare two, draft a message | Passages of those documents (masked), if each may be read by the AI | The same as above |

Each request to the AI service is limited in length and time (`VAULTMIND_LLM_TIMEOUT_SECONDS`),
and the model is told that document text is data, not instructions.

### What happens to it

- **In VaultMind:** questions and answers are stored in the person's conversation history (they
  can delete conversations), and each question is recorded in the activity log. The insights
  page shows administrators what people ask, without who asked.
- **At the AI provider:** what Anthropic keeps, for how long and for what is set by your agreement
  with them, not by VaultMind. Check their commercial terms and data processing agreement, and
  whether your account has the retention options you need, before turning AI on for real
  documents.

## Controls for administrators

| Control | Where |
|---|---|
| Turn AI off entirely | Remove `VAULTMIND_ANTHROPIC_API_KEY` |
| Which model | `VAULTMIND_LLM_MODEL` (default `claude-sonnet-5-5`; `claude-haiku-5-5` costs a fraction) |
| Daily limits on AI use, company-wide and per person | `VAULTMIND_AI_DAILY_LIMIT`, `VAULTMIND_AI_DAILY_LIMIT_PER_PERSON` (past them, answers are built offline and say why) |
| Highest classification that may be sent | `VAULTMIND_AI_MAX_CLASSIFICATION` (shown on **Admin → Overview**) |
| Keep a whole source away from the AI | **Admin → Sources** → the source → *Let the assistant read these files* |
| Keep one document away from the AI | The document's page → *Let the assistant read this document* (its managers and admins) |
| See what's kept away | **Admin → Overview → AI**, and each document's page |

A document kept from the AI is still found by search, its passages are still quoted in offline
answers to people who can open it, and the answer says why the AI didn't read it.

## How this is tested

| Promise | Test |
|---|---|
| Restricted documents never reach the AI by default, not even for the summary at upload | `test_restricted_documents_never_reach_the_ai_service_by_default` |
| The ceiling can be raised or lowered, and masking still applies | `test_the_ceiling_can_be_raised_or_lowered` |
| Upload summaries are masked | `test_upload_summaries_mask_identity_numbers_before_the_ai_sees_them` |
| A new version of a document kept from the AI isn't sent to describe the change | `test_a_new_version_of_a_document_kept_from_the_ai_isnt_sent_to_describe_the_change` |
| Earlier answers quoting such documents are left out of follow-ups | `test_earlier_answers_from_documents_the_ai_may_not_read_are_left_out_of_follow_ups` |
| Documents with AI reading off never reach the model | `test_documents_with_ai_reading_off_never_reach_the_model` |
| Identity numbers and secrets are masked in answers | `test_identity_numbers_and_secrets_are_masked_before_the_model_sees_them` |
| Rephrasing a weak search sends only the question | `test_weak_searches_are_rephrased_by_the_model_without_sending_documents` |
| Nothing the person can't open is sent or leaked | The answer-quality benchmark's leak check over 229 questions: 0 leaks (`python -m eval.run`) |

These tests use a stand-in for the AI service that records everything it receives. A run
against the real API is a separate, opt-in test (`tests/test_live_anthropic.py`).

**Fixed on 31 October 2026:** before then, upload summaries sent document text without
masking identity numbers, descriptions of changes ignored a document's AI-off setting, and a
follow-up question could resend an earlier offline answer quoting a document kept from the AI.
