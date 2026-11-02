"""Microsoft 365 connector: SharePoint / OneDrive files with their sharing, and optionally the directory.

Authentication is an app registration using client credentials. Application permissions:
Files.Read.All (or Sites.Read.All), and for directory sync User.Read.All and
GroupMember.Read.All, with admin consent.

A source points at one document library: a drive id, a SharePoint site URL (plus library
name), or a person's OneDrive (their email). Word, PDF and text files are read directly;
PowerPoint, Excel and older Office formats are converted to PDF by Microsoft first.
"""

import threading
import time
from collections import deque
from collections.abc import Iterator
from datetime import datetime
from pathlib import PurePath
from urllib.parse import quote, urlparse

from ..config import get_settings
from ..extract import SUPPORTED_EXTENSIONS
from .base import ConnectorError, DirectoryGroup, DirectoryUser, Grant, Http, RemoteFile

# Read as they are: .docx, .xlsx, .pptx (see extract.py). These are converted by SharePoint first.
CONVERT_TO_PDF = {".doc", ".ppt", ".pps", ".ppsx", ".xls", ".odt", ".odp", ".ods", ".rtf"}


class _Token:
    def __init__(self) -> None:
        self._value, self._expires = "", 0.0
        self._lock = threading.Lock()
        self._http = Http("Microsoft sign-in")

    def __call__(self) -> str:
        with self._lock:
            if time.time() < self._expires - 60:
                return self._value
            s = get_settings()
            r = self._http.request(
                "POST",
                f"{s.microsoft_login_base.rstrip('/')}/{quote(s.microsoft_tenant_id)}/oauth2/v2.0/token",
                auth=False,
                data={
                    "grant_type": "client_credentials",
                    "client_id": s.microsoft_client_id,
                    "client_secret": s.microsoft_client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            if r.status_code >= 400:
                raise ConnectorError("Microsoft refused the app credentials. Check the tenant id, client id and secret.")
            body = r.json()
            self._value, self._expires = body["access_token"], time.time() + int(body.get("expires_in", 3600))
            return self._value


class Microsoft365Connector:
    provider = "microsoft"

    def __init__(self, config: dict, *, directory: bool = False) -> None:
        if not get_settings().microsoft_configured:
            raise ConnectorError("Microsoft 365 isn't set up on the server (tenant id, client id and secret).")
        self.config = config
        self.http = Http("Microsoft 365", token_fn=_Token())
        self.graph = get_settings().microsoft_graph_base.rstrip("/")
        self._drive_id: str | None = None

    # ------------------------------------------------------------------ helpers
    def _get(self, path: str, **params) -> dict:
        return self.http.json("GET", path if path.startswith("http") else self.graph + path, params=params or None)

    def _pages(self, path: str, **params) -> Iterator[dict]:
        body = self._get(path, **params)
        while True:
            yield from body.get("value", [])
            nxt = body.get("@odata.nextLink")
            if not nxt:
                return
            body = self._get(nxt)

    def drive_id(self) -> str:
        if self._drive_id:
            return self._drive_id
        c = self.config
        if c.get("drive_id"):
            self._drive_id = str(c["drive_id"])
        elif c.get("site_url"):
            u = urlparse(str(c["site_url"]))
            if not u.hostname:
                raise ConnectorError("The SharePoint site URL isn't valid")
            site = self._get(f"/sites/{u.hostname}:{u.path.rstrip('/') or '/'}")
            library = str(c.get("library") or "Documents").lower()
            drives = list(self._pages(f"/sites/{site['id']}/drives"))
            match = next((d for d in drives if d.get("name", "").lower() == library), None)
            if match is None:
                names = ", ".join(d.get("name", "") for d in drives) or "none"
                raise ConnectorError(f"No library called “{c.get('library') or 'Documents'}” on that site (found: {names})")
            self._drive_id = match["id"]
        elif c.get("user_email"):
            self._drive_id = self._get(f"/users/{quote(str(c['user_email']))}/drive")["id"]
        else:
            raise ConnectorError("Choose a document library: a drive id, a SharePoint site URL or a person's OneDrive")
        return self._drive_id

    # ------------------------------------------------------------------ files
    def list_files(self) -> Iterator[RemoteFile]:
        drive = self.drive_id()
        folder = str(self.config.get("folder_path") or "").strip("/")
        start = self._get(f"/drives/{drive}/root:/{quote(folder)}:" if folder else f"/drives/{drive}/root")
        if "folder" not in start:
            raise ConnectorError(f"“{folder}” is a file, not a folder")
        queue: deque[tuple[str, str]] = deque([(start["id"], folder)])
        select = "id,name,file,folder,lastModifiedDateTime,size,webUrl,eTag,cTag"
        while queue:
            item_id, path = queue.popleft()
            for it in self._pages(f"/drives/{drive}/items/{item_id}/children", **{"$select": select, "$top": "200"}):
                if "folder" in it:
                    queue.append((it["id"], f"{path}/{it['name']}" if path else it["name"]))
                    continue
                if "file" not in it:
                    continue  # notebooks, packages and other non-files
                modified = it.get("lastModifiedDateTime")
                yield RemoteFile(
                    external_id=it["id"],
                    name=it["name"],
                    path=path,
                    mime_type=(it.get("file") or {}).get("mimeType", ""),
                    version_key=it.get("cTag") or it.get("eTag") or modified or "",
                    modified=datetime.fromisoformat(modified.replace("Z", "+00:00")) if modified else None,
                    size=it.get("size"),
                    url=it.get("webUrl"),
                )

    def fetch(self, f: RemoteFile) -> tuple[bytes, str]:
        drive = self.drive_id()
        ext = PurePath(f.name).suffix.lower()
        if ext in SUPPORTED_EXTENSIONS:
            r = self.http.request("GET", f"{self.graph}/drives/{drive}/items/{f.external_id}/content")
            name = f.name
        elif ext in CONVERT_TO_PDF:
            r = self.http.request("GET", f"{self.graph}/drives/{drive}/items/{f.external_id}/content", params={"format": "pdf"})
            name = f.name + ".pdf"
        else:
            raise ConnectorError(f"“{f.name}” isn't a format VaultMind can read")
        if r.status_code >= 400:
            raise ConnectorError(f"Couldn't download “{f.name}” (HTTP {r.status_code})")
        return r.content, name

    def grants(self, f: RemoteFile) -> list[Grant] | None:
        out: list[Grant] = []
        for p in self._pages(f"/drives/{self.drive_id()}/items/{f.external_id}/permissions"):
            link = p.get("link") or {}
            if link.get("scope") in ("organization", "anonymous"):
                out.append(Grant("company"))
            identities = [p.get("grantedToV2")] + list(p.get("grantedToIdentitiesV2") or [])
            for ident in filter(None, identities):
                user, group = ident.get("user"), ident.get("group")
                if user and (user.get("email") or user.get("id")):
                    out.append(
                        Grant(
                            "user",
                            (user.get("email") or "").lower(),
                            subject=user.get("id", ""),
                            name=user.get("displayName", ""),
                        )
                    )
                elif group and group.get("id"):
                    out.append(Grant("group", group["id"], name=group.get("displayName", "")))
                elif ident.get("siteGroup") or ident.get("siteUser"):
                    site = ident.get("siteGroup") or ident.get("siteUser")
                    out.append(Grant("unresolved", site.get("displayName", "SharePoint group")))
        return out

    # ------------------------------------------------------------------ directory
    def directory(self) -> tuple[list[DirectoryUser], list[DirectoryGroup]]:
        users = []
        fields = "id,displayName,mail,userPrincipalName,jobTitle,department,accountEnabled"
        for u in self._pages("/users", **{"$select": fields, "$top": "999"}):
            email = (u.get("mail") or u.get("userPrincipalName") or "").lower()
            if not email or "#ext#" in email:
                continue  # guests and accounts without a mailbox
            users.append(
                DirectoryUser(
                    external_id=u["id"],
                    email=email,
                    name=u.get("displayName") or email,
                    title=u.get("jobTitle") or "",
                    department=u.get("department") or "",
                    active=bool(u.get("accountEnabled", True)),
                )
            )
        groups = []
        for g in self._pages("/groups", **{"$select": "id,displayName,mail", "$top": "999"}):
            member_ids = [
                m["id"]
                for m in self._pages(f"/groups/{g['id']}/transitiveMembers", **{"$select": "id", "$top": "999"})
                if m.get("@odata.type", "#microsoft.graph.user") == "#microsoft.graph.user"
            ]
            groups.append(DirectoryGroup(external_id=g["id"], name=g.get("displayName") or g["id"], member_ids=member_ids))
        return users, groups
