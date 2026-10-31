"""Prometheus metrics behind a token, request ids, and alert rules that only use real metrics."""

import re
from pathlib import Path

import yaml

from app import metrics
from app.config import get_settings

TOKEN = "scrape-token-for-tests-0123456789"


def _scrape(client, monkeypatch) -> str:
    monkeypatch.setattr(get_settings(), "metrics_token", TOKEN)
    r = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain; version=0.0.4")
    return r.text


def _value(text: str, series: str) -> float:
    for line in text.splitlines():
        if line.startswith(series + " "):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"{series} not in the scrape")


def test_metrics_need_the_token(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_token", "")
    assert client.get("/metrics").status_code == 404  # off without a token
    monkeypatch.setattr(get_settings(), "metrics_token", TOKEN)
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_requests_answers_and_sign_ins_are_counted(client, login, monkeypatch):
    metrics.registry.reset()
    headers = login("priya")
    client.post("/api/auth/login", json={"email": "priya@northwind.example", "password": "wrong"})
    client.post("/api/assistant/chat", headers=headers, json={"message": "How many days of earned leave do I get?"})
    client.post("/api/assistant/chat", headers=headers, json={"message": "What dental insurance do we have?"})
    client.get("/api/documents/9999", headers=headers)
    text = _scrape(client, monkeypatch)

    assert _value(text, 'vaultmind_sign_ins_total{method="password",result="ok"}') == 1
    assert _value(text, 'vaultmind_sign_ins_total{method="password",result="failed"}') == 1
    assert _value(text, 'vaultmind_answers_total{evidence="enough",mode="offline"}') == 1
    assert _value(text, 'vaultmind_answers_total{evidence="not_enough",mode="offline"}') == 1
    chat = 'route="/api/assistant/chat",status="2xx"'
    assert _value(text, f'vaultmind_http_requests_total{{method="POST",{chat}}}') == 2
    assert _value(text, 'vaultmind_http_requests_total{method="GET",route="/api/documents/{doc_id}",status="4xx"}') == 1
    assert _value(text, 'vaultmind_http_request_duration_seconds_count{route="/api/assistant/chat"}') == 2
    assert _value(text, "vaultmind_database_up") == 1
    assert _value(text, "vaultmind_documents") == 28
    assert _value(text, "vaultmind_sessions_open") == 1
    assert _value(text, "vaultmind_scanner_configured") == 0 and _value(text, "vaultmind_backup_configured") == 0
    # Labels come from route templates, never raw paths, so ids and words can't flood the series.
    assert "/api/documents/9999" not in text


def test_refused_opens_are_counted(client, login, doc_id, monkeypatch):
    metrics.registry.reset()
    client.get(f"/api/documents/{doc_id('Salary Bands 2026')}", headers=login("aarav"))
    assert _value(_scrape(client, monkeypatch), 'vaultmind_access_denied_total{action="document.view_denied"}') == 1


def test_every_response_has_a_request_id_and_keeps_a_sensible_one(client):
    r = client.get("/api/health")
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["X-Request-ID"])
    assert client.get("/api/health", headers={"X-Request-ID": "trace-abc-12345"}).headers["X-Request-ID"] == "trace-abc-12345"
    odd = client.get("/api/health", headers={"X-Request-ID": "bad id\nwith newline"}).headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", odd)


def test_log_lines_carry_the_request_id():
    import logging

    import app.main  # noqa: F401  (sets up logging)

    assert any(isinstance(f, metrics.RequestIdFilter) for h in logging.getLogger().handlers for f in h.filters)
    record = logging.LogRecord("vaultmind", logging.INFO, __file__, 1, "Searching", None, None)
    token = metrics.request_id.set("trace-abc-12345")
    try:
        assert metrics.RequestIdFilter().filter(record) and record.request_id == "trace-abc-12345"
    finally:
        metrics.request_id.reset(token)
    outside = logging.LogRecord("vaultmind", logging.INFO, __file__, 1, "Starting", None, None)
    metrics.RequestIdFilter().filter(outside)
    assert outside.request_id == "-"


def test_label_values_are_escaped():
    assert metrics._labels({"a": 'x"y\\z\nq'}) == '{a="x\\"y\\\\z\\nq"}'


def test_alert_rules_only_use_metrics_vaultmind_exports(client, monkeypatch):
    rules = yaml.safe_load((Path(__file__).resolve().parents[2] / "deploy" / "prometheus-alerts.yml").read_text())
    exprs = " ".join(r["expr"] for g in rules["groups"] for r in g["rules"])
    used = set(re.findall(r"\bvaultmind_[a-z_]+", exprs))
    exported = set(metrics.registry._help)
    exported |= {name for name, *_ in metrics.gauges()}
    exported |= {
        "vaultmind_scanner_up",
        "vaultmind_backup_last_success_timestamp_seconds",
        "vaultmind_backup_last_verified",
    }  # only present when a scanner or backups are set up
    exported |= {f"{h}_bucket" for h in exported} | {f"{h}_count" for h in exported}
    assert used <= exported, used - exported
    for group in rules["groups"]:
        for rule in group["rules"]:
            assert rule["labels"]["severity"] in ("critical", "warning")
            assert rule["annotations"]["runbook"].startswith("docs/operations.md#")
