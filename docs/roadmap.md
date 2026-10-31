# Roadmap

VaultMind's aim: **the company search and answers people trust**, for companies of 100 to 2,000
people that run it on their own servers with their own infrastructure team. Each item below says
why it matters and how we'll know it's done. Answer quality is measured with the
answer-quality benchmark ([`backend/eval`](../backend/eval/)), so "better" means a number moved
on the held-out questions, not a feeling.

## Where it stands (31 October 2026)

Ready for a **pilot with one or a few teams** on one server: permission-aware search and
answers with evidence, documents that disagree and old documents flagged, sessions that can be
ended, checked backups, malware scanning, metrics and alerts, and an enforced AI data policy.
See [production-readiness.md](production-readiness.md) for what's verified and what isn't.

Benchmark, held-out questions, offline mode: Recall@5 1.00, answer accuracy 0.92, citation
precision 0.96, conflicts found 100%, wrongly abstaining 6%, permission leaks 0.

## Next: from a pilot to the whole company (about 90 days)

### 1. Run as several instances

**Why:** one process holds the search index, the rate limits and the scheduler, so it can't use more
than one machine, and a restart rebuilds the index from the database.

**What:** move search to PostgreSQL full-text search or OpenSearch (both are things an
infrastructure team already runs), rate limits to Redis, and syncs, notices and backups to a
separate worker process.

**Done when:** three app instances behind a load balancer pass the browser audit and the
benchmark, a rolling restart drops no requests, and 2,000 people's worth of load (about 20
questions a second at peak) keeps the slowest 5% of answers under 3 seconds offline.

### 2. Better answers to questions phrased differently from the documents

**Why:** the benchmark's weakest group is paraphrases (7 of 12 correct): "Do I need to disclose a
side project?" when the Code of Conduct talks about "outside work", or "Do team dinners need
approval?" when the policy says "team meals need prior manager approval". Keyword search with a
glossary only goes so far.

**What:** optional dense embeddings from an embeddings server the company runs (any
OpenAI-compatible endpoint, so nothing has to leave the network), fused with the current search
by reciprocal rank fusion. Embedded only for documents the AI data policy allows.

**Done when:** paraphrase accuracy on the held-out questions rises clearly (target 80%) with no
drop elsewhere and no new leaks. Not built yet because no model could be run to measure it, and
adding it unmeasured would add dependencies for nothing.

**Also:** one subject no document mentions next to words that do match ("dental cover for
parents" matched parental leave) is still answered from the wrong text. A simple rule to stop
that broke three correct answers on held-out questions and was reverted; embeddings, or a check
that the answer sentence mentions the subject, are the next things to try.

### 3. Real questions in the benchmark

**Why:** the benchmark's 229 questions were written against the demo company. A pilot's own
questions are the real test.

**What:** turn **Admin → Overview → "Asked, but nothing matched"** into a review queue where
administrators mark the right document (or "nothing answers this"), and add those as private
benchmark cases that run in the company's own CI.

**Done when:** a pilot has 200 reviewed questions of its own and a baseline for them.

### 4. Governance for a whole company

**Why:** as more teams join, the questions become "who can see what, and should they?".

**What:**
- **Access reviews:** documents shared more widely than their classification suggests, grants that
  have outlived their reason, people who moved teams but kept access, each with a one-click fix.
- **Retention and legal holds:** keep or delete by classification and age; holds that stop deletion.
- **Evidence for auditors:** a tamper-evident activity log (each entry chained to the previous one's
  hash) and exports of access decisions and AI use.
- **SCIM** provisioning from Okta, Entra ID or Google, and **SAML** sign-in.

**Done when:** an auditor can answer "who could open the salary bands on 1 March, and who did?"
from an export, and a quarterly access review takes an administrator under an hour.

## Later

- More sources: Confluence, Notion, Jira, Dropbox.
- Files in object storage (S3-compatible) with encryption at rest.
- Confirming the assistant's actions (request access, share) from Slack.
- Self-service password reset for companies not using company sign-in.
- A high-availability guide: PostgreSQL replicas, backups to object storage, recovery-time targets.
- A third-party penetration test before general availability.

## Not planned

- **A hosted version.** VaultMind is for companies that keep their documents on their own servers.
- **Training models on company documents.** Documents are searched and quoted, never used for training.
