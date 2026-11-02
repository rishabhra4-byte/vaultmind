import os
import tempfile
import uuid

# Configure an isolated database and storage folder *before* the app is imported.
_tmp = tempfile.mkdtemp(prefix="vaultmind-test-")
os.environ.setdefault("VAULTMIND_DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["VAULTMIND_STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["VAULTMIND_ANTHROPIC_API_KEY"] = ""
os.environ["VAULTMIND_JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256"
os.environ["VAULTMIND_SEED_DEMO_DATA"] = "true"
os.environ["VAULTMIND_ENV"] = "development"
os.environ["VAULTMIND_SCHEDULER_ENABLED"] = "false"  # tests run background jobs explicitly

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import llm  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.deps import chat_limiter, login_limiter_account, login_limiter_ip  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import DEMO_PASSWORD, seed  # noqa: E402
from app.services import rebuild_index  # noqa: E402


class FakeLLM:
    """Records what the assistant sends and replies with a canned answer."""

    name = "fake"

    def __init__(self, reply: str = "Here you go.") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024, *, timeout: float | None = None) -> str:
        self.calls.append({"system": system, "messages": messages})
        return self.reply

    def stream(self, system: str, messages: list[dict], max_tokens: int = 1024):
        """The canned answer in a few pieces, as a streaming model would send it."""
        self.calls.append({"system": system, "messages": messages, "streamed": True})
        words = self.reply.split(" ")
        for i in range(0, len(words), 3):
            yield " ".join(words[i : i + 3]) + (" " if i + 3 < len(words) else "")

    @property
    def last_prompt(self) -> str:
        call = self.calls[-1]
        return call["system"] + "\n".join(m["content"] for m in call["messages"])


def _stamp_head() -> None:
    """The tables come straight from the models, so they're at the latest migration already."""
    from alembic import command

    from app.migrations import alembic_config

    cfg = alembic_config()
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.stamp(cfg, "head")


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    _stamp_head()
    with SessionLocal() as db:
        seed(db)
        rebuild_index(db)
    chat_limiter.reset()
    login_limiter_account.reset()
    login_limiter_ip.reset()
    llm.set_llm(None)  # offline by default; tests opt in to the fake
    yield
    llm.reset_llm()


@pytest.fixture
def empty_database(tmp_path):
    """An engine on a brand-new, empty database of the kind the suite runs on (SQLite, or
    PostgreSQL when VAULTMIND_DATABASE_URL points there), for testing the migrations."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["VAULTMIND_DATABASE_URL"])
    if url.get_backend_name() != "postgresql":
        scratch = create_engine(f"sqlite:///{tmp_path}/scratch.db")
        yield scratch
        scratch.dispose()
        return
    name = f"vm_scratch_{uuid.uuid4().hex[:12]}"
    server = create_engine(url, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    scratch = create_engine(url.set(database=name))
    try:
        yield scratch
    finally:
        scratch.dispose()
        with server.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def login(client):
    def _login(key: str) -> dict:
        r = client.post("/api/auth/login", json={"email": f"{key}@northwind.example", "password": DEMO_PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return _login


@pytest.fixture
def fake_llm():
    fake = FakeLLM()
    llm.set_llm(fake)
    return fake


@pytest.fixture
def doc_id(client, login):
    """Look up a seeded document id by title, as the CEO (who can see all non-restricted docs)."""
    headers = login("dev")  # admin sees everything

    def _find(title: str) -> int:
        docs = client.get("/api/documents", headers=headers).json()
        return next(d["id"] for d in docs if d["title"] == title)

    return _find
