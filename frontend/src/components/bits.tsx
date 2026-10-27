import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useApp } from "../state";
import type { Classification } from "../types";

const CLASS_LABEL: Record<Classification, string> = {
  public: "Public",
  internal: "Internal",
  confidential: "Confidential",
  restricted: "Restricted",
};

export function ClassTag({ value }: { value: Classification }) {
  return <span className={`class-tag class-${value}`}>{CLASS_LABEL[value]}</span>;
}

/** Six-step seniority ladder, filled up to the user's position. */
export function Ladder({ position, label }: { position: number; label: string }) {
  return (
    <div className="ladder" aria-label={`Position: ${label}, level ${position} of 6`}>
      <div className="ladder-steps" aria-hidden>
        {[1, 2, 3, 4, 5, 6].map((n) => (
          <span key={n} className={n <= position ? "on" : ""} style={{ height: 6 + n * 3 }} />
        ))}
      </div>
      <span className="ladder-label">{label}</span>
    </div>
  );
}

export function timeAgo(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  const d = Math.floor(s / 86400);
  if (d < 30) return `${d} day${d > 1 ? "s" : ""} ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function until(iso: string | null): string {
  if (!iso) return "No expiry";
  const days = Math.ceil((new Date(iso).getTime() - Date.now()) / 86400000);
  return days <= 1 ? "Expires within a day" : `Expires in ${days} days`;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <div className="empty-body">{children}</div>}
    </div>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);
  return (
    <dialog ref={ref} className="modal" onClose={onClose} onCancel={onClose} aria-label={title}>
      <div className="modal-head">
        <h2>{title}</h2>
        <button className="icon-btn" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
      {children}
    </dialog>
  );
}

/** Ask the document's owning team for time-boxed access. */
export function RequestAccessDialog({
  doc,
  onClose,
  onDone,
}: {
  doc: { id: number; title: string; team: string };
  onClose: () => void;
  onDone?: () => void;
}) {
  const { notify } = useApp();
  const [reason, setReason] = useState("");
  const [days, setDays] = useState(7);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/api/access-requests", { method: "POST", json: { document_id: doc.id, reason, duration_days: days } });
      notify(`Request sent to ${doc.team}`);
      onDone?.();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't send the request");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Request access" onClose={onClose}>
      <form onSubmit={submit} className="stack">
        <p className="muted">
          <strong>{doc.title}</strong> is owned by {doc.team}. A manager there will review your request.
        </p>
        <label className="field">
          <span>Why do you need it?</span>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            minLength={10}
            required
            rows={3}
            placeholder="e.g. I'm joining the interview panel and need the role breakdown"
          />
        </label>
        <label className="field">
          <span>For how long?</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[1, 3, 7, 14, 30].map((d) => (
              <option key={d} value={d}>
                {d} day{d > 1 ? "s" : ""}
              </option>
            ))}
          </select>
        </label>
        {error && <p className="error">{error}</p>}
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy || reason.trim().length < 10}>
            {busy ? "Sending…" : "Send request"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function DocLink({ id, children }: { id: number; children: ReactNode }) {
  return <Link to={`/library/${id}`}>{children}</Link>;
}
