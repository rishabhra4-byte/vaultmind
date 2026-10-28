"""Slack endpoints: the /vaultmind slash command and the Events API (mentions, direct messages).

Set the Slack app's Request URLs to https://<your VaultMind>/api/slack/commands and
https://<your VaultMind>/api/slack/events. See docs/integrations.md#slack.
"""

import json
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, status

from .. import slack_bot
from ..config import get_settings

router = APIRouter(prefix="/api/slack", tags=["slack"], include_in_schema=False)


async def _verified_body(request: Request) -> bytes:
    s = get_settings()
    if not s.slack_configured:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Slack isn't set up")
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not slack_bot.verify(s.slack_signing_secret, timestamp, body, signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad signature")
    return body


@router.post("/commands")
async def command(request: Request):
    body = await _verified_body(request)
    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
    text = form.get("text", "").strip()
    if not text or text.lower() in ("help", "?"):
        return {"response_type": "ephemeral", "text": slack_bot.HELP}
    response_url = form.get("response_url", "")
    if not slack_bot.allowed_response_url(response_url):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unexpected response URL")
    user_id = form.get("user_id", "")
    slack_bot.run_in_background(lambda: slack_bot.answer_command(user_id, text, response_url))
    return {"response_type": "ephemeral", "text": "Searching the documents you can open…"}


@router.post("/events")
async def events(request: Request):
    body = await _verified_body(request)
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not JSON") from None
    if payload.get("type") == "url_verification":
        return {"challenge": str(payload.get("challenge", ""))}
    # Slack resends an event when it didn't get a reply within 3 seconds; the first one is handled.
    if request.headers.get("X-Slack-Retry-Num") or payload.get("type") != "event_callback":
        return {"ok": True}
    if not slack_bot.first_time(str(payload.get("event_id", ""))):
        return {"ok": True}
    event = payload.get("event") or {}
    if event.get("bot_id") or event.get("subtype"):
        return {"ok": True}  # never answer bots (including ourselves), edits or joins
    is_mention = event.get("type") == "app_mention"
    is_dm = event.get("type") == "message" and event.get("channel_type") == "im"
    if is_mention or is_dm:
        slack_bot.run_in_background(lambda: slack_bot.answer_event(event))
    return {"ok": True}
