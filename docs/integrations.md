# Connecting VaultMind to your company

Everything here is optional. Each part is switched on by setting environment variables (in
`.env` for Docker Compose) and restarting. Credentials only ever live in the server's
environment, never in the database or the browser.

Set `VAULTMIND_PUBLIC_URL` first, to the address people use, e.g.
`https://vaultmind.example.com`. Sign-in redirects and the links in emails and Slack messages
are built from it.

- [Shared folders](#shared-folders)
- [Google Workspace](#google-workspace): Drive files, people and groups, and *Sign in with Google*
- [Microsoft 365](#microsoft-365): SharePoint and OneDrive files, people and groups, and *Sign in with Microsoft*
- [Email](#email)
- [Slack](#slack)
- [How files and people are kept in sync](#how-syncing-works)

> **What has been verified.** Every connector, sign-in flow and delivery channel is covered by
> automated tests against local stand-ins that follow each service's real API: paging,
> redirects, throttling, token checks, and wrong or tampered tokens. None of them has been run
> against real Google, Microsoft, Slack or mail servers. Before relying on one, connect a test
> account and check the results (see "Checking it works" in each section).

---

## Shared folders

For files on a network share or a folder mounted into the container.

1. Mount the share into the app container (for example add `- /mnt/company-share:/shares/company:ro`
   under the `app` service's `volumes` in `docker-compose.yml`).
2. Allow that location: `VAULTMIND_FOLDER_ROOTS=["/shares"]`. Admins can only connect folders
   inside these roots, so nobody can point VaultMind at the server's own files.
3. In VaultMind: **Admin → Sources → Connect a source → Shared folder**, then enter the folder
   path.

File-system permissions don't map to people in a portable way, so a shared folder uses
**folder rules**: each subfolder gets an owning team and a classification (for example
`HR/Payroll` → Human Resources, Restricted), and the longest matching folder wins. The
sensitivity scanner can still raise a file's classification when it finds identity numbers,
secrets or salary data; it never lowers one.

---

## Google Workspace

### Drive files, people and groups

VaultMind reads Drive with a **service account** that has domain-wide delegation and acts as
one of your admins. It only ever reads.

1. In Google Cloud, create a project, enable the **Google Drive API** and the **Admin SDK API**,
   create a service account and download a JSON key.
2. In the Google Admin console: **Security → Access and data control → API controls → Domain-wide
   delegation → Add new**. Enter the service account's client ID and these scopes:
   ```
   https://www.googleapis.com/auth/drive.readonly
   https://www.googleapis.com/auth/admin.directory.user.readonly
   https://www.googleapis.com/auth/admin.directory.group.readonly
   https://www.googleapis.com/auth/admin.directory.group.member.readonly
   ```
   (The last three are only needed for syncing people and groups.)
3. Set:
   ```
   VAULTMIND_GOOGLE_SERVICE_ACCOUNT_FILE=/run/secrets/google-sa.json   # or VAULTMIND_GOOGLE_SERVICE_ACCOUNT_JSON='{...}'
   VAULTMIND_GOOGLE_ADMIN_EMAIL=it-admin@example.com                   # the admin the service account acts as
   ```
4. In VaultMind: **Admin → Sources → Connect a source → Google Drive**. Paste one or more folder
   links (shared drives work too; subfolders are included) and choose how access works:
   - **Exactly who they're shared with in Google Drive** (recommended). Each file keeps its Drive
     sharing: named people, Google groups, your domain, or "anyone with the link" (treated as the
     whole company). People who can't open a file in Drive don't see it in VaultMind at all.
   - **VaultMind's team and classification rules**, as for shared folders.

   Tick **Also sync people, teams and groups from the directory** to create and update accounts
   from Google: new people get an account, departments become teams, job titles set a starting
   position (it errs low; an admin can raise it), suspended people are switched off, and Google
   groups are kept in step. People who already had a VaultMind account are linked, not changed.
   A sync never switches off an administrator, so a broken directory can't lock everyone out.

Google Docs, Sheets and Slides are exported as text; PDF, Word, text and CSV files are read as
they are; other files are skipped and listed on the source's card.

### Sign in with Google

This uses an **OAuth client**, separate from the service account.

1. In Google Cloud: **APIs & Services → Credentials → Create credentials → OAuth client ID →
   Web application**. Add the redirect URI:
   ```
   https://vaultmind.example.com/api/auth/sso/google/callback
   ```
   On the consent screen, choose **Internal** so only your organisation can use it.
2. Set:
   ```
   VAULTMIND_GOOGLE_CLIENT_ID=...apps.googleusercontent.com
   VAULTMIND_GOOGLE_CLIENT_SECRET=...
   VAULTMIND_GOOGLE_ALLOWED_DOMAINS=["example.com"]
   ```
3. The sign-in page shows **Continue with Google**. Only verified accounts from the allowed
   domains get in. Someone signing in for the first time gets an account (Associate level, in
   `VAULTMIND_SSO_DEFAULT_TEAM` or the first team, with the first-week checklist), unless
   `VAULTMIND_SSO_AUTO_CREATE=false`, in which case an admin must add them first.

### Checking it works

Connect a test folder that holds a file shared with one colleague only. After the first sync,
that colleague should see it and you shouldn't (unless you're an admin). Change its sharing in
Drive, press **Sync now**, and check VaultMind follows.

---

## Microsoft 365

### SharePoint and OneDrive files, people and groups

1. In Microsoft Entra ID: **App registrations → New registration**. Note the **Directory
   (tenant) ID** and **Application (client) ID**, then create a **client secret**.
2. **API permissions → Microsoft Graph → Application permissions**: `Files.Read.All` (or
   `Sites.Read.All`), plus `User.Read.All` and `GroupMember.Read.All` for syncing people and
   groups. Then **Grant admin consent**.
3. Set:
   ```
   VAULTMIND_MICROSOFT_TENANT_ID=...
   VAULTMIND_MICROSOFT_CLIENT_ID=...
   VAULTMIND_MICROSOFT_CLIENT_SECRET=...
   ```
4. In VaultMind: **Admin → Sources → Connect a source → Microsoft 365**, then choose a SharePoint
   site (its address and library, "Documents" by default), someone's OneDrive (their email), or a
   drive ID. Optionally limit it to one folder. Access works as for Google: either the sharing
   from Microsoft 365 (people, Microsoft 365 groups, and "people in your organisation" links), or
   folder rules.

Word, PDF and text files are read directly; PowerPoint, Excel and older Office formats are
converted to PDF by Microsoft first. Throttling (HTTP 429) is retried after the delay Microsoft
asks for.

### Sign in with Microsoft

It reuses the same app registration.

1. **Authentication → Add a platform → Web**, with the redirect URI:
   ```
   https://vaultmind.example.com/api/auth/sso/microsoft/callback
   ```
2. Set `VAULTMIND_MICROSOFT_SSO=true`. Only accounts from your tenant can sign in.

### Turning off passwords

Once everyone signs in with Google or Microsoft, set `VAULTMIND_PASSWORD_LOGIN=false`.
Administrators keep their password as a way in if the identity provider is down; everyone else
must use their company account.

---

## Email

Copies of notifications (requests to approve, decisions, changes to documents people read,
access about to expire) can go by email.

```
VAULTMIND_SMTP_HOST=smtp.example.com
VAULTMIND_SMTP_PORT=587
VAULTMIND_SMTP_USERNAME=vaultmind@example.com
VAULTMIND_SMTP_PASSWORD=...
VAULTMIND_SMTP_FROM=VaultMind <vaultmind@example.com>
VAULTMIND_SMTP_STARTTLS=true
```

Each person can turn email copies off from the notifications panel. Emails are sent by the
background scheduler, so a slow mail server never slows the app down; failures are recorded and
not retried.

---

## Slack

The Slack app does two things: people ask VaultMind questions (with `/vaultmind`, by mentioning
it, or in a direct message), and notifications are copied to them as direct messages.

1. At https://api.slack.com/apps, **Create New App → From scratch**.
2. **OAuth & Permissions → Bot Token Scopes**: `chat:write`, `commands`, `users:read`,
   `users:read.email`, `app_mentions:read`, `im:history`. Install the app and copy the **Bot User
   OAuth Token** (`xoxb-…`).
3. **Basic Information**: copy the **Signing Secret**.
4. **Slash Commands → Create New Command**: `/vaultmind`, request URL
   `https://vaultmind.example.com/api/slack/commands`.
5. **Event Subscriptions**: on, request URL `https://vaultmind.example.com/api/slack/events`;
   bot events `app_mention` and `message.im`. Under **App Home**, allow messages in the Messages
   tab.
6. Set:
   ```
   VAULTMIND_SLACK_BOT_TOKEN=xoxb-...
   VAULTMIND_SLACK_SIGNING_SECRET=...
   ```

How it stays safe:

- Every request must carry Slack's signature (checked with the signing secret) and be less than
  five minutes old.
- People are matched to VaultMind by the email on their Slack profile, so it must be the same as
  their work email. Unmatched people are told how to fix it.
- Answers use the person's own permissions, exactly as in the web app. In channels, answers
  are **visible only to the person who asked**, because they may quote documents the rest of the
  channel can't open.
- Each answer is also saved as a conversation in VaultMind with a link to it. Actions the
  assistant offers (asking for access, sharing) are confirmed there, never from Slack.

---

## How syncing works

- A background scheduler in the app process syncs each source on its interval (every hour by
  default), sends email and Slack copies of notifications, and reminds people two days before
  temporary access ends. `VAULTMIND_SCHEDULER_ENABLED=false` turns it off (for example when
  running a one-off command).
- A sync reads the list of files, then for each file only downloads and re-reads it if it
  changed. A changed file becomes a new version, and people who read it in the last 90 days are
  told what changed.
- Files deleted in the source are removed from VaultMind. If the list of files can't be read at
  all (for example the credentials expired), nothing is removed and the error shows on the
  source's card.
- Sharing with someone who has no VaultMind account gives nobody access until an account with
  that email exists. A Google or Microsoft group in a file's sharing only has members in
  VaultMind once the directory is synced. SharePoint site groups can't be looked up, so they are
  counted as "unresolved" on the source's card and give nobody access.
- Admins can switch AI reading off for a whole source or one document: those passages are never
  sent to the AI, though people who can open the files still find them in search.
