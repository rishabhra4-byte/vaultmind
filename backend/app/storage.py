"""Content-addressed blob storage on local disk.

Files are stored under their SHA-256, so identical uploads share one blob. Swapping this
for S3 or GCS only means reimplementing these three functions.
"""

import hashlib
from pathlib import Path

from .config import get_settings


def _root() -> Path:
    root = Path(get_settings().storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(data: bytes) -> tuple[str, str]:
    digest = sha256(data)
    key = f"{digest[:2]}/{digest}"
    path = _root() / key
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic on the same filesystem
    return key, digest


def get(key: str) -> bytes:
    return (_root() / key).read_bytes()
