"""Small fake HTTP APIs for tests: Google Drive + Admin SDK, Microsoft Graph, Slack.

They implement just enough of each real API (same paths, parameters and response shapes)
for the connectors to be exercised end to end, including paging, redirects and errors.
"""

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import jwt


@dataclass
class Req:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes

    @property
    def form(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.body.decode()).items()}

    @property
    def json(self) -> dict:
        return json.loads(self.body or b"{}")

    @property
    def bearer(self) -> str:
        return self.headers.get("authorization", "").removeprefix("Bearer ")


@dataclass
class Resp:
    status: int = 200
    payload: object = None  # dict -> JSON, bytes -> raw body
    headers: dict = field(default_factory=dict)


class FakeServer:
    def __init__(self, handle: Callable[[Req], Resp]) -> None:
        self.requests: list[Req] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self):
                u = urlparse(self.path)
                req = Req(
                    method=self.command,
                    path=unquote(u.path),
                    query={k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()},
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=self.rfile.read(int(self.headers.get("content-length", 0) or 0)),
                )
                server.requests.append(req)
                try:
                    resp = handle(req)
                except Exception as exc:  # surface fake bugs as 500s with a message
                    resp = Resp(500, {"error": repr(exc)})
                data = resp.payload if isinstance(resp.payload, bytes) else json.dumps(resp.payload or {}).encode()
                self.send_response(resp.status)
                ctype = "application/octet-stream" if isinstance(resp.payload, bytes) else "application/json"
                self.send_header("content-type", resp.headers.pop("content-type", ctype))
                for k, v in resp.headers.items():
                    self.send_header(k, v)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PATCH = do_DELETE = _serve  # noqa: N815

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


def tiny_pdf(text: str) -> bytes:
    """A one-page PDF with a line of text, enough for the PDF text extractor."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


# --------------------------------------------------------------------------- Google


GFOLDER = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"


class FakeGoogle:
    """Drive v3 (files, export, media, permissions), Admin SDK directory, and the token endpoint."""

    PAGE = 2  # small pages, so paging is always exercised

    def __init__(self, public_key, sa_email: str, admin_email: str) -> None:
        self.public_key, self.sa_email, self.admin_email = public_key, sa_email, admin_email
        self.files: dict[str, dict] = {}
        self.users: list[dict] = []
        self.groups: dict[str, dict] = {}  # email -> {"name", "members": [emails]}
        self.scopes: list[str] = []
        self.fail_token = False
        self.server = FakeServer(self.handle)
        self.url = self.server.url

    def add(self, fid, name, parent=None, mime="text/plain", content=b"", perms=(), modified="2026-10-01T10:00:00Z"):
        self.files[fid] = {
            "id": fid,
            "name": name,
            "mimeType": mime,
            "parents": [parent] if parent else [],
            "content": content,
            "permissions": list(perms),
            "modifiedTime": modified,
            "trashed": False,
        }

    def _page(self, items: list, key: str, req: Req) -> Resp:
        start = int(req.query.get("pageToken") or 0)
        body = {key: items[start : start + self.PAGE]}
        if start + self.PAGE < len(items):
            body["nextPageToken"] = str(start + self.PAGE)
        return Resp(200, body)

    def handle(self, req: Req) -> Resp:
        if req.path == "/token":
            if self.fail_token:
                return Resp(400, {"error": "unauthorized_client"})
            claims = jwt.decode(req.form["assertion"], self.public_key, algorithms=["RS256"], audience=self.url + "/token")
            assert claims["iss"] == self.sa_email and claims["sub"] == self.admin_email
            self.scopes = claims["scope"].split()
            return Resp(200, {"access_token": "g-token", "expires_in": 3600, "token_type": "Bearer"})
        if req.bearer != "g-token":
            return Resp(401, {"error": {"code": 401, "message": "Invalid Credentials"}})
        p = req.path
        if p == "/drive/v3/files":
            parent = req.query["q"].split("'")[1]
            kids = [
                {k: v for k, v in f.items() if k not in ("content", "permissions", "parents")}
                | ({"md5Checksum": str(hash(f["content"]))} if f["mimeType"] != GDOC and f["mimeType"] != GFOLDER else {})
                | {"webViewLink": f"https://drive.example/{f['id']}"}
                for f in self.files.values()
                if parent in f["parents"] and not f["trashed"]
            ]
            return self._page(sorted(kids, key=lambda f: f["name"]), "files", req)
        if p.startswith("/drive/v3/files/") and p.endswith("/permissions"):
            fid = p.split("/")[4]
            return self._page(self.files[fid]["permissions"], "permissions", req)
        if p.startswith("/drive/v3/files/") and p.endswith("/export"):
            f = self.files[p.split("/")[4]]
            assert f["mimeType"] == GDOC and req.query["mimeType"] == "text/plain"
            return Resp(200, f["content"])
        if p.startswith("/drive/v3/files/"):
            f = self.files.get(p.split("/")[4])
            if f is None:
                return Resp(404, {"error": {"code": 404, "message": "File not found"}})
            if req.query.get("alt") == "media":
                return Resp(200, f["content"])
            return Resp(200, {"id": f["id"], "name": f["name"], "mimeType": f["mimeType"]})
        if p == "/admin/directory/v1/users":
            return self._page(self.users, "users", req)
        if p == "/admin/directory/v1/groups":
            return self._page([{"email": e, "name": g["name"], "id": e} for e, g in self.groups.items()], "groups", req)
        if p.startswith("/admin/directory/v1/groups/") and p.endswith("/members"):
            email = p.split("/")[5]
            members = [{"email": m, "type": "USER", "status": "ACTIVE"} for m in self.groups[email]["members"]]
            return self._page(members, "members", req)
        return Resp(404, {"error": {"code": 404, "message": f"no route {p}"}})


# --------------------------------------------------------------------------- Microsoft Graph


class FakeGraph:
    """Graph v1.0 drives (children, content with PDF conversion, permissions), users, groups, sites."""

    PAGE = 2

    def __init__(self, tenant: str, client_id: str, secret: str) -> None:
        self.tenant, self.client_id, self.secret = tenant, client_id, secret
        self.items: dict[str, dict] = {"root": {"id": "root", "name": "root", "folder": {}, "parent": None}}
        self.users: list[dict] = []
        self.groups: dict[str, dict] = {}  # id -> {"displayName", "members": [user ids]}
        self.throttle_next = 0  # answer this many requests with 429 first
        self.server = FakeServer(self.handle)
        self.url = self.server.url
        self.graph = self.url + "/v1.0"

    def add(self, iid, name, parent="root", content=None, perms=(), ctag="c1"):
        item = {"id": iid, "name": name, "parent": parent, "perms": list(perms), "cTag": ctag}
        if content is None:
            item["folder"] = {"childCount": 0}
        else:
            item["file"] = {"mimeType": "application/octet-stream"}
            item["content"] = content
        self.items[iid] = item

    def _public(self, it: dict) -> dict:
        out = {k: v for k, v in it.items() if k in ("id", "name", "folder", "file", "cTag")}
        out |= {"lastModifiedDateTime": "2026-10-01T10:00:00Z", "webUrl": f"https://contoso.sharepoint.example/{it['id']}"}
        if "content" in it:
            out["size"] = len(it["content"])
        return out

    def _page(self, values: list, req: Req) -> Resp:
        start = int(req.query.get("$skiptoken") or 0)
        body = {"value": values[start : start + self.PAGE]}
        if start + self.PAGE < len(values):
            body["@odata.nextLink"] = f"{self.graph}{req.path.removeprefix('/v1.0')}?$skiptoken={start + self.PAGE}"
        return Resp(200, body)

    def handle(self, req: Req) -> Resp:
        if req.path == f"/{self.tenant}/oauth2/v2.0/token":
            f = req.form
            if f.get("client_id") != self.client_id or f.get("client_secret") != self.secret:
                return Resp(401, {"error": "invalid_client"})
            return Resp(200, {"access_token": "m-token", "expires_in": 3600, "token_type": "Bearer"})
        if req.path.startswith("/download/"):
            it = self.items[req.path.split("/")[2]]
            if req.query.get("format") == "pdf":
                return Resp(200, tiny_pdf(it["content"].decode()))
            return Resp(200, it["content"])
        if req.bearer != "m-token":
            return Resp(401, {"error": {"code": "InvalidAuthenticationToken"}})
        if self.throttle_next > 0:
            self.throttle_next -= 1
            return Resp(429, {"error": {"code": "TooManyRequests"}}, {"Retry-After": "0"})
        p = req.path.removeprefix("/v1.0")
        parts = p.strip("/").split("/")
        if p.startswith("/sites/") and ":" in p:
            return Resp(200, {"id": "site-1", "displayName": "HR site"})
        if p == "/sites/site-1/drives":
            return Resp(200, {"value": [{"id": "drive-hr", "name": "Documents"}, {"id": "drive-x", "name": "Archive"}]})
        if parts[0] == "drives" and len(parts) >= 3:
            if parts[2] == "root" and len(parts) == 3:
                return Resp(200, self._public(self.items["root"]))
            if parts[2].startswith("root:"):  # /drives/{d}/root:/{path}:
                path = p.split("root:/", 1)[1].rstrip(":")
                node = "root"
                for name in path.split("/"):
                    node = next(i for i, it in self.items.items() if it["parent"] == node and it["name"] == name)
                return Resp(200, self._public(self.items[node]))
            iid = parts[3]
            if parts[4] == "children":
                kids = sorted((it for it in self.items.values() if it["parent"] == iid), key=lambda it: it["name"])
                return self._page([self._public(k) for k in kids], req)
            if parts[4] == "content":
                q = "?format=pdf" if req.query.get("format") == "pdf" else ""
                return Resp(302, b"", {"Location": f"{self.url}/download/{iid}{q}"})
            if parts[4] == "permissions":
                return Resp(200, {"value": self.items[iid]["perms"]})
        if p == "/users":
            return self._page(self.users, req)
        if p == "/groups":
            return self._page([{"id": gid, "displayName": g["displayName"]} for gid, g in self.groups.items()], req)
        if parts[0] == "groups" and parts[2] == "transitiveMembers":
            members = [{"@odata.type": "#microsoft.graph.user", "id": uid} for uid in self.groups[parts[1]]["members"]]
            members.append({"@odata.type": "#microsoft.graph.group", "id": "nested-group"})
            return self._page(members, req)
        return Resp(404, {"error": {"code": "itemNotFound", "message": p}})


def wait_until(check: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")
