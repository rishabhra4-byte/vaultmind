# VaultMind production-readiness audit

**Date:** 27 October 2026, with the company features added on 28 October ([section G](#g-company-features-28-october-2026)) · **Code audited:** `main`, commits `3304ed3` to `a9a292e`, then the `wip/company-features` branch · **Scope:** functionality, AI integration, security, accessibility (WCAG 2.2 AA), browsers and devices, Docker and deployment, fonts and design, performance, data and reliability, testing and CI, SEO and maintainability.

Every test result below comes from actually running the test. Anything that could not be run says so, with the reason and the steps to finish it.

## Verdict

**Ready with limitations.**

- **Ready** for a public demo (demo data on) and for a pilot with one team, run as a single container behind HTTPS.
- **Not yet ready** to be a company's main document system. Before that, it needs:
  - a test with a real Anthropic key (`tests/test_live_anthropic.py` does it in one command)
  - each connector, the company sign-in and the Slack app tried against the real Google, Microsoft and Slack services (they are tested only against local stand-ins of their APIs, see [G4](#g4-still-unverified))
  - a manual pass with a real screen reader and on real phones
  - sessions that end when a password changes
  - scheduled backups
  - a written decision on sending document passages to Anthropic

The details are in [F](#f-release-recommendation) and [G](#g-company-features-28-october-2026). Sections A to F describe the audit of 27 October; the open risk R5 there (people managed only from the command line, no SSO) has since been addressed by the company features.

---

## A. Critical security and data-loss risks

### Found and fixed

| # | Risk | Severity | Fix | Proven by |
|---|---|---|---|---|
| A1 | **Production could run with a default signing secret.** Anyone who knew the placeholder could forge an admin token. | Critical | In production the app refuses to start unless the secret is at least 32 characters and not a known placeholder. In development it generates a random one. | `test_production_refuses_a_missing_or_weak_secret`; CI Docker step "refuses to start without a secret" |
| A2 | **Demo accounts in every deployment.** These are nine accounts with a published password, one of them an admin. | Critical (real installs) | Demo data is off by default in production. `.env.example` no longer turns it on, and startup warns if it is on in production. | `test_demo_data_is_off_in_production_unless_asked_for` |
| A3 | **Search could read locked documents.** Content search ran over locked-but-listed files, so searching "22 to 34" showed whether the salary sheet contains it. | High | Content matching runs only over documents the person can open. Locked documents match on title only, through a separate title index. | `test_search_cannot_probe_the_contents_of_locked_documents` |
| A4 | **The assistant's locked hints leaked the same way.** | High | The hints use title-only matching with a minimum score. | `test_assistant_locked_hints_match_titles_not_contents` |
| A5 | **Locked rows showed tags and sensitivity flags generated from their content.** | Medium | Locked documents carry no summary, tags or flags. | `test_locked_documents_hide_content_derived_metadata` |
| A6 | **Unlimited password guessing.** | High | 10 failed attempts per account and 50 per network address in 5 minutes. Unknown accounts get the same answer and timing, and input length is limited. Only failures count, so an office behind one address can't lock itself out. | `test_login_is_rate_limited_per_account`, `…_per_address`, `test_successful_sign_ins_do_not_use_up_the_limit`, `test_login_gives_the_same_answer_for_unknown_accounts` |
| A7 | **Downloads trusted the uploader's content type and file name.** Downloads went out with whatever type the uploader's browser claimed (for example `text/html`), without `nosniff`. Only the attachment header kept browsers from rendering them. A file named outside Latin-1 (in Hindi, say) made the download fail with a server error. | Medium | Content types come from an allow-list, everything else is `application/octet-stream`. Files are always sent as attachments with `nosniff`. Names are sanitised, with an RFC 6266 UTF-8 file name. | `test_downloads_never_use_the_uploaders_content_type`, `test_hostile_file_names_are_sanitised`, `test_files_named_in_any_language_download` |
| A8 | **Known-vulnerable dependencies.** pypdf had 18 advisories; Vite and React Router had advisories. | High | All upgraded. | `pip-audit` and `npm audit` report 0, both run in CI |
| A9 | **No security headers or CSP.** Unknown `/api/...` URLs returned the app's HTML with status 200. | Medium | Added a strict CSP (no inline script or style, no `data:` fonts), `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, COOP, and optional HSTS. Unknown API routes return a JSON 404. | `test_security_headers_on_api_responses`, `test_frontend_gets_a_strict_csp…`, `test_unknown_api_routes_are_json_404s`, browser audit "csp" check |
| A10 | **Unbounded request bodies.** Memory could be exhausted before validation ran. | Medium | Size limit enforced while the body streams in. | `test_request_bodies_over_the_limit_are_refused_before_parsing` |
| A11 | **Hostile uploads.** A DOCX zip bomb, a huge PDF, or an encrypted or damaged PDF caused crashes or leaked parser errors. | Medium | Size, page and text limits, and plain-language refusals. | `test_docx_zip_bomb_is_rejected`, `test_password_protected_pdf…`, `test_damaged_pdf…` |
| A12 | **Upgrades would have lost data.** The schema was created with `create_all`, with no migrations. | High (data loss on upgrade) | Alembic migrations run at startup. Databases from version 1.0 are detected and stamped, then upgraded in place. | `test_migrations_build_the_same_schema_as_the_models`, `test_databases_from_version_1_are_upgraded_in_place` |
| A13 | **Races in access requests.** Two approvers could both decide one request, and duplicate pending requests were possible. | Medium (integrity) | A unique partial index, plus compare-and-set on decisions (the loser gets 409). | `test_two_approvers_cannot_both_decide_the_same_request`, `test_database_allows_only_one_pending_request…` |
| A14 | **Deleted documents left their files on disk forever.** | Medium (data retention) | The stored file is removed when the last document using it is deleted. Shared files survive. | `test_deleting_the_last_copy_removes_the_stored_file`, `test_shared_blob_survives…` |
| A15 | **Scanning sent the whole file to the AI before the person chose to upload.** With a key set, it also showed an unredacted summary. | Medium (privacy) | The scan preview is local and redacted. The AI summary is made only after upload, with a 10-second limit. | `test_scanning_a_file_before_upload_never_sends_it_to_the_model`; upload flows in the browser audit |
| A16 | **A link could make a signed-in person ask a question.** `/?ask=…` auto-sent the question, which then appeared in their history and audit log. | Low | Questions from the palette and document pages are passed in navigation state, which an external link can't set. | Browser audit "a link with ?ask= does not send a question", plus the palette and document-page ask checks |
| A17 | **Signed-out visitors could list every team.** | Low | The team list requires sign-in. | `test_team_list_needs_sign_in`, `docker-smoke.sh` |

### Still open (not fixed)

| # | Risk | Severity | Why it's still open | Mitigation now |
|---|---|---|---|---|
| R1 | **Sessions can't be revoked.** Sessions are 8-hour JWTs kept in `localStorage`, and changing a password doesn't end them. | Medium | Needs a session table or a token-version column (a schema change). | Deactivating an account cuts it off on the next request (`test_deactivating_someone_ends_their_signed_in_session`). A strict CSP and no `innerHTML` reduce the chance of an XSS stealing a token. |
| R2 | **Single process only.** The search index and the rate limits are held in memory. | Medium (operations) | Scaling out needs a shared index and Redis. | The image runs one worker, and the README says not to scale out. |
| R3 | **Uploads aren't scanned for malware.** | Medium | Needs an external scanner such as ClamAV. | Files are never rendered inline: they are served as attachments with an allow-listed type and `nosniff`. |
| R4 | **With a key set, document passages go to Anthropic.** | Policy | This is a data-processing decision for the company, not a code fix. | Only passages the asking person may open are sent. The README says this plainly. |
| R5 | **People are managed only from the command line.** There is no self-service password reset and no SSO. | Functional gap | *Addressed on 28 October:* people are now added, edited and imported on the admin page, can be synced from Google or Microsoft, and can sign in with Google or Microsoft (G1). There is still no self-service password reset for password accounts. | `create-admin`, `create-user` and `set-password` operator commands, which are tested. |
| R6 | **Backups are manual.** | Data loss if neglected | Scheduling depends on where it's hosted. | The procedure is in the README and was drilled locally. |
| R7 | **Default PostgreSQL password.** If `POSTGRES_PASSWORD` isn't set, it is `vaultmind`. | Low | The database isn't published outside the Compose network. | The README and Compose comments say to set it before the first start. |

---

## B. Bugs found and fixed

| Area | Bug | Fix |
|---|---|---|
| AI | Any provider failure (bad key, no credit, rate limit, overload, timeout, network) became a generic error. | Errors are classified. The answer falls back to an offline one that says why. A rejected key backs off for 10 minutes instead of failing every question. Empty and truncated responses are handled. |
| AI | Answers didn't say whether AI wrote them. | Each answer is labelled "Written by Claude…" or "Offline answer: matching passages, no AI". |
| AI | A slow answer couldn't be cancelled. | A Stop button aborts the request and puts the question back in the box. |
| AI | The upload summary could hold an upload for the full model timeout and its retries. | It is limited to 10 seconds with no retries, then falls back to a local summary. |
| Fonts | Fonts came from Google Fonts: third-party requests that the CSP blocks. | Fonts are self-hosted with `@fontsource` and `font-display: swap`. |
| Fonts | Vite inlined small font files as `data:` URIs, which the CSP blocked. The browser audit found this. | `assetsInlineLimit: 0`, so every font is a cached file. |
| Layout | The landing navigation overflowed by 23px at 320px wide. | The duplicate call to action is hidden in the nav under 360px. |
| Performance | The sign-in page jumped as data loaded on phones (layout shift 0.44, "poor"). | The page renders once its data has arrived. Layout shift is now 0, at the cost of about 0.4s of first paint on slow 3G for direct visits. |
| Accessibility | Closing the command palette or a dialog with Esc dropped focus to the page body. | Focus returns to the control that opened it. |
| Accessibility | Headings in document previews were exposed as paragraphs (WCAG 1.3.1). | They are real heading elements, nested under the page title. |
| Accessibility | The assistant page lost its heading once a chat started, and messages had no speaker. | Added a page heading plus "You asked" and "VaultMind answered" labels for screen readers. |
| Accessibility | The sign-in page had no `main` landmark. The hero illustration was read as fragmented text. Eight buttons were all called "Request access". | Added the landmark. The illustration is now a described image. Buttons are named "Request access to <title>", and locked rows say so. |
| Accessibility | The sign-in error wasn't announced, and the palette didn't expose the active option. | `role="alert"`; `aria-activedescendant`. |
| Navigation | No 404 page, every tab had the same title, and signed-out deep links misbehaved. | A not-found page and per-page titles. Signed-out links go to sign-in. A non-admin opening `/admin` gets "not found". |
| Data | After A17 made the team list sign-in-only, the list was missing after signing in, because metadata was only fetched once. | Metadata is fetched again when the signed-in person changes. |
| Data | The activity filter treated `%` and `_` as wildcards. | Escaped. |
| Reliability | No readiness check. Stale database connections after a PostgreSQL restart. No clean shutdown. | `/api/ready` checks the database; the container health check uses it. `pool_pre_ping`. Graceful shutdown with a 20-second drain. |
| Reliability | The rate limiter kept every key it had ever seen. | Idle keys are pruned. |
| SEO | No social preview, no `robots.txt`, no favicon, and nothing without JavaScript. | Open Graph and Twitter tags with a real 1200×630 screenshot, `robots.txt`, an SVG favicon, `<noscript>` text, and a meta description. |

---

## C. Tests run and their results

| Suite | Where | Result |
|---|---|---|
| Backend: pytest, SQLite | Local, Python 3.13 | **89 passed**, 4 live tests skipped (92s) |
| Backend: pytest, PostgreSQL 16 | Local, PostgreSQL 16.15 | **89 passed**, 4 live tests skipped (94s) |
| Backend: pytest, SQLite and PostgreSQL | GitHub Actions, Python 3.12 | **passed** (both jobs, every run) |
| Live Claude API (`test_live_anthropic.py`, 4 tests) | — | **skipped: no API key available** (see D1) |
| Lint and format (`ruff check`, `ruff format --check`) | Local and CI | **pass** |
| `pip-audit -r requirements.txt` | Local and CI | **No known vulnerabilities** |
| `npm audit --audit-level=high` | Local and CI | **0 vulnerabilities** |
| TypeScript (`tsc --noEmit`) and production build | Local and CI | **pass**. The CI step that greps the bundle for secrets passes. |
| Browser audit, Chromium 153 (`audit.js`) | Local | **274/274 checks passed, 0 console errors or failed requests** |
| Browser audit, Chromium, Firefox and WebKit | GitHub Actions, final commits `2178331` and `a9a292e` | **All three passed on both.** On `a9a292e`, Firefox needed one retried page load (`/` at 1440px, dark); `2178331` needed none |
| Docker runtime job | GitHub Actions, every run | **passed, every step.** It built the image, refused to start with no secret, became healthy, passed the smoke test, passed the full browser audit against the container (production mode, PostgreSQL), kept its data across a restart, logged no errors, and shut down gracefully |
| Accessibility review (`a11y-review.js`) | Local, Chromium | **16/16 passed.** Covers a keyboard-only sign-in with visible focus, the palette and dialog focus trap and return, focus never hidden behind sticky parts at 390 and 1280px, reduced motion, text spacing (WCAG 1.4.12), and accessible names |
| axe WCAG 2 A/AA scan, every page, light and dark, 390 and 1440px | Local and CI (inside `audit.js`) | **0 violations** |
| Lighthouse 12.8.2, landing page | Local | Mobile: Performance **98**, Accessibility **100**, Best practices **100**, SEO **100**. Desktop: 100/100/100/100 |
| Lighthouse 12.8.2, sign-in page | Local | Mobile: **99**/100/100/100. Desktop: 100/100/100/100 |
| Slow 3G loading (`perf.js`) | Local | Landing: first paint 2.8s, 142 KB, layout shift 0.056. Sign-in: first paint 3.2s, layout shift **0** (was 0.441). The library shows a loading state, then its content after 0.8s |
| Memory soak, 45 rounds of every page plus the palette | Local | Heap grew **0.11 MB**. DOM nodes (559) and listeners (198) unchanged. No leak |
| Docker smoke test (`docker-smoke.sh`) against a production-mode server | Local, without Docker | **6/6** |
| Backup and restore drill | Local, PostgreSQL 16 | **Passed.** `pg_dump` plus a copy of the file storage was restored into an empty database. The uploaded document was found by search, its download was byte-identical, and a pending request survived |
| Migrations on PostgreSQL | Local | Upgrade to head, no drift from the models, downgrade to base |
| Real Anthropic SDK against a fake API server (`test_llm_provider.py`) | Local and CI | **Passed.** Covers success, 401, 429, 529, 500, no credit, timeout, unreachable, key cool-down, empty and truncated replies |

### Every CI run during the audit

Nothing here has been filtered out. In every run where the browser audit got to the end, the app passed every functional check in every browser. Every failure was in the test setup, not the app.

| Commit | What changed | Backend ×2 | Frontend | Chromium | Firefox | WebKit | Docker |
|---|---|---|---|---|---|---|---|
| `3304ed3` | The hardening work | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `7541808` | Report, operator commands, sign-in limits | ✅ | ✅ | ✅ | ❌ run stopped early (cause hidden, see next row) | ✅ | ✅ |
| `0e44a2b` | Audit records a failing block and carries on; failures become annotations | ✅ | ✅ | ✅ | ❌ loading `/robots.txt` never reported "load" | ✅ | ✅ |
| `bd811d9` | Sweep stops loading `/robots.txt` | ✅ | ✅ | ✅ | ❌ one load of `/` (1024px, dark) never reported "load"; all other checks passed | stalled installing system packages, cancelled | ✅ |
| `16747f6` | Such a load is retried once and listed as a note | ✅ | ✅ | ✅ | ✅ | stalled installing, cancelled | ✅ |
| `af43ac2` | Pinned runner image, job time limits, install retries | ✅ | ✅ | ❌ the retries interrupted apt and broke it | ✅ | ✅ | ✅ |
| `2178331` | System packages installed only when a browser needs them | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `326a541` | Test that scanning never calls the model | ✅ | ✅ | cancelled, superseded by the next commit | cancelled | cancelled | cancelled |
| `a9a292e` | Test for file names in any language; report wording | ✅ | ✅ | ✅ | ✅ (one load retried) | ✅ | ✅ |

**What this shows:**

- **Firefox under automation sometimes never reports a page's `load` event.** This happened in four runs, each time on a different page; the last one was handled by the retry. It never happened in Chromium or WebKit, and the same pages loaded fine everywhere else in those runs. The cause is not established. One guess, not confirmed, is the browser switching processes on a page sent with `Cross-Origin-Opener-Policy`.
  - The audit now retries such a load once and reports it as a note and a CI warning. A second timeout still fails.
  - Of the last two full runs, one needed no retries and one needed a single retry.
  - Nothing points to real Firefox users being affected: the same pages loaded normally on every other attempt. D3 (real devices) still applies.
- **WebKit's system packages sometimes took very long to install from the Ubuntu mirrors**: 26 minutes once, and two installs stalled until I cancelled them. CI now pins `ubuntu-24.04`, caches the browsers, gives every job a time limit, and only runs apt when a browser can't start without it.

---

## D. Still unverified, and why

| # | Item | Status | Why | How to finish it |
|---|---|---|---|---|
| D1 | **Real Claude API** | Blocked | No API key was provided. All AI behaviour is tested against a fake model and a fake HTTP server running the real SDK. | `cd backend && VAULTMIND_LIVE_ANTHROPIC_KEY=sk-ant-… pytest tests/test_live_anthropic.py -v`. This checks the key and model, bad-key handling, that a manager gets a cited AI answer, and that an intern's prompt-injection attempt gets no salary figure. Then sign in as Aarav and as Rohan and ask the salary question. |
| D2 | **Safari itself** (macOS and iOS) | Not tested | The CI matrix runs Playwright's WebKit build on Linux, which passes the full audit. It is the same engine, but not the same browser as Safari on Apple systems (system fonts, scrolling, form controls). | On a Mac: `BROWSER=webkit npm run audit`, then a manual pass in Safari, as in D3. |
| D3 | **Real phones** (iOS and Android) | Not tested | No physical devices. Phone widths were checked by emulating viewports (320–430px) in Chromium, and WebKit runs in CI. | Open the demo on an iPhone (Safari) and an Android phone (Chrome). Sign in by tapping a person, ask a question, upload a file, and open the command palette from the search button. |
| D4 | **Screen readers** (VoiceOver, NVDA, TalkBack) | Not tested | No assistive technology here. Covered instead by axe, the accessibility review, and a manual read of the accessibility-tree snapshots (`e2e/shots/a11y-tree/`, produced by `npm run a11y`), which led to five fixes. | With VoiceOver (macOS: Cmd+F5) or NVDA (Windows): sign in from the persona list, ask a question, and check the answer is announced once it finishes. Move by headings on a document page. Open and close the upload dialog and the palette. |
| D5 | **Docker on your own machine** | Verified in CI, blocked here | This environment can't download images from any registry, so the image was built and tested by the CI Docker job instead. | `docker compose up --build`, then `bash e2e/docker-smoke.sh http://localhost:8000`. |
| D6 | **Fonts on a real production domain** | Partly verified | Fonts are self-hosted and served from `/assets` with long-lived caching. The audit checks they load with no third-party requests under the CSP, in Chromium, Firefox and WebKit, locally and in the container. Not checked: a CDN or proxy in front of the app. | After deploying: open DevTools → Network → Font, and confirm the `.woff2` files come from your domain with `Cache-Control: immutable`. |
| D7 | **Load and concurrency at company scale** | Not tested | No load-testing infrastructure. Concurrency safety of requests and approvals is tested; throughput is not. | For example, `hey -n 2000 -c 50 -H "Authorization: Bearer …" https://host/api/documents`. Watch p95 latency and memory. Search is in-process BM25, rebuilt at startup. |
| D8 | **Backups and restore in Docker Compose** | Procedure drilled without Docker | Same image-registry block as D5. | Run the README backup commands against a Compose install, restore into a fresh one, then run `docker-smoke.sh … --after-restart`. |

---

## E. Commands to reproduce

```bash
# Backend: unit, API, security, migration and provider tests
cd backend
pip install -r requirements-dev.txt
pytest                                              # SQLite
VAULTMIND_DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5432/vaultmind_test pytest
ruff check app tests alembic && ruff format --check app tests alembic
pip install pip-audit && pip-audit -r requirements.txt
VAULTMIND_LIVE_ANTHROPIC_KEY=sk-ant-... pytest tests/test_live_anthropic.py -v   # needs a real key

# Frontend: dependency audit, type check, production build
cd ../frontend
npm ci && npm audit --audit-level=high && npm run build
cp -r dist ../backend/static

# Browser checks need a fresh server with demo data (the audit changes data)
cd ../backend && rm -rf vaultmind.db storage && VAULTMIND_SEED_DEMO_DATA=true uvicorn app.main:app --port 8000
cd ../e2e && npm ci && npx playwright install chromium firefox webkit
npm run audit                           # Chromium: flows, 8 widths × 2 themes, axe
BROWSER=firefox npm run audit           # restart the server with a fresh database between runs
BROWSER=webkit npm run audit
npm run a11y                            # keyboard, focus, motion, text spacing; writes shots/a11y-tree/
npm run perf                            # slow 3G loading and a memory soak (Chromium)
npx lighthouse http://localhost:8000/ --only-categories=performance,accessibility,best-practices,seo

# A running deployment (Docker or not)
bash e2e/docker-smoke.sh http://localhost:8000
bash e2e/docker-smoke.sh http://localhost:8000 --after-restart   # after the audit and a restart

# Docker image and the full container check (what the CI docker job does)
echo "VAULTMIND_JWT_SECRET=$(openssl rand -hex 32)" > .env && echo "VAULTMIND_SEED_DEMO_DATA=true" >> .env
docker compose up -d --build && bash e2e/docker-smoke.sh http://localhost:8000
```

---

## F. Release recommendation

**Ready with limitations**, for these reasons:

- **The access model holds.** People can't see what they aren't cleared for, whether through the assistant, search, the library, downloads or metadata. Every leak found in this audit is fixed and covered by a test that would fail if it came back.
- **It installs safely by default.** Production refuses a weak secret, demo data is opt-in, schema upgrades preserve data, and a backup restores cleanly.
- **The product works in all three browser engines.** The full 274-check audit passes in Chromium, Firefox and WebKit, and against the production container on PostgreSQL. It also passes axe with no violations and scores 98–100 in Lighthouse.
- **It isn't yet ready to be a company's system of record**, because some things are unverified or missing:
  - the real Claude API (D1)
  - real screen readers and devices (D3, D4)
  - sessions that end on a password change (R1)
  - scale-out (R2)
  - malware scanning (R3)
  - people management in the app (R5)
  - scheduled backups (R6)

**Before relying on it with real company documents:**

1. Run `test_live_anthropic.py` with your key, or keep AI off.
2. Do the D3 and D4 manual passes.
3. Deploy following "Deploying for real" in the README: secrets, no demo data, HTTPS with HSTS, `FORWARDED_ALLOW_IPS`, and one process.
4. Schedule the backup commands.
5. Agree internally on sending passages to Anthropic, if AI is on.

---

## Audit checklist

Statuses:

- ✅ Verified: a check ran and passed.
- ❌ Failed: the check ran and failed. Nothing is in this state; failures found during the audit were fixed and re-run.
- ⚪ Not tested: no check was run.
- ⛔ Blocked: the check couldn't run here.

### 1. Functionality

| Check | Status | Evidence |
|---|---|---|
| Every flow, as six different people | ✅ | `audit.js`, 274/274 (Chromium locally; CI in Chromium, Firefox, WebKit and against the container) |
| Invalid input, empty and unsupported files, duplicates, sensitive overrides | ✅ | `audit.js`, `test_api.py` |
| Offline API, failed send keeps the draft, rate-limit message | ✅ | `audit.js` failure-handling block |
| Concurrency of requests and approvals | ✅ | `test_security.py` |
| Operator commands (create admin or user, reset password) | ✅ | `test_security.py` |

### 2. AI integration

| Check | Status | Evidence |
|---|---|---|
| Permission filtering before the prompt; prompt-injection resistance (fake model) | ✅ | `test_api.py` |
| Real SDK error handling (401, 429, 529, 5xx, credit, timeout, network, empty, truncated) | ✅ | `test_llm_provider.py` |
| Offline mode clearly labelled; fallback reason shown | ✅ | `audit.js` "answers say whether AI wrote them", `test_llm_provider.py` |
| API key never in the browser bundle | ✅ | CI bundle grep. The key is read only on the server. |
| Real Claude key and model | ⛔ | D1: no key. Run `test_live_anthropic.py`. |

### 3. Security

| Check | Status | Evidence |
|---|---|---|
| Authorization on every endpoint; restricted documents indistinguishable from missing | ✅ | `test_api.py`, `audit.js` |
| Search, hint and metadata side channels closed | ✅ | A3–A5 tests |
| Sign-in throttling, enumeration, input limits, tampered tokens | ✅ | `test_security.py` |
| Secrets: weak secret refused; no secrets in the bundle or repo | ✅ | Tests, CI grep, staged-diff scan before each commit |
| Headers, CSP, HSTS option, JSON 404 | ✅ | Tests, `audit.js`, `docker-smoke.sh` |
| Upload and download hardening | ✅ | `test_security.py` |
| Dependency vulnerabilities | ✅ | `pip-audit` 0, `npm audit` 0 |
| Session revocation | ❌ not implemented (R1) | Tracked as an open risk, not a failed test |
| Malware scanning | ⚪ | R3 |

### 4. Accessibility (WCAG 2.2 AA)

| Check | Status | Evidence |
|---|---|---|
| axe A/AA on every page, both themes, phone and desktop | ✅ | 0 violations |
| Keyboard only, visible focus (2.4.7), focus order (2.4.3) | ✅ | `a11y-review.js` |
| Focus not obscured (2.4.11) | ✅ | `a11y-review.js` at 390 and 1280px |
| Target size 24px or more (2.5.8) | ✅ | `audit.js` phone widths |
| Dragging alternative (2.5.7) | ✅ | The upload drop zone is also a keyboard-operable button |
| Accessible authentication (3.3.8) | ✅ | Password managers and paste work (`autocomplete` set); no cognitive test |
| Reflow at 320px (1.4.10) and text spacing (1.4.12) | ✅ | `audit.js` and `a11y-review.js` |
| Reduced motion | ✅ | `a11y-review.js` |
| Real screen readers | ⛔ | D4 |

### 5. Browsers and devices

| Check | Status | Evidence |
|---|---|---|
| Chromium | ✅ | Local and CI |
| Firefox | ✅ | CI. An occasional automation timeout is retried once and reported (section C) |
| WebKit (Playwright build on Linux) | ✅ | CI |
| Safari on macOS and iOS | ⛔ | D2, D3 |
| Phone and tablet widths (320–1920px) | ✅ | Viewport emulation |
| Physical iOS and Android devices | ⛔ | D3 |

### 6. Docker and deployment

| Check | Status | Evidence |
|---|---|---|
| Image builds; non-root user; health check uses readiness | ✅ | CI Docker job |
| Refuses to start without a secret | ✅ | CI Docker job |
| Full audit against the container on PostgreSQL | ✅ | CI Docker job |
| Data survives a restart; no errors logged; graceful shutdown | ✅ | CI Docker job |
| Docker on a developer machine | ⛔ here | D5 |

### 7. Fonts and design

| Check | Status | Evidence |
|---|---|---|
| Self-hosted, `font-display: swap`, no third-party requests, allowed by the CSP | ✅ | `audit.js` fonts check (all engines in CI) |
| Light and dark themes with no flash on load | ✅ | `audit.js` theme check |
| Behind a production CDN or proxy | ⚪ | D6 |

### 8. Performance

| Check | Status | Evidence |
|---|---|---|
| Lighthouse 98–100 on the public pages | ✅ | Section C |
| Layout shift under 0.1 on the public pages | ✅ | `perf.js` |
| No memory leak over a long session | ✅ | `perf.js` soak |
| Gzip and immutable caching of hashed assets | ✅ | `docker-smoke.sh` |
| Load at company scale | ⚪ | D7 |
| Route-level code splitting | ✅ | Added on 28 October: the signed-in pages load on demand (section G3) |

### 9. Data and reliability

| Check | Status | Evidence |
|---|---|---|
| Migrations, including upgrading a 1.0 database in place | ✅ | Tests; PostgreSQL upgrade and downgrade |
| Readiness, reconnect (`pool_pre_ping`), graceful shutdown | ✅ | Tests, CI Docker job |
| Backup and restore | ✅ local drill / ⛔ in Compose | D8 |
| Scheduled backups | ⚪ | R6 |
| Error tracking and alerting | ⚪ | Logs go to stdout (`docker compose logs`). Nothing alerts on errors yet; add Sentry or your platform's log alerts |

### 10. Testing and CI

| Check | Status | Evidence |
|---|---|---|
| CI runs lint, dependency audits, backend on two databases, three browsers, and the Docker runtime | ✅ | `.github/workflows/ci.yml`. Every job passed on the final commit |
| CI can't hang: pinned runner image, a time limit on every job, browsers cached | ✅ | Section C, "Every CI run during the audit" |
| Evidence kept when CI fails (reports, screenshots, logs) | ✅ | `upload-artifact` steps |

### 11. SEO and maintainability

| Check | Status | Evidence |
|---|---|---|
| Lighthouse SEO 100; meta description; Open Graph image; `robots.txt`; `<noscript>` | ✅ | Section C |
| Absolute URLs for `og:image` and a canonical link | ⚪ | They need the production domain. Set them in `frontend/index.html` when deploying. |
| Types, lint, formatting, documented operator commands | ✅ | `tsc`, `ruff`, README |

---

## G. Company features (28 October 2026)

The features asked for after the audit: VaultMind now reads files where they already live, people sign in with their company account, the assistant does more, and admins can run it without the command line.

### G1. What was added

- **Connectors** for shared folders, Google Drive and Microsoft 365 (SharePoint, OneDrive), synced on a schedule. Each file can keep its sharing from the source (people, groups, domain, company-wide links), or follow folder rules (team and classification per folder). Optional directory sync creates and updates people, teams (from departments) and groups.
- **Sign in with Google or Microsoft** (OpenID Connect, authorization code with PKCE). Password sign-in can be switched off for everyone except admins.
- **A smarter assistant:**
  - citations of the exact paragraph
  - asking about one document (summary, key points, "what it means for me")
  - comparing two documents
  - what changed between versions
  - actions it can offer, run only when the person presses the button: ask for access, share, save an answer, draft a message
  - a company glossary that widens searches, and asking the model for better search words when a search finds little
  - an AI switch per document and per source, and identity numbers and secrets masked before passages reach the model
- **Notifications** in the app, by email and in Slack. They cover requests to decide, decisions, access about to expire, and changes to documents someone read.
- **A Slack app**: `/vaultmind`, mentions and direct messages, answered with the person's own permissions.
- **A first-week checklist** for new joiners.
- **Admin pages**: people (with spreadsheet import), teams, groups, sources, the glossary, the activity log and insights.

### G2. Risks in the new parts, and how they're handled

| # | Risk | How it's handled | Proven by |
|---|---|---|---|
| G-S1 | A synced file is visible to people it isn't shared with at the source | In "keep the sharing" mode a file can only be opened by its source principals (plus admins and holders of a grant). It is never discoverable, so people without access can't even see its title. Only admins manage synced files. | `test_mirrored_files_follow_the_sharing_from_their_source`, `test_people_without_access_cannot_see_mirrored_files_at_all`, `test_google_drive_files_keep_their_sharing`, `test_microsoft_365_files_keep_their_sharing` |
| G-S2 | Sharing removed at the source stays open in VaultMind | Every sync re-reads each file's sharing and replaces it. Files deleted at the source are removed. A failed listing (for example expired credentials) removes nothing and is reported. | `test_google_sharing_changes_and_removals_follow_on_the_next_sync`, `test_google_problems_are_reported_not_crashed`, `test_folder_changes_renames_and_deletions_follow_on_the_next_sync` |
| G-S3 | An admin points a shared-folder source at the server's own files | Only folders inside `VAULTMIND_FOLDER_ROOTS` can be connected; symlinks leading outside the folder are skipped. | `test_folders_outside_the_allowed_locations_are_refused`, `test_folder_paths_cannot_escape` |
| G-S4 | Folder rules label a payroll file "Public" | The scanner's findings set a floor: rules can raise a file's classification, never lower it below what the scanner found. | `test_a_shared_folder_syncs_with_folder_rules` |
| G-S5 | Directory sync gives too much access, or locks people out | New people's positions come from their job titles and err low. People suspended at the source are switched off; people who already had an account are linked, not changed; and a sync never switches off an administrator. | `test_position_from_title`, `test_google_drive_files_keep_their_sharing`, `test_google_sharing_changes_and_removals_follow_on_the_next_sync` |
| G-S6 | Forged or replayed company sign-in | Each sign-in gets a random state, nonce and PKCE verifier, kept in a signed, HttpOnly, 10-minute cookie. The ID token's signature is checked against the provider's published keys, along with its issuer, audience, expiry and nonce. Google accounts must be verified and in an allowed domain; Microsoft accounts must be in the tenant. The return address must be on the same site. The session token is handed to the page in the URL fragment, so it never reaches a server log. | `test_sso.py` (9 tests: wrong state, missing cookie, another key's signature, another nonce, other domains, unverified email, another tenant, deactivated people, unsafe return addresses) |
| G-S7 | The assistant does something nobody asked for | Actions are only proposed. They run when the person presses the button, and permissions are checked again at that moment with the same rules as the regular pages. Proposals belong to one person; anyone else gets 404. A dismissed or completed action can't run again. | `test_request_access_from_a_locked_match`, `test_share_with_a_team_needs_a_manager_and_a_confirmation`, `test_give_one_person_access`, `test_dismissed_actions_cannot_run` |
| G-S8 | A saved answer leaks a document to a wider audience | Saved answers are Restricted (only their author and admins can open them) until the author shares them. If any source was kept away from the AI, so is the saved note. | `test_save_an_answer_as_a_private_document` |
| G-S9 | Identity numbers or secrets sent to the AI service | Aadhaar, PAN, card numbers, API keys and passwords are masked in every passage before it is sent; documents and sources with AI reading off are never sent. The rewrite of a weak search sends only the question. | `test_identity_numbers_and_secrets_are_masked_before_the_model_sees_them`, `test_documents_with_ai_reading_off_never_reach_the_model`, `test_weak_searches_are_rephrased_by_the_model_without_sending_documents` |
| G-S10 | The model cites a paragraph it was never given | Paragraph citations are checked against the passages actually sent; unknown ones are reduced to the document, unknown documents are removed. | `test_the_model_can_only_cite_passages_it_was_given` |
| G-S11 | Forged Slack requests, or answers leaking into channels | Every request needs a valid Slack signature less than five minutes old. People are matched by their Slack profile's email and answered with their own permissions. In channels, answers are ephemeral (only the asker sees them). Retries and repeated events are answered once; bots are ignored. Answers go only to Slack's own addresses. | `test_slack_endpoints_need_slack_set_up_and_a_valid_signature`, `test_slash_command_answers_privately_with_the_askers_permissions`, `test_mentions_are_answered_only_to_the_person_who_asked` |
| G-S12 | Insights expose who asked what | The insights page shows questions without who asked them. | `test_insights_show_what_people_ask_and_what_needs_attention` |
| G-S13 | A file deleted before its database change commits, or kept after it | Stored files are deleted only after the transaction commits, and only when no other document uses them. | `test_a_file_is_only_deleted_once_the_deletion_is_committed` |
| G-S14 | Admin spreadsheet import makes a partial mess | Import has a preview; nothing is saved while any row has a problem, and nobody can remove their own admin rights through it. | `test_import_people_from_a_spreadsheet` |
| G-S15 | A new version is deployed while people have VaultMind open, so the next page they open can't download its code (the signed-in pages load on demand) | The download is tried again after 1.5 seconds. If it still fails, the page says it didn't load and offers **Reload**; the sidebar keeps working, so other pages can still be opened. Before this, the screen went blank. | Browser audit: `offline: a page whose code fails to download offers a reload; the rest of the app still works` |

### G3. Tests run and their results (28 October)

| Suite | Where | Result |
|---|---|---|
| Backend: pytest, SQLite | Local, Python 3.13 | **167 passed**, 4 live tests skipped (203s) |
| Backend: pytest, PostgreSQL 16 | Local, PostgreSQL 16 | **167 passed**, 4 live tests skipped (213s) |
| Database migrations on PostgreSQL | Local, PostgreSQL 16 | **pass.** Upgrading an empty database to the latest version gives exactly the models' schema, and a database from 1.0 is upgraded in place with its data kept and its documents' text recorded as version 1. These two tests used to run only on SQLite; they now run on whichever database the suite uses, so CI's PostgreSQL job covers them too |
| Lint and format (`ruff check`, `ruff format --check`) | Local | **pass** |
| `pip-audit -r requirements.txt` | Local | **No known vulnerabilities** |
| `npm audit --audit-level=high` (frontend and e2e) | Local | **0 vulnerabilities** |
| TypeScript and production build | Local | **pass** |
| Browser audit, Chromium 153 (`audit.js`) | Local | **435/435 checks passed, 0 console errors or failed requests.** The first run had 8 failures. Two were checks clicking the wrong citation. Six were a real bug: the people table made the page scroll sideways on phones, because text hidden for screen readers escaped its scrolling box. Both were fixed, then the whole audit was run again. After the Firefox and WebKit findings below, a check for a page whose code fails to download was added, and the audit was run again: 435/435. |
| Accessibility review (`a11y-review.js`) | Local, Chromium | **24/24 passed.** New checks cover focus moving into the notifications and "Ask about this document" panels and back to the button on Esc, focus kept inside the "Connect a source" dialog, accessible names on every admin page, and focus never hidden on the admin pages |
| Performance (`perf.js`) | Local, Chromium | Slow 3G, first visit: homepage first paint **2.7s**, 134 KB, layout shift 0.056; sign-in **3.1s**, 112 KB, layout shift **0**. Memory soak: heap grew **0 MB** over 40 rounds, DOM nodes (592) and listeners (199) unchanged. The new pages first made the homepage slower (3.2s, 161 KB), so the signed-in pages now load on demand: the first download for visitors went from 100 KB to 71 KB of compressed JavaScript |
| CI on the pull request (backend ×2, frontend, Chromium, Firefox, WebKit, Docker) | GitHub Actions | First two runs: backend on SQLite and PostgreSQL, frontend, Chromium and the Docker job (production mode, PostgreSQL, full audit) **passed**; Firefox and WebKit **failed**, on console and network problems only (see below). The run for the fix was in progress at the time of this commit; its result is added once it finishes |

**What Firefox and WebKit found.** In both CI runs every check passed in Firefox and WebKit, but the audit's list of console and network problems wasn't empty, so the jobs failed. There were two causes:

1. The audit moves to the next page as soon as a check passes. Firefox and WebKit report the requests they cancel when a page is left as failed requests; Chromium doesn't. The audit now ignores a cancelled request only when the page navigated after the request started; every other failed or cancelled request still counts. Both cases were checked in Chromium with a request cancelled on purpose: counted when the page stays, ignored when the page is left.
2. In WebKit, a page's code that was still downloading when the tab reloaded showed up as a page error ("Importing a module script failed"). That pointed to a real gap (G-S15): since the signed-in pages load on demand, a page whose code couldn't be downloaded left a blank screen. The download is now retried after 1.5 seconds, which also means a download cancelled by leaving the page never surfaces, and if it still fails the page offers a reload.

New backend test files: `test_foundation.py` (groups, mirrored sharing, versions, notifications), `test_connectors.py` (shared folder, Google Drive, Microsoft 365, directory sync, scheduling), `test_sso.py`, `test_delivery.py` (reminders, email and Slack copies, the scheduler), `test_assistant_plus.py` (paragraph citations, one-document questions, compare, what changed, glossary, AI controls, masking, actions), `test_proactive.py` (the Slack app, the first-week checklist, notification settings) and `test_admin_tools.py` (people, import, teams, groups, insights). The browser audit gained checks for every new screen and flow, and its sweep covers the document panel, the notifications panel and four admin pages at eight widths in both themes.

### G4. Still unverified

| # | Item | Status | Why | How to finish it |
|---|---|---|---|---|
| G-D1 | **Google Drive and Workspace directory** | Tested against a stand-in only | No Google Workspace or service account here. The stand-in (`tests/fakeserver.py`) follows the real Drive v3 and Admin SDK paths, paging, exports and permission shapes, and checks the service account's signed token. | Follow [integrations.md](integrations.md#google-workspace) with a test folder, then check the "Checking it works" steps. |
| G-D2 | **Microsoft 365 and Entra ID** | Tested against a stand-in only | No tenant here. The stand-in follows Microsoft Graph paths, paging, download redirects, PDF conversion and throttling. | Follow [integrations.md](integrations.md#microsoft-365) with a test site, then check that sharing changes are followed after **Sync now**. |
| G-D3 | **Sign in with Google / Microsoft** | Tested against a stand-in OpenID provider | The stand-in issues real RS256 ID tokens from published keys and checks PKCE, so the protocol is exercised end to end, but not with Google's or Microsoft's own servers and consent screens. | Register the redirect URIs, sign in with a test account from an allowed domain, one from another domain (refused), and a deactivated one (refused). |
| G-D4 | **Slack** | Tested against a stand-in | No Slack workspace here. Signatures are computed exactly as Slack does. | Install the app in a test workspace ([integrations.md](integrations.md#slack)), try `/vaultmind` as two people with different access, and mention it in a channel to confirm only the asker sees the answer. |
| G-D5 | **Email** | Tested with a stand-in SMTP class | No mail server here. | Set the SMTP variables, request access to a document, and check the approver's inbox. |
| G-D6 | **The assistant's new answers with the real Claude API** | Same as D1 | Prompts and citation checks are tested with a fake model. | After D1, try "Compare the travel policy with the expense policy" and the quick actions on a document page. |
| G-D7 | **Syncing at scale** | Not tested | A sync reads files one by one, inside the single app process. Large drives (tens of thousands of files) will take a long first sync. | Connect a large test folder and time the first and second syncs; the second only downloads changed files. |
