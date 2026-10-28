"""What every connector provides, independent of where the files live."""

import logging
import random
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)


class ConnectorError(Exception):
    """A problem worth showing to the admin as-is (bad credentials, missing folder...)."""


@dataclass
class RemoteFile:
    external_id: str
    name: str
    path: str  # folder path inside the source, for rules and display, e.g. "Engineering/Guides"
    mime_type: str = ""
    version_key: str = ""  # changes whenever the content changes (checksum, eTag, modified time)
    modified: datetime | None = None
    size: int | None = None
    url: str | None = None  # "open in Drive / SharePoint"
    extra: dict = field(default_factory=dict)  # connector-specific details needed to fetch it


@dataclass
class Grant:
    """One entry of a file's sharing, before it's matched to VaultMind people and groups.

    kind: "user" (value = email, or `subject` = provider user id), "group" (external group id or
    email), "domain" (email domain), "company" (everyone in the organisation / anyone with the link).
    """

    kind: str
    value: str = ""
    subject: str = ""  # provider id when there's no email (Microsoft users)
    name: str = ""


@dataclass
class DirectoryUser:
    external_id: str
    email: str
    name: str
    title: str = ""
    department: str = ""
    active: bool = True


@dataclass
class DirectoryGroup:
    external_id: str
    name: str
    member_ids: list[str] = field(default_factory=list)  # DirectoryUser.external_id values
    member_emails: list[str] = field(default_factory=list)


class Connector(Protocol):
    provider: str  # "folder" | "google" | "microsoft"

    def list_files(self) -> Iterator[RemoteFile]: ...

    def fetch(self, f: RemoteFile) -> tuple[bytes, str]:
        """The file's bytes, and a file name whose extension tells the text extractor the format."""
        ...

    def grants(self, f: RemoteFile) -> list[Grant] | None:
        """The file's sharing, or None when the connector can't read sharing (rules mode only)."""
        ...

    def directory(self) -> tuple[list[DirectoryUser], list[DirectoryGroup]]: ...


class Http:
    """A small HTTP client for provider APIs: timeouts, retries on 429/5xx with backoff, paging."""

    def __init__(self, name: str, token_fn=None) -> None:
        self.name = name
        self._token_fn = token_fn
        self._client = httpx.Client(timeout=get_settings().connector_timeout_seconds, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, url: str, *, auth: bool = True, attempts: int = 4, **kwargs) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        for attempt in range(1, attempts + 1):
            if auth and self._token_fn:
                headers["Authorization"] = f"Bearer {self._token_fn()}"
            try:
                r = self._client.request(method, url, headers=headers, **kwargs)
            except httpx.TimeoutException as exc:
                if attempt == attempts:
                    raise ConnectorError(f"{self.name} didn't respond in time") from exc
                self._sleep(attempt)
                continue
            except httpx.HTTPError as exc:
                if attempt == attempts:
                    raise ConnectorError(f"Couldn't reach {self.name}: {exc.__class__.__name__}") from exc
                self._sleep(attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == attempts:
                    raise ConnectorError(f"{self.name} is busy or failing (HTTP {r.status_code}); try again later")
                self._sleep(attempt, r.headers.get("Retry-After"))
                continue
            if r.status_code in (401, 403):
                raise ConnectorError(f"{self.name} refused access (HTTP {r.status_code}). Check the credentials and permissions.")
            return r
        raise ConnectorError(f"{self.name} request failed")  # pragma: no cover

    def json(self, method: str, url: str, **kwargs) -> dict:
        r = self.request(method, url, **kwargs)
        if r.status_code == 404:
            raise ConnectorError(f"{self.name}: not found ({url.split('?')[0].rsplit('/', 2)[-2:]})")
        if r.status_code >= 400:
            raise ConnectorError(f"{self.name} returned HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as exc:
            raise ConnectorError(f"{self.name} returned something that isn't JSON") from exc

    @staticmethod
    def _sleep(attempt: int, retry_after: str | None = None) -> None:
        delay = min(float(retry_after), 30.0) if retry_after and retry_after.isdigit() else 0.5 * 2 ** (attempt - 1)
        time.sleep(delay + random.uniform(0, 0.2))
