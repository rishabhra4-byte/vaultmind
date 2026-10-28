"""Google Workspace connector: Drive files with their sharing, and optionally the directory.

Authentication is a service account with domain-wide delegation, acting as an admin
(`VAULTMIND_GOOGLE_ADMIN_EMAIL`). It needs these read-only scopes in the Admin console:
drive.readonly, and for directory sync admin.directory.user.readonly,
admin.directory.group.readonly and admin.directory.group.member.readonly.

Google Docs, Sheets and Slides are exported as text (Sheets as CSV); other files are
downloaded as they are. Each file's sharing is read with the permissions API and copied
into VaultMind, so people see exactly the files Drive would show them.
"""

import json
import re
import threading
import time
from collections import deque
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import jwt

from ..config import get_settings
from .base import ConnectorError, DirectoryGroup, DirectoryUser, Grant, Http, RemoteFile

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DIRECTORY_SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.group.member.readonly",
]
FOLDER = "application/vnd.google-apps.folder"
# Google's own formats can't be downloaded; they're exported to something we can read.
EXPORTS = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}
FILE_FIELDS = "id,name,mimeType,modifiedTime,md5Checksum,size,webViewLink,trashed"
_ID = re.compile(r"(?:folders/|id=|^)([A-Za-z0-9_-]{10,})")


def folder_id(value: str) -> str:
    """Accept a folder id or a Drive folder link, e.g. https://drive.google.com/drive/folders/1AbC..."""
    m = _ID.search(value.strip())
    if not m:
        raise ConnectorError(f"“{value}” doesn't look like a Drive folder link or id")
    return m.group(1)


def _service_account() -> dict:
    s = get_settings()
    raw = s.google_service_account_json or (
        Path(s.google_service_account_file).read_text() if s.google_service_account_file else ""
    )
    try:
        info = json.loads(raw)
        return {"client_email": info["client_email"], "private_key": info["private_key"], "kid": info.get("private_key_id")}
    except (ValueError, KeyError, OSError) as exc:
        raise ConnectorError("The Google service account key is missing or isn't valid JSON") from exc


class _Token:
    """OAuth access token from a signed service-account assertion, refreshed before it expires."""

    def __init__(self, scopes: list[str]) -> None:
        self.scopes = scopes
        self._value, self._expires = "", 0.0
        self._lock = threading.Lock()
        self._http = Http("Google sign-in")

    def __call__(self) -> str:
        with self._lock:
            if time.time() < self._expires - 60:
                return self._value
            s = get_settings()
            sa = _service_account()
            now = int(time.time())
            assertion = jwt.encode(
                {
                    "iss": sa["client_email"],
                    "sub": s.google_admin_email,
                    "scope": " ".join(self.scopes),
                    "aud": s.google_token_url,
                    "iat": now,
                    "exp": now + 3600,
                },
                sa["private_key"],
                algorithm="RS256",
                headers={"kid": sa["kid"]} if sa["kid"] else None,
            )
            r = self._http.request(
                "POST",
                s.google_token_url,
                auth=False,
                data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
            )
            if r.status_code >= 400:
                raise ConnectorError(
                    "Google refused the service account. Check domain-wide delegation, "
                    "the scopes and VAULTMIND_GOOGLE_ADMIN_EMAIL."
                )
            body = r.json()
            self._value, self._expires = body["access_token"], time.time() + int(body.get("expires_in", 3600))
            return self._value


class GoogleDriveConnector:
    provider = "google"

    def __init__(self, config: dict, *, directory: bool = False) -> None:
        if not get_settings().google_configured:
            raise ConnectorError("Google Workspace isn't set up on the server (service account and admin email).")
        self.folders = [folder_id(f) for f in config.get("folders", []) if str(f).strip()]
        if not self.folders:
            raise ConnectorError("Add at least one Drive folder to sync.")
        scopes = [DRIVE_SCOPE, *(DIRECTORY_SCOPES if directory else [])]
        self.http = Http("Google Drive", token_fn=_Token(scopes))
        self.api = get_settings().google_api_base.rstrip("/")
        self.admin_api = get_settings().google_admin_api_base.rstrip("/")

    def _pages(self, url: str, params: dict, key: str, base: str | None = None) -> Iterator[dict]:
        token = None
        while True:
            q = dict(params, **({"pageToken": token} if token else {}))
            body = self.http.json("GET", (base or self.api) + url, params=q)
            yield from body.get(key, [])
            token = body.get("nextPageToken")
            if not token:
                return

    def list_files(self) -> Iterator[RemoteFile]:
        queue: deque[tuple[str, str]] = deque()
        seen: set[str] = set()
        for fid in self.folders:
            meta = self.http.json(
                "GET", f"{self.api}/drive/v3/files/{fid}", params={"fields": "id,name,mimeType", "supportsAllDrives": "true"}
            )
            if meta.get("mimeType") != FOLDER:
                raise ConnectorError(f"“{meta.get('name', fid)}” is a file, not a folder")
            queue.append((fid, meta.get("name", "")))
        while queue:
            fid, path = queue.popleft()
            if fid in seen:
                continue
            seen.add(fid)
            params = {
                "q": f"'{fid}' in parents and trashed = false",
                "fields": f"nextPageToken,files({FILE_FIELDS})",
                "pageSize": "200",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            for f in self._pages("/drive/v3/files", params, "files"):
                if f.get("trashed"):
                    continue
                if f.get("mimeType") == FOLDER:
                    queue.append((f["id"], f"{path}/{f['name']}" if path else f["name"]))
                    continue
                modified = f.get("modifiedTime")
                yield RemoteFile(
                    external_id=f["id"],
                    name=f["name"],
                    path=path,
                    mime_type=f.get("mimeType", ""),
                    version_key=f"{modified}:{f.get('md5Checksum', '')}",
                    modified=datetime.fromisoformat(modified.replace("Z", "+00:00")) if modified else None,
                    size=int(f["size"]) if f.get("size") else None,
                    url=f.get("webViewLink"),
                )

    def fetch(self, f: RemoteFile) -> tuple[bytes, str]:
        if f.mime_type in EXPORTS:
            mime, ext = EXPORTS[f.mime_type]
            r = self.http.request("GET", f"{self.api}/drive/v3/files/{f.external_id}/export", params={"mimeType": mime})
            name = f.name + ext
        elif f.mime_type.startswith("application/vnd.google-apps."):
            raise ConnectorError(f"Google {f.mime_type.rsplit('.', 1)[-1]} files can't be read as text")
        else:
            r = self.http.request(
                "GET", f"{self.api}/drive/v3/files/{f.external_id}", params={"alt": "media", "supportsAllDrives": "true"}
            )
            name = f.name
        if r.status_code >= 400:
            raise ConnectorError(f"Couldn't download “{f.name}” (HTTP {r.status_code})")
        return r.content, name

    def grants(self, f: RemoteFile) -> list[Grant] | None:
        out: list[Grant] = []
        params = {
            "supportsAllDrives": "true",
            "pageSize": "100",
            "fields": "nextPageToken,permissions(type,role,emailAddress,domain,deleted,displayName)",
        }
        for p in self._pages(f"/drive/v3/files/{f.external_id}/permissions", params, "permissions"):
            if p.get("deleted"):
                continue
            kind = p.get("type")
            if kind == "user" and p.get("emailAddress"):
                out.append(Grant("user", p["emailAddress"].lower(), name=p.get("displayName", "")))
            elif kind == "group" and p.get("emailAddress"):
                out.append(Grant("group", p["emailAddress"].lower(), name=p.get("displayName", "")))
            elif kind == "domain" and p.get("domain"):
                out.append(Grant("domain", p["domain"].lower()))
            elif kind == "anyone":
                out.append(Grant("company"))
        return out

    def directory(self) -> tuple[list[DirectoryUser], list[DirectoryGroup]]:
        users = []
        params = {"customer": "my_customer", "maxResults": "200", "projection": "full"}
        for u in self._pages("/admin/directory/v1/users", params, "users", base=self.admin_api):
            orgs = u.get("organizations") or []
            org = next((o for o in orgs if o.get("primary")), orgs[0] if orgs else {})
            users.append(
                DirectoryUser(
                    external_id=u["id"],
                    email=u["primaryEmail"].lower(),
                    name=(u.get("name") or {}).get("fullName") or u["primaryEmail"],
                    title=org.get("title", ""),
                    department=org.get("department", ""),
                    active=not u.get("suspended", False) and not u.get("archived", False),
                )
            )
        groups = []
        for g in self._pages(
            "/admin/directory/v1/groups", {"customer": "my_customer", "maxResults": "200"}, "groups", base=self.admin_api
        ):
            emails = [
                m["email"].lower()
                for m in self._pages(
                    f"/admin/directory/v1/groups/{quote(g['email'])}/members",
                    {"maxResults": "200", "includeDerivedMembership": "true"},
                    "members",
                    base=self.admin_api,
                )
                if m.get("type") == "USER" and m.get("email") and m.get("status", "ACTIVE") == "ACTIVE"
            ]
            groups.append(DirectoryGroup(external_id=g["email"].lower(), name=g.get("name") or g["email"], member_emails=emails))
        return users, groups
