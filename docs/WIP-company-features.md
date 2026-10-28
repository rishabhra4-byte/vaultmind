# Work in progress: company features (branch `wip/company-features`)

This branch isn't merged. `main` is unchanged and is still the audited release.
Resume with:

```bash
git fetch origin && git switch wip/company-features
cd backend && python -m pytest -q          # last full run: 167 passed, 4 skipped
cd ../frontend && npx tsc -b               # type-checks clean
```

## Done (backend, with tests)

- **Connectors** (`app/connectors/`, `app/sync.py`, `app/scheduler.py`): shared folder, Google Drive, Microsoft 365, directory sync (people, teams, groups), scheduled syncs. Admin API: `/api/admin/sources`.
- **Groups and versions**: documents shared with groups; mirrored sharing from the source; version history; `/documents/{id}/versions`, `/changes`; readers notified of changes.
- **Sign in with Google / Microsoft** (`app/sso.py`): OIDC with PKCE, state and nonce checks, allowed domains and tenant checks. Password sign-in can be switched off; admins keep it as a fallback.
- **Smarter assistant** (`app/assistant.py`, `app/actions.py`, `app/glossary.py`):
  - paragraph citations (`[D12.3]`)
  - asking about one document (`document_id` and quick-action `intent`)
  - comparing two documents
  - "what changed"
  - actions that only run after the person confirms them: request access, share, save an answer, draft a message
  - glossary and the model rephrasing weak searches
  - per-document and per-source AI on/off, and ID numbers and secrets masked before text reaches the model
- **Notifications**: in-app; email and Slack copies; expiry reminders. `/api/me/preferences`.
- **Slack bot** (`app/slack_bot.py`, `/api/slack/commands`, `/api/slack/events`): signed requests only. Channel answers are visible only to the asker.
- **First-week checklist**: `/api/onboarding`.
- **Admin API** (`app/routers/directory.py`, `admin.py`): people (create, edit, password reset, CSV import with preview), teams, groups, `/api/admin/insights`, glossary.

Google, Microsoft, Slack, SMTP and OIDC are tested against the local fakes in `tests/fakeserver.py` and `tests/test_sso.py`. They have **not** been tried against the real services.

## Partly done (frontend)

Written and type-checked, but without styling:
- sign-in buttons and the `?next=` return path (`signin.ts`, `Login.tsx`, `main.tsx`)
- paragraph-citation links (`RichText.tsx`)
- the shared message component and action cards (`Bubble.tsx`, `ActionCard.tsx`)
- the notification bell (`NotificationBell.tsx`)
- the first-week checklist (in `Assistant.tsx`)

These CSS classes still need styles in `styles.css`:
- action cards: `action-card`, `action-head`, `action-done-icon`, `action-link`, `inline-field`
- locked hints and answer notes: `sealed-row` (`.sealed` inside `.sealed-list` holds the row and the card), `ai-blocked`, `search-note`
- first-week checklist: `first-week`, `progress`, `tick`, `step-hint`
- conversation list: `thread-doc`
- notifications: `nav-btn` (sidebar button styled like the nav links), `notif-panel` and its parts (`notif-head`, `notif-list`, `notif`, `notif-title`, `notif-body`, `notif-empty`, `notif-foot`)
- `.msg.compact`

## Left to do

1. **Document page:**
   - passages with `id="p{ordinal}"` anchors and a highlight from the URL hash
   - an "Ask about this document" panel with quick actions and a compare picker
   - version list and what-changed view (`?changes=1`), and new-version upload
   - AI on/off toggle, group sharing in access settings
   - source info and an "Open in Drive" link
2. **Library:** a source filter, and the team filter read from `?team=`.
3. **Admin pages** at `/admin/*`: overview with insights, people (with import), teams, groups, sources, glossary, activity.
4. **e2e audit** (`e2e/audit.js`): the signed-out deep link now goes to `/signin?next=…` and returns there after sign-in. Update that check, and add checks for the new UI.
5. **Docs:**
   - README, `.env.example`, `docker-compose.yml`
   - `docs/production-readiness.md`
   - setup guides for Google, Microsoft, Slack and SMTP
6. **Checks and release:**
   - run ruff, pip-audit and npm audit
   - open a PR to `main`, confirm all CI jobs, then merge
