# VaultMind

**An AI document assistant that only answers from the files you're cleared to read.**

Employees ask in plain English ("I'm new, what should I read first?", "what's the SDE-2 salary band?"). VaultMind finds the right documents for their role, answers with numbered sources that link to the exact paragraph, and checks their **team** and **position** before it reads a single page on their behalf. If a matching document exists that they can't open, it says so and offers to ask the people who own it.

It connects to where company files already live (Google Drive, SharePoint and OneDrive, or a shared folder) and keeps each file's sharing, so people see in VaultMind exactly what they could open there. People sign in with their Google or Microsoft account, get told when documents they read change, and can ask from Slack too.

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

**Answers people can trust**

- **Permission-aware AI assistant.** Retrieval runs only over documents the person may open, so the language model never sees anything they aren't cleared for. Answers cite their sources, each citation opens the document at the paragraph it came from, and conversations are saved.
- **Ask about one document.** Open a document and ask beside it: summarise it, list its key points, explain what it means for you (your team, role and level), say what changed in the latest version, or compare it with another document.
- **What changed.** Every new version is kept. The assistant answers "what changed in the travel policy?" or "what's new?", and the document page shows a sentence-by-sentence comparison of any two versions.
- **It speaks your company's language.** A glossary of the words people use for the same thing ("PTO", "time off", "leave") widens every search. A few common ones are built in, admins add their own, and when a search finds little the model suggests better search words (only the question is sent, never document text).
- **It acts only when you say so.** The assistant can offer to ask for access, share a document with a team, group or person, save an answer as a private document, or draft a message. Each is a card with a button; nothing happens until it's pressed, and permissions are checked again at that moment.
- **AI controls.** Admins and document owners can keep a document, or a whole source, away from the AI. ID numbers, card numbers and secrets are masked before any passage is sent.
- **Works without an API key.** With no key, an offline mode uses the same permission-filtered search and builds answers from the best-matching passages. Every answer says how it was made.

**Connected to the company**

- **Google Drive, Microsoft 365 and shared folders.** Files are synced on a schedule with the sharing they have at the source: named people, groups, the whole domain. Or use folder rules: each folder gets an owning team and a classification. Files deleted at the source disappear here.
- **People from the directory.** Optionally, people, teams (from departments) and groups are kept in step with Google Workspace or Microsoft Entra ID.
- **Sign in with Google or Microsoft.** OpenID Connect with PKCE, limited to your domains or tenant. Password sign-in can be switched off (admins keep it as a fallback).
- **Notifications.** In the app, by email, or in Slack: an access request to decide, a decision on yours, access about to expire, or a change to a document you read.
- **Slack.** `/vaultmind`, mentions and direct messages answer with the person's own permissions. Answers in channels are visible only to the person who asked.

**Access that's easy to follow**

- **Access by team and seniority.** Six positions (Intern → Executive) combine with four classifications (Public, Internal, Confidential, Restricted), plus sharing with other teams and groups.
- **"Why can't I open this?"** Every decision comes with a plain-language reason, e.g. *"This document needs Manager level or above; you are Intern."*
- **Access requests with approvals.** Requests go to people with authority over that document. Approved access is time-boxed, expires automatically, and can be revoked.
- **Sensitivity scanner.** On every upload and every synced file, it detects PAN, Aadhaar, card numbers, API keys, passwords, salary and finance language, and contact details. It suggests a classification and blocks under-classified uploads unless the uploader explicitly overrides, which is logged.

**For admins and new joiners**

- **Admin pages.** People (add, edit, import from a spreadsheet with a preview), teams, groups, connected sources with their sync health, the glossary, the activity log, and insights: what people ask, what found nothing, and documents that may be out of date.
- **First-week checklist.** New joiners get a short list (ask a question, read the starter documents for their team, see how access works) that ticks itself off as they go.
- **Role-aware recommendations**, a **command palette** (<kbd>Ctrl</kbd>/<kbd>⌘</kbd>+<kbd>K</kbd>), **light and dark themes**, and a **demo company** (Northwind Labs: 5 teams, 9 people, 26 documents including a synced shared folder) with one-click sign-in as each person.

---

## How access works

| Classification | Who can open it |
|---|---|
| **Public** | Everyone in the company |
| **Internal** | The owning team and teams it's shared with |
| **Confidential** | As internal, *and* at or above a minimum position (Manager by default) |
| **Restricted** | Only the uploader and people with an approved grant. Never shown to anyone else, not even as a title. |

Overrides, in order: admins see everything, uploaders keep access, an active grant opens a document, and executives see every non-restricted document company-wide. Sharing with a group works like sharing with a team.

**Synced files** follow their source. With *keep the sharing from the source*, a file from Google Drive or Microsoft 365 can be opened by exactly the people, groups and domains it's shared with there, and is invisible to everyone else (like in Drive). With *folder rules*, it follows the table above, using the team and classification of its folder.

All of this lives in one pure module, [`backend/app/policy.py`](backend/app/policy.py), with no database calls, so the rules are easy to read and unit-test.

## Architecture

```mermaid
flowchart LR
    U[Web app or Slack] -->|question| API[FastAPI]
    API --> P{Policy engine<br/>team · position · groups<br/>grants · source sharing}
    P -->|allowed doc ids| R[BM25 retriever<br/>+ glossary]
    R -->|passages from allowed docs only,<br/>AI reading on, IDs masked| LLM[Claude]
    P -->|discoverable but locked| H[Locked hints<br/>title + reason only]
    LLM --> A[Answer citing paragraphs]
    H --> A
    A -.->|proposed actions| C[Confirm button]
    API --> DB[(PostgreSQL / SQLite)]
    API --> S[(Content-addressed<br/>file storage)]
    SCH[Scheduler] -->|sync| SRC[Google Drive · Microsoft 365<br/>shared folders]
    SCH -->|notifications| N[Email · Slack]
    SRC -->|files, sharing, people| DB
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
**Quality:** pytest (167 tests, on SQLite and PostgreSQL, plus 4 opt-in live Claude checks; Google, Microsoft, Slack, SMTP and sign-in are tested against local fakes of their APIs), a 435-check browser audit run in Chromium, Firefox and WebKit (Playwright + axe), an accessibility review, performance checks, Ruff, pip-audit and npm audit, and a CI job that tests the Docker image itself

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
3. **People.** Create the first admin with the operator command (the password is prompted for, at least 12 characters):
   ```bash
   docker compose exec app python -m app.cli create-admin --email you@company.com --name "Your Name"
   ```
   Then add everyone else on **Admin → People** (one by one, or from a spreadsheet), or sync them from Google Workspace or Microsoft 365, and turn on *Sign in with Google / Microsoft*. Deactivating someone cuts them off at once; a password change doesn't end sessions that are already signed in (they last 8 hours).
4. **Files and integrations.** Connect Google Drive, Microsoft 365 or shared folders on **Admin → Sources**, and set up email and Slack if you want them. [`docs/integrations.md`](docs/integrations.md) has the steps for each, and the environment variables are listed in [`.env.example`](.env.example). Set `VAULTMIND_PUBLIC_URL` to the address people use.
5. **HTTPS.** Put it behind a reverse proxy that terminates TLS, then set `VAULTMIND_HSTS=true`. Publish the port only to the proxy (for example `127.0.0.1:8000:8000`), and set `FORWARDED_ALLOW_IPS` to the proxy's address so sign-in limits see each person's real address.
6. **One app process.** The search index, the rate limits and the background scheduler (syncs, reminders, email and Slack) live in the app process, so run one container with one worker (the image does). Scaling out needs a shared index, a Redis-backed limiter and a separate scheduler.
7. **Backups.** Back up the database and the file volume together:
   ```bash
   docker compose exec -T db pg_dump -U vaultmind vaultmind > vaultmind.sql
   docker compose cp app:/data/storage ./storage-backup
   ```
   Restore with `psql` into an empty database and copy the folder back, then restart. Database migrations run automatically on start, so upgrading is `git pull && docker compose up -d --build`; back up first.
8. **AI.** With `VAULTMIND_ANTHROPIC_API_KEY` set, passages from documents the asking person may open are sent to Anthropic to write the answer (with ID numbers and secrets masked). Make sure that fits your company's data policies, and switch AI reading off for any source or document that shouldn't be sent. Without a key, answers are built offline and labelled as such.

[`docs/production-readiness.md`](docs/production-readiness.md) has the full audit: what was tested, what was fixed, and what still needs checking before real use.

### Tests

```bash
cd backend
pytest                 # 167 tests: policy, scanner, search, API flows, leakage, security, migrations, AI errors,
                       # connectors, sync, sign-in, notifications, Slack, the assistant's actions and admin tools
ruff check app tests alembic && ruff format --check app tests alembic
VAULTMIND_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/vaultmind_test pytest   # same suite on PostgreSQL
```

### Browser audit

[`e2e/audit.js`](e2e/audit.js) drives the real app and checks **435 things**. CI runs it in Chromium, Firefox and WebKit, and again against the production Docker container:

- Every flow as six different people: sign-in (including wrong passwords, a corrupted session, and coming back to the page that was asked for), the assistant, paragraph citations, copy, Stop, conversations, confirming an access request from an answer, asking about one document, versions and what changed, the AI switch, notifications, the first-week checklist, the command palette, library search and filters, upload (drag-and-drop, unsupported and empty files, duplicates, the sensitivity override), downloads, access settings, delete with confirmation, approving, denying, cancelling and revoking requests, and the admin pages (people and import, teams, groups, sources, glossary, insights).
- Failure handling: the API going offline, a page whose code can't be downloaded (a reload is offered and the rest of the app keeps working), a failed chat message (the draft is kept), and rate limiting.
- Security and polish: the content security policy, self-hosted fonts, no team list for signed-out visitors, a 404 page, and a title for every page.
- Every page at 320, 360, 390, 768, 1024, 1280, 1440 and 1920 px, in light and dark: no horizontal overflow, readable headings, and touch targets of at least 24 px.
- An [axe](https://github.com/dequelabs/axe-core) WCAG 2 A/AA scan of every page, plus a check for any console error or failed request (requests the browser cancels because the audit moved on to the next page don't count).

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
5. Sign in as **Karan (Sales)**: the bell shows that the expense policy he read has changed. Open it to see exactly what changed.
6. Open any document and press **Ask about this document**, then try *Key points* and *What it means for me*. Click a citation number to jump to its paragraph.
7. As **Rohan**, ask *"Share the API Design Guidelines with Sales"*. Nothing happens until you press the button on the card.
8. Sign in as **Dev (Admin)** to see insights (what people ask, what found nothing), the synced demo drive on **Sources**, and the full activity log.

## Project structure

```
backend/
  app/
    policy.py        access rules: who can view, manage, approve (pure functions)
    assistant.py     permission-filtered retrieval + LLM / offline answers, one-document questions,
                     compare, what changed
    actions.py       actions the assistant offers, run only when confirmed
    glossary.py      company words and search expansion
    llm.py           Anthropic client, error classification, fallback reasons
    search.py        BM25 indexes over document chunks and titles
    recommend.py     explainable, role-aware suggestions
    sensitivity.py   PII / secret scanner, redaction, masking before the AI
    services.py      ingest pipeline: extract → scan → chunk → summarise → version → index
    versions.py      sentence-level diffs and change summaries
    connectors/      shared folder, Google Drive, Microsoft 365
    sync.py          syncing files, sharing, people and groups from a source
    scheduler.py     background syncs, reminders, email and Slack delivery
    sso.py           Sign in with Google / Microsoft (OpenID Connect)
    slack_bot.py     the Slack app: slash command, mentions, direct messages
    notify.py        in-app notifications; delivery.py sends email and Slack copies
    onboarding.py    first-week checklist
    files.py         safe file names and download headers
    routers/         auth, documents, assistant, access, notifications, sources, directory,
                     glossary, Slack, admin
    cli.py           operator commands: create-admin, create-user, set-password
    seed.py          demo company (and demo_drive/, its synced shared folder)
  alembic/           database migrations (run automatically on start)
  tests/
frontend/
  src/pages/         Landing, Login, Assistant, Library, Document, Requests, Admin (+ admin/ sections), NotFound
  src/components/    Shell, NotificationBell, Bubble, ActionCard, doc/ (ask panel, versions), CommandPalette,
                     RichText, icons, shared bits (avatars, tags, dialogs)
  src/theme.tsx      light / dark theme
e2e/
  audit.js           browser audit: flows, breakpoints, accessibility (Chromium, Firefox, WebKit)
  a11y-review.js     keyboard, focus, motion and text-spacing checks, accessibility-tree snapshots
  perf.js            slow-network loading and memory soak
  docker-smoke.sh    smoke test for a running deployment
docs/
  integrations.md           connecting Google, Microsoft, shared folders, email and Slack
  production-readiness.md   audit report and checklist
```

## What I'd build next

- Ending existing sessions when a password changes
- A shared search index, a Redis-backed rate limiter and a separate scheduler, so it can run as several instances
- Hybrid retrieval (BM25 + embeddings) with re-ranking
- More sources (Confluence, Notion, Dropbox) and Okta / generic SAML sign-in
- Confirming the assistant's actions from Slack, not only in the web app

## License

MIT
