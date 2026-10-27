"""Thin wrapper around the language model so the rest of the app doesn't depend on a vendor SDK.

`get_llm()` returns None when no API key is configured, and the assistant falls back to
offline mode. Tests swap in a fake with `set_llm()`.
"""

import logging
from typing import Protocol

from .config import get_settings

log = logging.getLogger(__name__)


class LLM(Protocol):
    name: str

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024) -> str: ...


class AnthropicLLM:
    def __init__(self, api_key: str, model: str) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.name = f"claude ({model})"

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1024) -> str:
        response = self._client.messages.create(model=self.model, system=system, messages=messages, max_tokens=max_tokens)
        return "".join(block.text for block in response.content if getattr(block, "type", "") == "text").strip()


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
                _cached = AnthropicLLM(s.anthropic_api_key, s.llm_model)
            except Exception:  # missing SDK or bad config shouldn't take the app down
                log.exception("Could not initialise the LLM client; running in offline mode")
                return None
    return _cached
