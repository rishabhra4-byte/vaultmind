"""Thin wrapper around the language model so the rest of the app doesn't depend on a vendor SDK.

`get_llm()` returns None when no API key is configured, and the assistant answers in
offline mode. Every provider failure is turned into an `LLMError` with a short reason code,
so the assistant can fall back to offline mode *and say why* in the reply.

Tests swap in a fake with `set_llm()`, or point the real client at a local fake server
through `base_url`.
"""

import logging
import threading
import time
from typing import Protocol

from .config import get_settings

log = logging.getLogger(__name__)

# What the person sees when the assistant falls back to offline mode.
REASONS = {
    "auth": "The AI service rejected VaultMind's API key, so this answer was built from matching passages instead.",
    "quota": "The AI service's usage limit has been reached, so this answer was built from matching passages instead.",
    "rate_limited": "The AI service is busy right now, so this answer was built from matching passages instead.",
    "unavailable": "The AI service is unavailable right now, so this answer was built from matching passages instead.",
    "timeout": "The AI service took too long to respond, so this answer was built from matching passages instead.",
    "network": "VaultMind couldn't reach the AI service, so this answer was built from matching passages instead.",
    "bad_response": "The AI service returned an unusable answer, so this one was built from matching passages instead.",
    "error": "The AI service failed, so this answer was built from matching passages instead.",
}


class LLMError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason if reason in REASONS else "error"


class LLM(Protocol):
    name: str

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024, *, timeout: float | None = None) -> str: ...


def classify(exc: Exception) -> LLMError:
    """Map an Anthropic SDK exception to one of our reason codes."""
    import anthropic

    message = str(exc)
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return LLMError("auth", message)
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("rate_limited", message)
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError("timeout", message)
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("network", message)
    if isinstance(exc, anthropic.BadRequestError) and "credit balance" in message.lower():
        return LLMError("quota", message)
    if isinstance(exc, anthropic.APIStatusError) and (exc.status_code >= 500 or exc.status_code == 529):
        return LLMError("unavailable", message)
    return LLMError("error", message)


class AnthropicLLM:
    # After the key is rejected, stop calling the API for a while instead of failing (and
    # logging) on every single question.
    AUTH_COOLDOWN_SECONDS = 600

    def __init__(self, api_key: str, model: str, base_url: str = "", timeout: float = 30.0, max_retries: int = 2) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, base_url=base_url or None, timeout=timeout, max_retries=max_retries)
        self.model = model
        self.name = f"claude ({model})"
        self._disabled_until = 0.0
        self._lock = threading.Lock()

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024, *, timeout: float | None = None) -> str:
        """`timeout` overrides the default and disables retries (for calls that must stay quick)."""
        if time.monotonic() < self._disabled_until:
            raise LLMError("auth", "skipped: key was rejected recently")
        try:
            client = self._client if timeout is None else self._client.with_options(timeout=timeout, max_retries=0)
            response = client.messages.create(model=self.model, system=system, messages=messages, max_tokens=max_tokens)
        except Exception as exc:
            err = classify(exc)
            if err.reason == "auth":
                with self._lock:
                    self._disabled_until = time.monotonic() + self.AUTH_COOLDOWN_SECONDS
                log.error("AI provider rejected the API key; using offline answers for %ss", self.AUTH_COOLDOWN_SECONDS)
            else:
                log.warning("AI provider call failed (%s): %s", err.reason, exc)
            raise err from exc
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text").strip()
        if not text:
            raise LLMError("bad_response", f"no text content (stop_reason={getattr(response, 'stop_reason', None)})")
        if getattr(response, "stop_reason", None) == "max_tokens":
            text += "\n\n(The answer was cut short. Ask a narrower question for the rest.)"
        return text


_override: LLM | None = None
_cached: LLM | None = None
_override_set = False


def set_llm(llm: LLM | None) -> None:
    """Force a specific LLM (or None for offline mode). Used by tests."""
    global _override, _override_set
    _override, _override_set = llm, True


def reset_llm() -> None:
    global _override, _override_set, _cached
    _override, _override_set, _cached = None, False, None


def get_llm() -> LLM | None:
    global _cached
    if _override_set:
        return _override
    if _cached is None:
        s = get_settings()
        if s.anthropic_api_key:
            try:
                _cached = AnthropicLLM(
                    s.anthropic_api_key,
                    s.llm_model,
                    base_url=s.anthropic_base_url,
                    timeout=s.llm_timeout_seconds,
                    max_retries=s.llm_max_retries,
                )
            except Exception:  # missing SDK or bad config shouldn't take the app down
                log.exception("Could not initialise the LLM client; running in offline mode")
                return None
    return _cached
