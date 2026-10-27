import os
import tempfile

# Configure an isolated database and storage folder *before* the app is imported.
_tmp = tempfile.mkdtemp(prefix="vaultmind-test-")
os.environ.setdefault("VAULTMIND_DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["VAULTMIND_STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["VAULTMIND_ANTHROPIC_API_KEY"] = ""
os.environ["VAULTMIND_JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import llm  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.deps import chat_limiter  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import DEMO_PASSWORD, seed  # noqa: E402
from app.services import rebuild_index  # noqa: E402


class FakeLLM:
    """Records what the assistant sends and replies with a canned answer."""

    name = "fake"

    def __init__(self, reply: str = "Here you go.") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        self.calls.append({"system": system, "messages": messages})
        return self.reply

    @property
    def last_prompt(self) -> str:
        call = self.calls[-1]
        return call["system"] + "\n".join(m["content"] for m in call["messages"])


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
        rebuild_index(db)
    chat_limiter.reset()
    llm.set_llm(None)  # offline by default; tests opt in to the fake
    yield
    llm.reset_llm()


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
