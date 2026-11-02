"""The real Anthropic SDK client, pointed at a local fake of the Messages API.

This exercises everything except Anthropic's servers themselves: the API key header, request
shape, retries with Retry-After, timeouts, connection failures, error parsing, and how each
failure becomes an offline answer that says why. It is not a substitute for one real call
with a valid key (see docs/production-readiness.md).
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import llm
from app.llm import AnthropicLLM, LLMError

KEY = "fake-key-for-the-local-test-server"  # not a real key; the fake server checks it


class FakeAnthropic:
    def __init__(self) -> None:
        self.mode = "ok"
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                fake.requests.append(
                    {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
                )
                mode = fake.mode
                if body.get("stream") and mode in ("ok", "truncated"):
                    return self._sse(
                        ["Leave is ", "24 days ", "a year [D1]."], "max_tokens" if mode == "truncated" else "end_turn"
                    )
                if mode == "slow":
                    time.sleep(2)
                    return self._json(200, _message("late"))
                if mode == "ok":
                    return self._json(200, _message("Leave is 24 days a year [D1]."))
                if mode == "empty":
                    return self._json(200, {**_message(""), "content": []})
                if mode == "truncated":
                    return self._json(200, {**_message("Partial answer"), "stop_reason": "max_tokens"})
                errors = {
                    "401": (401, "authentication_error", "invalid x-api-key"),
                    "429": (429, "rate_limit_error", "Number of requests has exceeded your rate limit"),
                    "529": (529, "overloaded_error", "Overloaded"),
                    "500": (500, "api_error", "Internal server error"),
                    "credit": (400, "invalid_request_error", "Your credit balance is too low to access the API."),
                }
                status, kind, message = errors[mode]
                self._json(status, {"type": "error", "error": {"type": kind, "message": message}}, retry_after="0")

            def _sse(self, pieces, stop_reason):
                """A streamed answer, in the Messages API's server-sent events format."""
                events = [
                    ("message_start", {"type": "message_start", "message": {**_message(""), "stop_reason": None}}),
                    (
                        "content_block_start",
                        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                    ),
                    *(
                        (
                            "content_block_delta",
                            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}},
                        )
                        for t in pieces
                    ),
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    (
                        "message_delta",
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                            "usage": {"output_tokens": 9},
                        },
                    ),
                    ("message_stop", {"type": "message_stop"}),
                ]
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for name, data in events:
                    self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                    self.wfile.flush()

            def _json(self, status, payload, retry_after=None):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                if retry_after is not None:
                    self.send_header("retry-after", retry_after)
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


def _message(text: str) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


@pytest.fixture
def fake():
    f = FakeAnthropic()
    yield f
    f.close()


def _client(fake, **kw) -> AnthropicLLM:
    return AnthropicLLM(KEY, "claude-test", base_url=fake.url, **{"timeout": 5.0, "max_retries": 2, **kw})


def test_successful_call_sends_the_key_and_the_permission_filtered_prompt(fake, client, login):
    llm.set_llm(_client(fake))
    r = client.post("/api/assistant/chat", headers=login("neha"), json={"message": "How many days of earned leave do I get?"})
    reply = r.json()["reply"]
    assert reply["meta"]["mode"] == "ai" and "fallback" not in reply["meta"]
    req = fake.requests[-1]
    assert req["path"] == "/v1/messages"
    # SDK 2.x sends the key as a bearer token; older SDKs used x-api-key. Either way, it's sent.
    sent = req["headers"].get("x-api-key") or req["headers"].get("authorization", "").removeprefix("Bearer ")
    assert sent == KEY
    assert req["headers"]["anthropic-version"]
    assert req["body"]["model"] == "claude-test"
    assert "Employee Handbook 2026" in json.dumps(req["body"])
    # The key never leaves the server.
    assert KEY not in r.text
    assert KEY not in client.get("/api/meta").text


@pytest.mark.parametrize(
    ("mode", "reason", "attempts"),
    [
        ("401", "auth", 1),  # not retried
        ("429", "rate_limited", 3),  # retried twice, honouring Retry-After
        ("529", "unavailable", 3),
        ("500", "unavailable", 3),
        ("credit", "quota", 1),
    ],
)
def test_provider_errors_fall_back_to_offline_answers_that_say_why(fake, client, login, mode, reason, attempts):
    fake.mode = mode
    llm.set_llm(_client(fake))
    reply = client.post("/api/assistant/chat", headers=login("neha"), json={"message": "How many days of earned leave?"}).json()[
        "reply"
    ]
    assert reply["meta"]["mode"] == "offline"
    assert reply["meta"]["fallback"]["reason"] == reason
    assert "matching passages" in reply["meta"]["fallback"]["message"]
    assert "24 days of earned leave" in reply["content"], "the offline answer still works"
    assert len(fake.requests) == attempts


def test_timeouts_are_bounded(fake):
    fake.mode = "slow"
    started = time.monotonic()
    with pytest.raises(LLMError) as exc:
        _client(fake, timeout=0.5, max_retries=0).complete("s", [{"role": "user", "content": "hi"}])
    assert exc.value.reason == "timeout"
    assert time.monotonic() - started < 1.8


def test_unreachable_provider_is_a_network_error():
    dead = AnthropicLLM(KEY, "claude-test", base_url="http://127.0.0.1:9", timeout=1.0, max_retries=0)
    with pytest.raises(LLMError) as exc:
        dead.complete("s", [{"role": "user", "content": "hi"}])
    assert exc.value.reason == "network"


def test_a_rejected_key_is_not_retried_on_every_question(fake):
    fake.mode = "401"
    c = _client(fake)
    for _ in range(3):
        with pytest.raises(LLMError):
            c.complete("s", [{"role": "user", "content": "hi"}])
    assert len(fake.requests) == 1


def test_empty_and_truncated_responses(fake):
    c = _client(fake)
    fake.mode = "empty"
    with pytest.raises(LLMError) as exc:
        c.complete("s", [{"role": "user", "content": "hi"}])
    assert exc.value.reason == "bad_response"
    fake.mode = "truncated"
    assert "cut short" in c.complete("s", [{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------- streaming


def test_streaming_with_the_real_client(fake):
    pieces = list(_client(fake).stream("system", [{"role": "user", "content": "hi"}]))
    assert pieces == ["Leave is ", "24 days ", "a year [D1]."]
    req = fake.requests[-1]
    assert req["body"]["stream"] is True and req["body"]["model"] == "claude-test"


def test_a_streamed_answer_cut_short_says_so(fake):
    fake.mode = "truncated"
    pieces = list(_client(fake).stream("system", [{"role": "user", "content": "hi"}]))
    assert pieces[-1].strip().startswith("(The answer was cut short")


def test_streaming_errors_become_reasons(fake):
    fake.mode = "401"
    with pytest.raises(LLMError) as err:
        list(_client(fake).stream("system", [{"role": "user", "content": "hi"}]))
    assert err.value.reason == "auth"


def test_the_streaming_endpoint_with_the_real_client(fake, client, login):
    llm.set_llm(_client(fake))
    r = client.post(
        "/api/assistant/chat/stream", headers=login("neha"), json={"message": "How many days of earned leave do I get?"}
    )
    kinds = [b.split("\n", 1)[0] for b in r.text.strip().split("\n\n")]
    assert kinds == ["event: delta"] * 3 + ["event: done"]
    assert KEY not in r.text
