"""Send copies of notifications by email and Slack, from the background scheduler."""

import logging
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import select

from . import slack
from .config import get_settings
from .database import SessionLocal
from .models import Notification, User, UserIdentity

log = logging.getLogger(__name__)
BATCH = 200


def _send_email(to: str, subject: str, body: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject.replace("\n", " ")[:200]
    msg.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        if s.smtp_starttls:
            smtp.starttls()
        if s.smtp_username:
            smtp.login(s.smtp_username, s.smtp_password)
        smtp.send_message(msg)


def slack_id(db, user: User) -> str | None:
    """The person's Slack id: remembered, or looked up by email and remembered."""
    known = db.scalar(select(UserIdentity.subject).where(UserIdentity.user_id == user.id, UserIdentity.provider == "slack"))
    if known:
        return known
    found = slack.user_id_for_email(user.email)
    if (
        found
        and db.scalar(select(UserIdentity.id).where(UserIdentity.provider == "slack", UserIdentity.subject == found)) is None
    ):
        db.add(UserIdentity(user_id=user.id, provider="slack", subject=found))
        db.flush()
    return found


def deliver_pending(now: datetime | None = None) -> int:
    """Email / Slack copies of notifications from the last day that haven't been sent yet."""
    s = get_settings()
    if not (s.email_configured or s.slack_configured):
        return 0
    now = now or datetime.now(UTC)
    sent = 0
    with SessionLocal() as db:
        pending = db.scalars(
            select(Notification)
            .where(Notification.delivered_at.is_(None), Notification.created_at >= now - timedelta(days=1))
            .order_by(Notification.id)
            .limit(BATCH)
        ).all()
        for n in pending:
            user = db.get(User, n.user_id)
            result: dict[str, str] = {}
            link = s.link(n.link) if n.link else ""
            if user is None or not user.is_active:
                result["skipped"] = "inactive"
            else:
                if s.email_configured and user.notify_email:
                    try:
                        body = "\n\n".join(x for x in (n.body, f"Open in VaultMind: {link}" if link else "") if x)
                        _send_email(user.email, n.title, body + "\n\n— VaultMind (change email notifications in your settings)")
                        result["email"] = "sent"
                    except (OSError, smtplib.SMTPException) as exc:
                        log.warning("Email to user %s failed: %s", user.id, exc)
                        result["email"] = f"failed: {exc.__class__.__name__}"
                if s.slack_configured:
                    try:
                        sid = slack_id(db, user)
                        if sid:
                            text = (
                                f"*{n.title}*"
                                + (f"\n{n.body}" if n.body else "")
                                + (f"\n<{link}|Open in VaultMind>" if link.startswith("http") else "")
                            )
                            slack.call("chat.postMessage", channel=sid, text=text, unfurl_links=False)
                            result["slack"] = "sent"
                        else:
                            result["slack"] = "no Slack account"
                    except slack.SlackError as exc:
                        result["slack"] = f"failed: {exc}"
            n.delivered_at, n.delivery = now, result
            sent += 1
        db.commit()
    return sent
