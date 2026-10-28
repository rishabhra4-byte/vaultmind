import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../state";
import type { Notification, Preferences } from "../types";
import { timeAgo } from "./bits";
import { IconBell } from "./icons";

interface NotificationList {
  unread: number;
  items: Notification[];
}

const POLL_MS = 60_000;

/** The bell in the sidebar: changes to documents you read, decisions on your requests, expiring access. */
export function NotificationBell() {
  const { notify } = useApp();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<NotificationList | null>(null);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const headingId = useId();

  const load = useCallback(() => {
    api<NotificationList>("/api/notifications")
      .then(setData)
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(() => document.visibilityState === "visible" && load(), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!open) return;
    load();
    api<Preferences>("/api/me/preferences")
      .then(setPrefs)
      .catch(() => {});
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    const onClick = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!panelRef.current?.contains(t) && !buttonRef.current?.contains(t)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, [open, load]);

  async function markRead(ids?: number[]) {
    setData(await api<NotificationList>("/api/notifications/read", { method: "POST", json: { ids: ids ?? null } }));
  }

  async function openItem(n: Notification) {
    setOpen(false);
    if (!n.read_at) markRead([n.id]).catch(() => {});
    if (n.link.startsWith("/")) navigate(n.link);
  }

  async function toggleEmail(on: boolean) {
    try {
      setPrefs(await api<Preferences>("/api/me/preferences", { method: "PATCH", json: { notify_email: on } }));
      notify(on ? "You'll get email copies" : "No more email copies");
    } catch {
      notify("Couldn't save that", "error");
    }
  }

  const unread = data?.unread ?? 0;
  return (
    <>
      <button
        ref={buttonRef}
        className={`nav-btn ${open ? "active" : ""}`}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <IconBell />
        <span className="nav-label">Notifications</span>
        {unread > 0 && (
          <span className="count" aria-label={`${unread} unread`}>
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div ref={panelRef} className="notif-panel" role="dialog" aria-labelledby={headingId} tabIndex={-1}>
          <div className="notif-head">
            <h2 id={headingId}>Notifications</h2>
            {unread > 0 && (
              <button className="link-btn small" onClick={() => markRead()}>
                Mark all as read
              </button>
            )}
          </div>
          {data === null ? (
            <p className="muted small notif-empty">Loading…</p>
          ) : data.items.length === 0 ? (
            <p className="muted small notif-empty">
              Nothing yet. You'll hear here when documents you read change, and when your requests are decided.
            </p>
          ) : (
            <ul className="notif-list">
              {data.items.map((n) => (
                <li key={n.id}>
                  <button className={`notif ${n.read_at ? "" : "unread"}`} onClick={() => openItem(n)}>
                    <span className="notif-title">
                      {!n.read_at && <span className="sr-only">Unread: </span>}
                      {n.title}
                    </span>
                    {n.body && <span className="notif-body">{n.body}</span>}
                    <time dateTime={n.created_at}>{timeAgo(n.created_at)}</time>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {prefs && (prefs.email_available || prefs.slack_available) && (
            <div className="notif-foot">
              {prefs.email_available && (
                <label className="check">
                  <span className="switch">
                    <input type="checkbox" checked={prefs.notify_email} onChange={(e) => toggleEmail(e.target.checked)} />
                    <span />
                  </span>
                  Email me a copy
                </label>
              )}
              {prefs.slack_available && (
                <p className="muted small">
                  {prefs.slack_linked
                    ? "Copies also go to you in Slack."
                    : "Copies go to Slack once your Slack email matches your work email."}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </>
  );
}
