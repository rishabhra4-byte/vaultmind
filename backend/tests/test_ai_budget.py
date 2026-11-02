"""Daily limits on AI use: company-wide and per person. Past a limit, answers are still given,
built offline, and say why."""

from datetime import date

from conftest import FakeLLM

from app import llm
from app.ai_budget import BudgetedLLM, DailyBudget
from app.config import get_settings

LEAVE = "How many days of earned leave do I get?"


def _ask(client, headers, message=LEAVE, **extra):
    r = client.post("/api/assistant/chat", headers=headers, json={"message": message, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_company_wide_limit_falls_back_to_offline_answers(client, login):
    fake = FakeLLM()
    llm.set_llm(BudgetedLLM(fake, DailyBudget(2)))
    headers = login("priya")
    assert [_ask(client, headers)["reply"]["meta"]["mode"] for _ in range(2)] == ["ai", "ai"]
    third = _ask(client, headers)["reply"]
    assert third["meta"]["mode"] == "offline"
    assert third["meta"]["fallback"]["reason"] == "budget"
    assert "limit of AI answers for the whole company" in third["meta"]["fallback"]["message"]
    assert "24 days" in third["content"], "still answered, from the passages"
    assert len(fake.calls) == 2


def test_the_budget_starts_again_each_day():
    budget = DailyBudget(1)
    assert budget.take(date(2026, 11, 1)) and not budget.take(date(2026, 11, 1))
    assert budget.take(date(2026, 11, 2))


def test_the_per_person_limit(client, login, fake_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_daily_limit_per_person", 1)
    priya = login("priya")
    first = _ask(client, priya)
    assert first["reply"]["meta"]["mode"] == "ai"
    second = _ask(client, priya)["reply"]
    assert second["meta"]["mode"] == "offline" and second["meta"]["fallback"]["reason"] == "person_budget"
    assert "You've used today's AI answers" in second["meta"]["fallback"]["message"]

    # Deleting conversations doesn't reset it (it's counted from the activity log)...
    client.delete(f"/api/assistant/conversations/{first['conversation']['id']}", headers=priya)
    assert _ask(client, priya)["reply"]["meta"]["mode"] == "offline"
    # ...and other people have their own allowance.
    assert _ask(client, login("rohan"))["reply"]["meta"]["mode"] == "ai"


def test_no_limits_by_default(client, login, fake_llm):
    headers = login("priya")
    assert all(_ask(client, headers)["reply"]["meta"]["mode"] == "ai" for _ in range(5))


def test_a_configured_limit_wraps_the_real_client(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "anthropic_api_key", "sk-ant-test-not-a-real-key")
    monkeypatch.setattr(s, "ai_daily_limit", 50)
    llm.reset_llm()
    client = llm.get_llm()
    assert isinstance(client, BudgetedLLM) and client.budget.limit == 50
    llm.reset_llm()
    monkeypatch.setattr(s, "ai_daily_limit", 0)
    assert not isinstance(llm.get_llm(), BudgetedLLM)
    llm.reset_llm()


def test_admins_see_todays_ai_use(client, login, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_daily_limit", 100)
    monkeypatch.setattr(get_settings(), "ai_daily_limit_per_person", 10)
    llm.set_llm(BudgetedLLM(FakeLLM(), DailyBudget(100)))
    _ask(client, login("priya"))
    ai = client.get("/api/admin/operations", headers=login("dev")).json()["ai"]
    assert ai["daily_limit"] == 100 and ai["daily_limit_per_person"] == 10 and ai["used_today"] == 1
