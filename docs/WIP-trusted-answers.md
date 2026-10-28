# Work in progress: answers you can check (paused 28 October 2026)

Branch `feature/trusted-answers`. Not merged; `main` is unchanged and green.

Direction chosen by the owner: mid-sized companies (100 to 2,000 people), self-hosted, known for
**search and answers people can trust**, with the pilot basics fixed along the way.

## Done on this branch

- **Benchmark** (`backend/eval/`): 224 questions over the demo company, with the asker's access,
  the expected sources and facts, and whether the right reply is an answer, a refusal or "not
  enough evidence". Includes prompt injections, typos, acronyms, conflicts and out-of-date
  documents. Split into dev (for tuning) and test (only reported). Run with
  `cd backend && python -m eval.run`.
- **Search** (`app/retrieval.py`, `app/search.py`, `app/text.py`): Porter stemming, typo and
  word-form matching, acronyms defined in documents, glossary phrases that count only as a
  whole, compound names like SDE-2, a reranking stage, and choosing the sentence that answers.
  Typo suggestions and acronyms only come from documents the person can open.
- **Evidence** (`app/assistant.py`, `app/facts.py`): answers lead with the evidence sentence and
  cite only supporting documents. When the evidence is too weak, the reply says so and names
  words no document mentions. Documents that disagree are flagged, and old documents are marked
  "possibly out of date". Source cards carry owner, update date, version, source and why the
  person can open the document.
- **Demo data**: two older policies that contradict current ones (Travel Policy 2024, Remote
  Work Guidelines).
- **Bug fix**: the scanner's "marked confidential" check fired on the word in passing. That made
  the Wi-Fi guide Confidential, hiding it from most staff. Found by the benchmark.

## Results so far (offline, no AI)

Held-out test split, before → after: Recall@5 0.984 → 1.000, MRR 0.937 → 0.992, answer accuracy
0.825 → 0.905, citation precision 0.712 → 0.960, abstaining when nothing answers it 0.17 → 1.00,
wrongly abstaining 0 → 0.06, conflicts found 0 → all, permission leaks 0 → 0.
Weights and the evidence threshold were tuned on dev only. After the first test run, the only
changes were fixes for failing unit tests (SDE-2 matching, heading credit, neighbouring sentences),
so **re-run the benchmark** before quoting numbers.

## Not done yet

1. Re-run the full backend suite. The last complete run had 3 failures: a document count,
   which is updated, the stemmer test, which is updated, and the Slack SDE-2 answer, which is
   fixed but not re-run.
2. Save `eval/baseline.json` and add `python -m eval.run --check` to CI.
3. Filters (owner, date, source) in the API, library and assistant. Optional embeddings
   (OpenAI-compatible endpoint, rank fusion), which can't be measured here because no model can
   be downloaded.
4. Pilot basics: session revocation (draft on `wip/governance-draft`), backups with a restore
   check, ClamAV scanning, Prometheus metrics, an AI data policy.
5. Interface: source facts, conflict and out-of-date warnings, the "not enough evidence" state,
   filters, sessions.
6. Docs (security overview, AI data policy, operations, roadmap), browser audit, PR, CI, merge.
