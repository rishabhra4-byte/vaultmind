"""A minimal Slack Web API client (the bot token is read from the server's environment)."""

import logging

import httpx

from .config import get_settings

log = logging.getLogger(__name__)


class SlackError(Exception):
    pass


def call(method: str, **payload) -> dict:
    s = get_settings()
    try:
        r = httpx.post(
            f"{s.slack_api_base.rstrip('/')}/{method}",
            json=payload,
            headers={"Authorization": f"Bearer {s.slack_bot_token}"},
            timeout=10.0,
        )
        body = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SlackError(f"{method}: {exc.__class__.__name__}") from exc
    if not body.get("ok"):
        raise SlackError(f"{method}: {body.get('error', 'failed')}")
    return body


def user_email(slack_user_id: str) -> str | None:
    """The email on a Slack profile (needs the users:read.email scope)."""
    s = get_settings()
    try:
        r = httpx.get(
            f"{s.slack_api_base.rstrip('/')}/users.info",
            params={"user": slack_user_id},
            headers={"Authorization": f"Bearer {s.slack_bot_token}"},
            timeout=10.0,
        )
        body = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not body.get("ok"):
        return None
    return ((body.get("user") or {}).get("profile") or {}).get("email", "").lower() or None


def user_id_for_email(email: str) -> str | None:
    s = get_settings()
    try:
        r = httpx.get(
            f"{s.slack_api_base.rstrip('/')}/users.lookupByEmail",
            params={"email": email},
            headers={"Authorization": f"Bearer {s.slack_bot_token}"},
            timeout=10.0,
        )
        body = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    return (body.get("user") or {}).get("id") if body.get("ok") else None
