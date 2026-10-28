"""Shared-folder connector: a folder on the server (for example a mounted network share).

Only folders inside the configured roots (VAULTMIND_FOLDER_ROOTS) can be connected, so an
admin can't point VaultMind at the server's own files. Symlinks that lead outside the
connected folder are skipped. Access comes from the source's rules (team and classification
per subfolder), because file-system permissions don't map portably to people.
"""

import fnmatch
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from ..config import get_settings
from .base import ConnectorError, DirectoryGroup, DirectoryUser, Grant, RemoteFile

HIDDEN_PREFIXES = (".", "~$")


def allowed_roots() -> list[Path]:
    from ..seed import DEMO_DRIVE

    roots = [Path(r).expanduser() for r in get_settings().folder_roots]
    if get_settings().demo_enabled:
        roots.append(DEMO_DRIVE)
    return [r.resolve() for r in roots if r.exists()]


def resolve_folder(path: str) -> Path:
    """The connected folder, checked to sit inside one of the allowed roots."""
    if not path or "\x00" in path:
        raise ConnectorError("Enter the folder's path on the server.")
    folder = Path(path).expanduser().resolve()
    roots = allowed_roots()
    if not any(folder == r or folder.is_relative_to(r) for r in roots):
        where = ", ".join(str(r) for r in roots) or "none are set up (VAULTMIND_FOLDER_ROOTS)"
        raise ConnectorError(f"That folder isn't inside an allowed location. Allowed: {where}.")
    if not folder.is_dir():
        raise ConnectorError("That folder doesn't exist on the server.")
    return folder


class FolderConnector:
    provider = "folder"

    def __init__(self, config: dict) -> None:
        self.root = resolve_folder(str(config.get("path", "")))
        self.exclude = [p for p in config.get("exclude", []) if isinstance(p, str)]

    def _excluded(self, rel: str) -> bool:
        return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel.rsplit("/", 1)[-1], pat) for pat in self.exclude)

    def list_files(self) -> Iterator[RemoteFile]:
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(HIDDEN_PREFIXES))
            for name in sorted(filenames):
                if name.startswith(HIDDEN_PREFIXES):
                    continue
                full = Path(dirpath) / name
                real = full.resolve()
                if not real.is_relative_to(self.root) or not real.is_file():
                    continue  # a symlink pointing outside the connected folder
                rel = full.relative_to(self.root).as_posix()
                if self._excluded(rel):
                    continue
                st = real.stat()
                folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
                yield RemoteFile(
                    external_id=rel,
                    name=name,
                    path=folder,
                    version_key=f"{st.st_mtime_ns}:{st.st_size}",
                    modified=datetime.fromtimestamp(st.st_mtime, UTC),
                    size=st.st_size,
                    extra={"full": str(real)},
                )

    def fetch(self, f: RemoteFile) -> tuple[bytes, str]:
        real = Path(f.extra["full"]).resolve()
        if not real.is_relative_to(self.root):
            raise ConnectorError("File moved outside the connected folder")
        return real.read_bytes(), f.name

    def grants(self, f: RemoteFile) -> list[Grant] | None:
        return None

    def directory(self) -> tuple[list[DirectoryUser], list[DirectoryGroup]]:
        return [], []
