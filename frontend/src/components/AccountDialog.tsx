import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { useApp } from "../state";
import type { SignedInSession } from "../types";
import { Modal, Skeleton, timeAgo } from "./bits";

const METHOD: Record<string, string> = { password: "with a password", google: "with Google", microsoft: "with Microsoft" };
const MIN_PASSWORD = 12;

/** Your account: where you're signed in (and signing those browsers out), and your password. */
export function AccountDialog({ onClose }: { onClose: () => void }) {
  const { user, notify } = useApp();
  const [sessions, setSessions] = useState<SignedInSession[] | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(() => {
    setFailed(false);
    api<SignedInSession[]>("/api/me/sessions")
      .then(setSessions)
      .catch(() => setFailed(true));
  }, []);
  useEffect(load, [load]);

  async function end(s: SignedInSession) {
    try {
      await api(`/api/me/sessions/${encodeURIComponent(s.id)}`, { method: "DELETE" });
      notify(`Signed out ${s.device}`);
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't sign that browser out", "error");
    }
    load();
  }

  async function endOthers() {
    try {
      const r = await api<{ ended: number }>("/api/me/sessions/end-others", { method: "POST" });
      notify(r.ended === 1 ? "Signed out 1 other session" : `Signed out ${r.ended} other sessions`);
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't sign the other sessions out", "error");
    }
    load();
  }

  if (!user) return null;
  const others = sessions?.filter((s) => !s.current).length ?? 0;
  return (
    <Modal title="Your account" onClose={onClose}>
      <div className="stack account">
        <p className="muted small">
          {user.full_name}, {user.email}
        </p>
        <section className="stack-sm" aria-labelledby="account-sessions">
          <h3 id="account-sessions">Where you're signed in</h3>
          {sessions === null && !failed && <Skeleton rows={2} />}
          {failed && (
            <p className="error" role="alert">
              Couldn't load your sessions.{" "}
              <button className="link-btn" onClick={load}>
                <u>Try again</u>
              </button>
            </p>
          )}
          {sessions && (
            <ul className="session-list">
              {sessions.map((s) => (
                <li key={s.id} className="session-row">
                  <div>
                    <strong>{s.device}</strong> {s.current && <span className="pill">This browser</span>}
                    <div className="muted small">
                      Signed in {timeAgo(s.created_at)} {METHOD[s.method] ?? ""}, last active {timeAgo(s.last_seen_at)}
                      {s.ip ? `, from ${s.ip}` : ""}
                    </div>
                  </div>
                  {!s.current && (
                    <button className="btn small" onClick={() => end(s)} aria-label={`Sign out ${s.device}, signed in ${timeAgo(s.created_at)}`}>
                      Sign out
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {others > 0 && (
            <div>
              <button className="btn" onClick={endOthers}>
                Sign out of all other sessions
              </button>
            </div>
          )}
          <p className="muted small">If a device you don't recognise is listed, sign it out and change your password.</p>
        </section>
        <section className="stack-sm" aria-labelledby="account-password">
          <h3 id="account-password">Password</h3>
          {user.has_password ? (
            <PasswordForm onChanged={load} />
          ) : (
            <p className="muted">You sign in with your company account, so there's no VaultMind password to change.</p>
          )}
        </section>
      </div>
    </Modal>
  );
}

function PasswordForm({ onChanged }: { onChanged: () => void }) {
  const { notify } = useApp();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (next.length < MIN_PASSWORD) return setError(`Use at least ${MIN_PASSWORD} characters.`);
    if (next !== again) return setError("The new passwords don't match.");
    setBusy(true);
    try {
      const r = await api<{ ended: number }>("/api/me/password", {
        method: "POST",
        json: { current_password: current, new_password: next },
      });
      setCurrent("");
      setNext("");
      setAgain("");
      notify(r.ended ? `Password changed. ${r.ended} other session${r.ended > 1 ? "s were" : " was"} signed out.` : "Password changed");
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't change your password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack-sm" onSubmit={submit}>
      <label className="field">
        <span>Current password</span>
        <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
      </label>
      <label className="field">
        <span>New password</span>
        <input
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          required
          aria-describedby="new-password-help"
        />
        <small id="new-password-help" className="muted">
          At least {MIN_PASSWORD} characters. Changing it signs out your other sessions.
        </small>
      </label>
      <label className="field">
        <span>New password again</span>
        <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
      </label>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div>
        <button className="btn primary" disabled={busy}>
          {busy ? "Changing…" : "Change password"}
        </button>
      </div>
    </form>
  );
}
