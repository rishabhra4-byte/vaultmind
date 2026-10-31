# Running VaultMind: operations and incident response

For the team that runs VaultMind on its own servers. It covers what to set up before the
first real user, backups and restores, malware scanning, monitoring, and what to do when
something goes wrong. The alert rules in [`deploy/prometheus-alerts.yml`](../deploy/prometheus-alerts.yml)
link to the sections below.

- [Before the first real user](#before-the-first-real-user)
- [Backups](#backups)
- [Malware scanning](#malware-scanning)
- [Monitoring](#monitoring)
- [Sessions and accounts](#sessions-and-accounts)
- [Upgrading](#upgrading)
- [When an alert fires](#when-an-alert-fires)
- [Incident response](#incident-response)
- [What has and hasn't been verified](#what-has-and-hasnt-been-verified)

---

## Before the first real user

| Setting | Why | Where |
|---|---|---|
| `VAULTMIND_JWT_SECRET` (32+ random characters) | Signs sessions; production refuses to start without it | `.env` |
| `POSTGRES_PASSWORD` | The Compose default is public | `.env`, before the first start |
| No `VAULTMIND_SEED_DEMO_DATA` | The demo accounts share a published password | `.env` |
| HTTPS in front, then `VAULTMIND_HSTS=true` and `FORWARDED_ALLOW_IPS` | Encrypts traffic; lets sign-in limits see real addresses | reverse proxy, `.env` |
| `VAULTMIND_BACKUP_DIR` on encrypted storage that isn't this machine | Scheduled, verified backups | `.env` and a mounted volume |
| `VAULTMIND_CLAMAV_ADDRESS` | Malware scanning of uploads and synced files | `.env` |
| `VAULTMIND_METRICS_TOKEN` and the alert rules | Monitoring | `.env`, Prometheus |
| `VAULTMIND_AI_MAX_CLASSIFICATION` | What may be sent to the AI service ([AI data policy](ai-data-policy.md)) | `.env` |
| `VAULTMIND_SESSION_IDLE_MINUTES` (default 120) | Signs out idle browsers | `.env` |

Run **one app container with one worker** (the image does). The search index, rate limits
and background jobs live in the app process; see the [roadmap](roadmap.md) for running
several.

## Backups

A backup is one file, `vaultmind-backup-<UTC time>.tar.gz`, holding every row of the database
(as portable JSON lines, read in one consistent snapshot), every stored file, and a manifest
with the SHA-256 of each part. Signed-in sessions are left out, so after a restore everyone
signs in again. The archive is written with `0600` permissions.

**It contains every document and every password hash.** Store it encrypted (an encrypted
volume, or a bucket with server-side encryption), with the same access controls as the
database, and away from the machine VaultMind runs on.

### Scheduled backups

Set `VAULTMIND_BACKUP_DIR` (and mount that folder into the container). Every
`VAULTMIND_BACKUP_INTERVAL_HOURS` (24) the app:

1. writes a backup,
2. **checks it restores**: it rebuilds the archive in a scratch database and folder, then compares
   the row count of every table with the manifest, checks every document's file against its
   SHA-256, searches the restored text, and checks someone can still administer it,
3. records the result in the activity log (`backup.created` or `backup.check_failed`) and next
   to the archive (`….tar.gz.check.json`), tells administrators in the app if anything failed,
4. keeps the newest `VAULTMIND_BACKUP_KEEP` (14) backups and deletes older ones.

**Admin → Overview → Backups** shows the newest backup and whether its check passed. The
`VaultMindBackupMissing` and `VaultMindBackupCheckFailed` alerts watch the same thing.

### By hand

```bash
docker compose exec app python -m app.cli backup --out /data/backups      # writes, then checks it
docker compose exec app python -m app.cli verify-backup /data/backups     # check the newest again
```

### Restoring

A restore only ever writes into an **empty** database; it refuses one that has people in it.
It also refuses a backup made by a different database version: restore with the VaultMind
version that made it, then upgrade.

```bash
# 1. Stop the app, and create a new, empty database (keep the old one until you're sure)
docker compose stop app
docker compose exec db createdb -U vaultmind vaultmind_restored

# 2. Restore into it, with an empty folder for the files
docker compose run --rm \
  -e VAULTMIND_DATABASE_URL=postgresql+psycopg://vaultmind:$POSTGRES_PASSWORD@db:5432/vaultmind_restored \
  -e VAULTMIND_STORAGE_DIR=/data/storage-restored \
  app python -m app.cli restore /data/backups/vaultmind-backup-20261031T020000Z.tar.gz --yes

# 3. Point the app at them (VAULTMIND_DATABASE_URL and VAULTMIND_STORAGE_DIR in .env), start it
docker compose up -d app
```

The search index is rebuilt from the database at start. Everyone signs in again.

### Restore drills

CI restores a backup into a new PostgreSQL database on every change (the Docker job), and
the test suite restores into an empty database on SQLite and PostgreSQL. Still, practise
on your own data: once a quarter, restore the newest backup into a scratch database
following the steps above, start a second app container on it, sign in, and open a few
documents. Record how long it took; that's your real recovery time.

## Malware scanning

Every upload, the upload preview, every new version and every synced file is streamed to
ClamAV's `clamd` before VaultMind parses or stores it. Files with malware are refused, the
attempt is recorded in the activity log (`document.upload_blocked` or `document.sync_blocked`)
and administrators are told in the app. If `clamd` can't scan a file (it's down, times out or
reports an error), the file is refused too, unless `VAULTMIND_CLAMAV_REQUIRED=false`, so an
outage never lets files in unscanned. Synced files that couldn't be scanned are retried at the
next sync.

With Docker Compose:

```bash
docker compose --profile clamav up -d clamav    # the first start downloads definitions (a few minutes, ~2 GB RAM)
echo "VAULTMIND_CLAMAV_ADDRESS=clamav:3310" >> .env
docker compose up -d app
```

Or point `VAULTMIND_CLAMAV_ADDRESS` at an existing `clamd` (`host:port`, or a socket path).

**Check it works** with the standard EICAR test file, which every scanner reports and which is
harmless: save the 68 characters from [eicar.org](https://www.eicar.org/download-anti-malware-testfile/)
as `eicar.txt` and upload it. The upload should be refused with "This file contains malware",
and **Admin → Activity** should show the refusal. **Admin → Overview → Malware scanning** says
whether ClamAV is answering.

## Monitoring

`GET /metrics` serves Prometheus metrics when `VAULTMIND_METRICS_TOKEN` is set; scrapers send
the token as a bearer token. Without a token the endpoint doesn't exist.

```yaml
scrape_configs:
  - job_name: vaultmind
    metrics_path: /metrics
    authorization: { credentials_file: /etc/prometheus/vaultmind-token }
    static_configs: [{ targets: ["vaultmind.internal:8000"] }]
rule_files: [/etc/prometheus/vaultmind-alerts.yml]   # deploy/prometheus-alerts.yml
```

| Metric | What it tells you |
|---|---|
| `vaultmind_http_requests_total{method,route,status}` | Traffic and errors, by route template (`/api/documents/{doc_id}`) and status class |
| `vaultmind_http_request_duration_seconds` | Latency per route (histogram) |
| `vaultmind_answers_total{mode,evidence}` | Answers written by AI or offline, and how often the documents held enough to answer |
| `vaultmind_ai_fallbacks_total{reason}` | Answers that fell back to offline because the AI service failed |
| `vaultmind_sign_ins_total{method,result}` | Sign-ins and failures (password guessing shows here) |
| `vaultmind_access_denied_total{action}` | Refused opens and downloads |
| `vaultmind_files_blocked_total{source}`, `vaultmind_scan_failures_total` | Malware refused; files refused because they couldn't be scanned |
| `vaultmind_syncs_total{result}`, `vaultmind_sources_failing` | Source syncs, and sources whose last sync failed |
| `vaultmind_backups_total{result}`, `vaultmind_backup_last_success_timestamp_seconds`, `vaultmind_backup_last_verified` | Backups and their restore checks |
| `vaultmind_database_up`, `vaultmind_scanner_up`, `vaultmind_sessions_open`, `vaultmind_documents` | State, read at each scrape |

Counters live in the app process and restart from zero with it; `rate()` and `increase()`
handle that.

**Request IDs.** Every response has an `X-Request-ID` header (VaultMind keeps one sent by your
proxy if it looks sensible), and every log line written while handling that request includes
it in square brackets. When someone reports an error, ask for the time and what they did, or
read the ID from the browser's network panel, and search the log for it.

## Sessions and accounts

- Every sign-in is a session the server can end. Signing out ends it on the server, not just in
  the browser.
- **People** see where they're signed in (the key icon in the sidebar), can sign other browsers out,
  and can change their password, which signs out their other sessions.
- **Administrators** can sign someone out everywhere (**Admin → People → Edit → Sign out
  everywhere**), for example after a lost laptop. Resetting someone's password, deactivating
  them or removing their admin rights also ends all their sessions at once.
- Sessions end after `VAULTMIND_SESSION_IDLE_MINUTES` without activity (120; `0` turns it off), and
  8 hours after sign-in in any case.
- Only a SHA-256 of each session id is stored, so a copy of the database can't be used to sign in.

## Upgrading

```bash
docker compose exec app python -m app.cli backup --out /data/backups   # 1. a checked backup first
git pull && docker compose up -d --build                               # 2. migrations run at start
```

Check **Admin → Overview** and `/api/ready` afterwards. To go back, restore the backup with the
previous version (backups are tied to the database version that made them).

---

## When an alert fires

### VaultMind is down

`up == 0`: Prometheus can't scrape it.

1. `docker compose ps` and `docker compose logs --tail 200 app`. Look for `Traceback` or a refusal
   to start (production refuses to start with a weak `VAULTMIND_JWT_SECRET`).
2. `curl -fsS localhost:8000/api/ready` from the host. `503` means the database is the problem
   (next section).
3. Restart: `docker compose up -d app`. If it doesn't stay up, roll back to the previous image and
   open an incident.

### The database is down

`vaultmind_database_up == 0`, or `/api/ready` returns `503`.

1. `docker compose ps db` and `docker compose logs --tail 200 db`.
2. Disk space on the database volume (`df -h`); PostgreSQL stops writing when it's full.
3. Once it's back, check **Admin → Overview** and run `verify-backup` on the newest backup.

### Server errors

More than 2% of requests return a 5xx.

1. Find the route: `sum by (route) (rate(vaultmind_http_requests_total{status="5xx"}[5m]))`.
2. Search the log around that time for `ERROR` and `Traceback`; the request ID in brackets ties
   lines to one request.
3. A recent upgrade? Roll back (see [Upgrading](#upgrading)).

### Slow answers

The slowest 5% of assistant answers take over 10 seconds.

1. Compare with `vaultmind_answers_total{mode="ai"}`: slow answers are usually the AI service.
   `VAULTMIND_LLM_TIMEOUT_SECONDS` (30) caps each call, after which the answer falls back to offline.
2. If offline answers are slow too, check CPU and the size of the search index
   (`vaultmind_search_index_documents`); see the [roadmap](roadmap.md) for scaling.

### AI answers falling back

Answers keep falling back to offline mode because the AI service fails.

1. `vaultmind_ai_fallbacks_total` by `reason`: `auth` (the key was revoked or is wrong), `quota`
   (the account's usage limit), `rate_limited`, `timeout`, `network`, `unavailable`, `bad_response`, `error`.
2. The status page of your AI provider, and the key in `.env`.
3. Nothing is lost meanwhile: offline answers use the same permission checks and say how they were made.

### Backups

No verified backup in 36 hours, or the newest one failed its restore check.

1. **Admin → Overview → Backups** shows which check failed; so does `….tar.gz.check.json` next to the
   archive, and **Admin → Activity** (`backup.failed`, `backup.check_failed`).
2. "A document's file is missing from storage": a stored file was lost. Find the document in the
   message, restore its file from an older backup, or delete the document.
3. Disk full or the folder not mounted: fix it, then run `python -m app.cli backup` by hand.
4. Until a backup passes, treat the most recent passing one as your recovery point.

### The malware scanner is down

ClamAV doesn't answer, so uploads and syncs are refused (with `VAULTMIND_CLAMAV_REQUIRED=true`).

1. `docker compose logs --tail 100 clamav`. It needs about 2 GB of memory to load its definitions;
   an out-of-memory kill is the usual cause.
2. Restart it: `docker compose --profile clamav up -d clamav`. The first start downloads definitions.
3. Turning `VAULTMIND_CLAMAV_REQUIRED` off to keep uploads going is a security decision; record it
   if you make it.

### Malware was blocked

The scanner refused a file. VaultMind didn't store it; nothing needs cleaning up in VaultMind.

1. **Admin → Activity** shows who uploaded it (or which source and file it was) and what ClamAV found.
2. For an upload: tell your security team, since the person's computer may be infected.
3. For a synced file: the file is still in the source (Drive, SharePoint, the share); have its
   owner remove it there.

### Many failed sign-ins

More than 50 failed sign-ins in 10 minutes: possibly someone guessing passwords. Per-account and
per-address limits already slow them down.

1. **Admin → Activity** and the log show which accounts; your proxy's log shows the addresses.
2. Block the addresses at the proxy or firewall if they're outside the company.
3. If an account may be compromised: reset its password (that ends all its sessions) and ask the
   person to change it. Better still, use company sign-in with MFA and turn off password sign-in.

### A source stops syncing

A connected drive or folder has failed to sync for an hour.

1. **Admin → Sources** shows the error. Expired or revoked credentials are the usual cause; see
   [integrations.md](integrations.md) for each service.
2. A failed listing removes nothing: files already synced stay as they were until a sync succeeds.

---

## Incident response

For anything that may have exposed documents, accounts or the server.

1. **Contain** (first 15 minutes): sign out affected people everywhere (or deactivate them); turn
   off the affected source (**Admin → Sources**); if the server itself may be compromised, take
   it off the network but keep it running for evidence.
2. **Keep the evidence**: export the activity log (**Admin → Activity**, or the `audit_events`
   table), the app log (`docker compose logs app > app.log`), your proxy's log, and take a
   backup. Don't restore over the database yet.
3. **Find out who saw what**: the activity log records every document opened, downloaded, refused,
   shared and changed, every access decision and every question asked, with times and people.
   Request IDs tie the app log to proxy logs.
4. **Fix and recover**: rotate `VAULTMIND_JWT_SECRET` (this signs everyone out), the AI key and any
   connector credentials that may have leaked; restore from a backup made before the incident
   if data was changed.
5. **Tell people** your policy and the law require you to tell, then write a short, blameless
   review: what happened, how it was found, what changes.

Report security problems in VaultMind itself privately to the maintainers (see
[the security overview](security-overview.md#reporting-a-vulnerability)).

## What has and hasn't been verified

| Part | Verified by | Not yet verified |
|---|---|---|
| Backups and restore checks | Unit tests on SQLite and PostgreSQL; a restore of a demo backup into a new PostgreSQL database that was then started and used; CI's Docker job on every change | Restoring a large install (time and memory with many GB of files) |
| Malware scanning | Tests against a stand-in `clamd` that speaks its protocol (clean, infected, errors, silence, down) | A real ClamAV: start it as above and upload the EICAR file |
| Metrics and alerts | Tests of the endpoint, its token, labels and counts; a test that every alert uses exported metrics; `promtool check rules` in CI | A real Prometheus scraping a running install |
| Sessions | API tests and browser checks (other sessions, admin sign-out, the reason shown at sign-in) | |
