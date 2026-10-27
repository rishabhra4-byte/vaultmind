# VaultMind

**An AI document assistant that only answers from the files you're cleared to read.**

Employees ask in plain English ("I'm new, what should I read first?", "what's the SDE-2 salary band?"). VaultMind finds the right documents for their role, answers with numbered sources, and checks their **team** and **position** before it reads a single page on their behalf. If a matching document exists that they can't open, it says so and lets them request access from the people who own it.

![CI](https://github.com/rishabhra4-byte/vaultmind/actions/workflows/ci.yml/badge.svg)

![VaultMind homepage](docs/landing.webp)

| An intern asks about salary bands | The engineering manager asks the same thing |
|---|---|
| ![Intern](docs/assistant-intern.webp) | ![Manager](docs/assistant-manager.webp) |

The intern gets nothing from the salary sheet. It is never even sent to the model. Instead they see that it exists, why they can't open it, and a button to request access.

| Library in dark mode: locked files are sealed, with the reason | <kbd>Ctrl</kbd>+<kbd>K</kbd> command palette |
|---|---|
| ![Library](docs/library-dark.webp) | ![Palette](docs/palette.webp) |

---

## Features

- **Permission-aware AI assistant.** Retrieval runs only over documents the user may open, so the language model never sees anything they aren't cleared for. Answers cite their sources, and conversations are saved.
- **Access by team and seniority.** Six positions (Intern → Executive) combine with four classifications (Public, Internal, Confidential, Restricted), plus sharing with other teams.
- **"Why can't I open this?"** Every decision comes with a plain-language reason, e.g. *"This document needs Manager level or above; you are Intern."*
- **Access requests with approvals.** Requests go to people with authority over that document: owning-team managers at or above its level, or directors for restricted files. Approved access is time-boxed and expires automatically, and it can be revoked.
- **Role-aware recommendations.** "Suggested for you" scores documents by team, role keywords, what colleagues are reading, and freshness, and shows the reasons behind each suggestion.
- **Sensitivity scanner on upload.** Detects PAN, Aadhaar, card numbers (with a Luhn check), API keys, passwords, salary and finance language, and contact details. It suggests a classification and blocks under-classified uploads unless the uploader explicitly overrides, which is logged. Auto-summaries are redacted.
- **Audit log and admin dashboard.** Logins, views, refused opens, downloads, assistant questions, uploads, approvals and role changes are all recorded.
- **Works without an API key.** With no key, an offline mode uses the same permission-filtered search and builds answers from the best-matching passages. With an Anthropic key, Claude writes the answers.
- **Command palette.** Press <kbd>Ctrl</kbd>/<kbd>⌘</kbd>+<kbd>K</kbd> anywhere to search every document you can see, jump to a page, or send a question straight to the assistant.
- **Light and dark themes.** Follows your system setting until you pick one, then remembers it, with no flash on load.
- **Built to be used, not just screenshotted.** Typing-style answers, a copy button, drag-and-drop upload, filter chips with live counts, search highlighting, loading skeletons, helpful empty and error states, keyboard shortcuts (<kbd>/</kbd> to search), and a skip link for keyboard users.
- **Demo company included.** Northwind Labs comes with 5 teams, 9 people across every position, and 20 realistic documents. One-click sign-in as each person lets you compare what they see.

---

## How access works

| Classification | Who can open it |
|---|---|
| **Public** | Everyone in the company |
| **Internal** | The owning team and teams it's shared with |
| **Confidential** | As internal, *and* at or above a minimum position (Manager by default) |
| **Restricted** | Only the uploader and people with an approved grant. Never shown to anyone else, not even as a title. |

Overrides, in order: admins see everything, uploaders keep access, an active grant opens a document, and executives see every non-restricted document company-wide.

All of this lives in one pure module, [`backend/app/policy.py`](backend/app/policy.py), with no database calls, so the rules are easy to read and unit-test.

## Architecture

```mermaid
flowchart LR
    U[User] -->|question| API[FastAPI]
    API --> P{Policy engine<br/>team · position · grants}
    P -->|allowed doc ids| R[BM25 retriever]
    R -->|passages from allowed docs only| LLM[Claude]
    P -->|discoverable but locked| H[Locked hints<br/>title + reason only]
    LLM --> A[Answer with citations]
    H --> A
    API --> DB[(PostgreSQL / SQLite)]
    API --> S[(Content-addressed<br/>file storage)]
    API --> L[(Audit log)]
```

### The security idea

Many "chat with your documents" apps put everything in the prompt and ask the model not to reveal it. That fails as soon as someone writes "ignore your instructions". VaultMind enforces access **before** retrieval:

1. The policy engine computes which documents this user may open.
2. The retriever scores passages **only from those documents**.
3. The model gets just those passages, wrapped as data, and is told to ignore any instructions inside them.
4. Any citation the model makes to a document outside its context is stripped.

A prompt-injection test checks this: an intern who claims to be the CEO and asks for the salary sheet gets a prompt that doesn't contain a single salary figure ([`tests/test_api.py`](backend/tests/test_api.py)).

Search follows the same rule. Content matching only ever runs over documents the person can open; locked documents can be found by title alone, so searching for a phrase can't reveal what a locked file says.

### Hardening

- Strict security headers and content security policy (no inline scripts), JSON 404s for unknown API routes, and request size limits enforced before parsing.
- Sign-in limits per account and per network address that count only failed attempts, the same answer and timing for unknown accounts, and input length limits.
- Production refuses to start with a missing or weak signing secret, and demo data is off unless asked for.
- Uploads: file names are sanitised, downloads are always served as attachments with a safe content type, and zip bombs, oversized PDFs and password-protected files get clear refusals.
- Requests and approvals are safe under concurrency (a unique index and compare-and-set), and files are deleted from storage when the last document using them goes.
- AI errors (bad key, no credit, rate limits, overload, timeouts) fall back to an offline answer that says why, and the API key never reaches the browser.

### Design decisions

- **BM25 over embeddings.** It needs no model download or vector database, it's fast and explainable, and it's strong on the keyword-heavy queries people type when looking for a company file. It sits behind a small `add / remove / search` interface, so a hybrid or embedding retriever can replace it later.
- **Content-addressed storage.** Files are stored by SHA-256, so identical uploads share one blob and duplicates are detected. Moving to S3 means replacing three functions.
- **Discoverability is a per-document choice.** Showing a locked title helps people find the right owner, but restricted documents never leak even their existence. Unknown and undiscoverable documents both return `404`.

## Tech stack

**Backend:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL (SQLite for local runs), JWT auth with bcrypt, Anthropic API, pypdf
**Frontend:** React 18, TypeScript, Vite, React Router, with no UI framework, self-hosted fonts
**Quality:** pytest (87 tests, on SQLite and PostgreSQL, plus 4 opt-in live Claude checks), a 274-check browser audit run in Chromium, Firefox and WebKit (Playwright + axe), an accessibility review, performance checks, Ruff, pip-audit and npm audit, and a CI job that tests the Docker image itself

## Run it

### Quickest: Docker

```bash
cp .env.example .env                                         # optional: add VAULTMIND_ANTHROPIC_API_KEY
echo "VAULTMIND_JWT_SECRET=$(openssl rand -hex 32)" >> .env  # required: the image runs in production mode
echo "VAULTMIND_SEED_DEMO_DATA=true" >> .env                 # the demo company (never with real documents)
docker compose up --build
```

Open http://localhost:8000, click **Try the demo**, and pick a person to sign in as. Every demo password is `demo1234`.

### Local development

```bash
# API on :8000
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload

# Web app on :5173 (proxies /api to :8000)
cd frontend
npm install
npm run dev
```

The first start creates a SQLite database and loads the demo company. Interactive API docs are at http://localhost:8000/docs (development only; they are switched off in production).

### Deploying for real

The Docker image runs in production mode. On top of the Quickest steps:

1. **Secrets.** `VAULTMIND_JWT_SECRET` must be at least 32 random characters, or the app refuses to start. Set your own `POSTGRES_PASSWORD` too, before the first start (PostgreSQL only reads it when it creates the database).
2. **No demo data.** Leave `VAULTMIND_SEED_DEMO_DATA` out. The demo accounts share a public password and one of them is an admin.
3. **People.** Create the first admin, then everyone else, with the operator commands (passwords are prompted for, at least 12 characters):
   ```bash
   docker compose exec app python -m app.cli create-admin --email you@company.com --name "Your Name"
   docker compose exec app python -m app.cli create-user --email ravi@company.com --name "Ravi Kumar" \
       --team Engineering --position senior --title "Backend Engineer"
   docker compose exec app python -m app.cli set-password --email ravi@company.com
   ```
   Positions and active status can then be changed on the admin page. Deactivating someone cuts them off at once; a password change doesn't end sessions that are already signed in (they last 8 hours).
4. **HTTPS.** Put it behind a reverse proxy that terminates TLS, then set `VAULTMIND_HSTS=true`. Publish the port only to the proxy (for example `127.0.0.1:8000:8000`), and set `FORWARDED_ALLOW_IPS` to the proxy's address so sign-in limits see each person's real address.
5. **One app process.** The search index and the rate limits live in memory, so run one container with one worker (the image does). Scaling out needs a shared index and a Redis-backed limiter.
6. **Backups.** Back up the database and the file volume together:
   ```bash
   docker compose exec -T db pg_dump -U vaultmind vaultmind > vaultmind.sql
   docker compose cp app:/data/storage ./storage-backup
   ```
   Restore with `psql` into an empty database and copy the folder back, then restart. Database migrations run automatically on start, so upgrading is `git pull && docker compose up -d --build`; back up first.
7. **AI.** With `VAULTMIND_ANTHROPIC_API_KEY` set, passages from documents the asking person may open are sent to Anthropic to write the answer. Make sure that fits your company's data policies. Without a key, answers are built offline and labelled as such.

[`docs/production-readiness.md`](docs/production-readiness.md) has the full audit: what was tested, what was fixed, and what still needs checking before real use.

### Tests

```bash
cd backend
pytest                 # 87 tests: policy rules, scanner, search, API flows, leakage, security, migrations, AI provider errors
ruff check app tests alembic && ruff format --check app tests alembic
VAULTMIND_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/vaultmind_test pytest   # same suite on PostgreSQL
```

### Browser audit

[`e2e/audit.js`](e2e/audit.js) drives the real app and checks **274 things**. CI runs it in Chromium, Firefox and WebKit, and again against the production Docker container:

- Every flow as six different people: sign-in (including wrong passwords and a corrupted session), the assistant, citations, copy, Stop, conversations, the command palette, library search and filters, upload (drag-and-drop, unsupported and empty files, duplicates, the sensitivity override), downloads, access settings, delete with confirmation, approving, denying, cancelling and revoking requests, and the admin tools.
- Failure handling: the API going offline, a failed chat message (the draft is kept), and rate limiting.
- Security and polish: the content security policy, self-hosted fonts, no team list for signed-out visitors, a 404 page, and a title for every page.
- Every page at 320, 360, 390, 768, 1024, 1280, 1440 and 1920 px, in light and dark: no horizontal overflow, readable headings, and touch targets of at least 24 px.
- An [axe](https://github.com/dequelabs/axe-core) WCAG 2 A/AA scan of every page, plus a check for any console error or failed request.

[`e2e/a11y-review.js`](e2e/a11y-review.js) covers what axe can't: keyboard-only use with a visible focus ring, focus trapping and return in dialogs, focus never hidden behind sticky parts, reduced motion, and text spacing, and saves accessibility-tree snapshots to read. [`e2e/perf.js`](e2e/perf.js) measures loading on a slow 3G connection and checks for memory leaks over 45 rounds of use.

```bash
cd frontend && npm ci && npm run build && cp -r dist ../backend/static
cd ../backend && rm -rf vaultmind.db storage && uvicorn app.main:app   # fresh demo data
cd ../e2e && npm ci && npx playwright install chromium
npm run audit            # BROWSER=firefox or BROWSER=webkit for the others
npm run a11y && npm run perf
```

## Things to try

1. Sign in as **Aarav (Intern)** and ask *"What are the salary bands for SDE-2?"*, then sign in as **Rohan (Engineering Manager)** and ask the same.
2. As Aarav, ask *"How do we handle a production incident?"*. The runbook is locked to Associate and above, so request access.
3. As **Rohan**, open **Access requests** and approve it. Aarav can now open the runbook for the number of days you chose.
4. Upload a text file containing a PAN number and mark it **Public**. The scanner stops you.
5. Sign in as **Dev (Admin)** to see refused opens, assistant questions and the full activity log.

## Project structure

```
backend/
  app/
    policy.py        access rules: who can view, manage, approve (pure functions)
    assistant.py     permission-filtered retrieval + LLM / offline answers
    llm.py           Anthropic client, error classification, fallback reasons
    search.py        BM25 indexes over document chunks and titles
    recommend.py     explainable, role-aware suggestions
    sensitivity.py   PII / secret scanner and redaction
    services.py      ingest pipeline: extract → scan → chunk → summarise → index
    files.py         safe file names and download headers
    routers/         auth, documents, assistant, access requests, admin
    cli.py           operator commands: create-admin, create-user, set-password
    seed.py          demo company
  alembic/           database migrations (run automatically on start)
  tests/
frontend/
  src/pages/         Landing, Login, Assistant, Library, Document, Requests, Admin, NotFound
  src/components/    Shell, CommandPalette, RichText, icons, shared bits (avatars, tags, dialogs)
  src/theme.tsx      light / dark theme
e2e/
  audit.js           browser audit: flows, breakpoints, accessibility (Chromium, Firefox, WebKit)
  a11y-review.js     keyboard, focus, motion and text-spacing checks, accessibility-tree snapshots
  perf.js            slow-network loading and memory soak
  docker-smoke.sh    smoke test for a running deployment
docs/
  production-readiness.md   audit report and checklist
```

## What I'd build next

- SSO (Google Workspace / Okta), syncing people and teams from an HR system, and managing people in the admin page
- Ending existing sessions when a password changes
- A shared search index and a Redis-backed rate limiter, so it can run as several instances
- Group and per-project permissions on top of teams
- Hybrid retrieval (BM25 + embeddings) with re-ranking
- Email or Slack notifications for access requests

## License

MIT
