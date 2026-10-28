import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useApp } from "../state";
import type { AssistantAction } from "../types";
import { IconCheck, IconCopy, IconMail } from "./icons";

const DAYS = [1, 3, 7, 14, 30];

/**
 * Something the assistant offered to do. Nothing happens until the person presses the main
 * button, and the server checks their permissions again at that moment.
 */
export function ActionCard({
  action,
  onChange,
  compact = false,
  autoFocus = false,
}: {
  action: AssistantAction;
  onChange: (a: AssistantAction) => void;
  compact?: boolean;
  /** Move focus into the card: it was opened by the person, not offered with an answer. */
  autoFocus?: boolean;
}) {
  const { notify } = useApp();
  const p = action.payload;
  const [reason, setReason] = useState(p.reason ?? "");
  const [days, setDays] = useState(p.duration_days ?? (action.kind === "share" ? 30 : 7));
  const [title, setTitle] = useState(p.title ?? "");
  const [subject, setSubject] = useState(p.subject ?? "");
  const [body, setBody] = useState(p.body ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const formRef = useRef<HTMLFormElement>(null);

  // A card the person opened may appear below the fold of the conversation: bring it into view.
  useEffect(() => {
    if (autoFocus) formRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [autoFocus]);

  if (action.status === "dismissed") return null;
  if (action.status === "done") {
    return (
      <div className="action-card done" role="status">
        <span className="action-done-icon">
          <IconCheck width={14} height={14} />
        </span>
        <span>{action.result.message ?? "Done."}</span>
        {action.result.link && (
          <Link to={action.result.link} className="action-link">
            Open
          </Link>
        )}
      </div>
    );
  }

  async function run(path: "confirm" | "dismiss", edits: Record<string, unknown> = {}) {
    setBusy(true);
    setError("");
    try {
      const updated = await api<AssistantAction>(`/api/assistant/actions/${action.id}/${path}`, {
        method: "POST",
        json: path === "confirm" ? edits : undefined,
      });
      onChange(updated);
      if (path === "confirm" && updated.result.message) notify(updated.result.message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const dismiss = (
    <button type="button" className="btn small ghost" onClick={() => run("dismiss")} disabled={busy}>
      Not now
    </button>
  );

  if (action.kind === "draft_message") {
    const mailto = p.email
      ? `mailto:${encodeURIComponent(p.email)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
      : null;
    async function copy() {
      try {
        await navigator.clipboard.writeText(subject ? `Subject: ${subject}\n\n${body}` : body);
        notify("Draft copied");
      } catch {
        notify("Couldn't copy. Select the text instead.", "error");
      }
    }
    return (
      <div className="action-card">
        <div className="action-head">
          <strong>{action.label}</strong>
          {p.email && <span className="muted small">{p.email}</span>}
        </div>
        <label className="field">
          <span>Subject</span>
          <input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={120} />
        </label>
        <label className="field">
          <span>Message</span>
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={compact ? 6 : 8} maxLength={8000} />
        </label>
        <p className="muted small">VaultMind never sends messages for you. Copy this, or open it in your email app.</p>
        {error && <p className="error">{error}</p>}
        <div className="row wrap">
          <button type="button" className="btn small" onClick={copy}>
            <IconCopy width={14} height={14} /> Copy
          </button>
          {mailto && (
            <a className="btn small" href={mailto}>
              <IconMail width={14} height={14} /> Open in email
            </a>
          )}
          <button type="button" className="btn small primary" onClick={() => run("confirm", { body })} disabled={busy}>
            Done
          </button>
        </div>
      </div>
    );
  }

  if (action.kind === "save_summary") {
    return (
      <form
        ref={formRef}
        className="action-card"
        onSubmit={(e) => {
          e.preventDefault();
          run("confirm", { title: title.trim() });
        }}
      >
        <div className="action-head">
          <strong>Save this answer as a document</strong>
        </div>
        <label className="field">
          <span>Title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} autoFocus={autoFocus} />
        </label>
        <p className="muted small">Only you (and admins) can open it until you share it from its page.</p>
        {error && <p className="error">{error}</p>}
        <div className="row wrap">
          <button className="btn small primary" disabled={busy || !title.trim()}>
            {busy ? "Saving…" : "Save"}
          </button>
          {dismiss}
        </div>
      </form>
    );
  }

  if (action.kind === "share") {
    const person = p.target?.kind === "user";
    return (
      <form
        className="action-card"
        onSubmit={(e) => {
          e.preventDefault();
          run("confirm", person ? { duration_days: days } : {});
        }}
      >
        <div className="action-head">
          <strong>{action.label}</strong>
        </div>
        {person && (
          <label className="field inline-field">
            <span>For</span>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {DAYS.map((d) => (
                <option key={d} value={d}>
                  {d} day{d > 1 ? "s" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        {error && <p className="error">{error}</p>}
        <div className="row wrap">
          <button className="btn small primary" disabled={busy}>
            {busy ? "Sharing…" : person ? "Give access" : "Share"}
          </button>
          {dismiss}
        </div>
      </form>
    );
  }

  // request_access
  return (
    <form
      ref={formRef}
      className="action-card"
      onSubmit={(e) => {
        e.preventDefault();
        run("confirm", { reason: reason.trim(), duration_days: days });
      }}
    >
      <label className="field">
        <span>Why do you need “{p.title}”?</span>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
          minLength={10}
          maxLength={1000}
          required
          autoFocus={autoFocus}
        />
      </label>
      <label className="field inline-field">
        <span>For</span>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {DAYS.map((d) => (
            <option key={d} value={d}>
              {d} day{d > 1 ? "s" : ""}
            </option>
          ))}
        </select>
      </label>
      {error && <p className="error">{error}</p>}
      <div className="row wrap">
        <button className="btn small primary" disabled={busy || reason.trim().length < 10}>
          {busy ? "Sending…" : "Send request"}
        </button>
        {dismiss}
      </div>
    </form>
  );
}
