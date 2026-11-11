import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { usePageTitle } from "../components/bits";
import { IconCheck, IconKey } from "../components/icons";
import { useApp } from "../state";

interface Request {
  user_code: string;
  client: string;
  ip: string;
  requested_at: string;
  expires_at: string;
}

const time = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

/** Approve an editor (the VS Code extension) signing in as you, by the code it shows. */
export default function Connect() {
  usePageTitle("Sign in an editor");
  const { user } = useApp();
  const [params, setParams] = useSearchParams();
  const code = params.get("code") ?? "";
  const [typed, setTyped] = useState(code);
  const [request, setRequest] = useState<Request | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState<"approved" | "denied" | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setRequest(null);
    setError("");
    if (!code) return;
    api<Request>(`/api/auth/device/${encodeURIComponent(code)}`)
      .then(setRequest)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Couldn't look that code up."));
  }, [code]);

  async function decide(allow: boolean) {
    if (!request) return;
    setBusy(true);
    try {
      await api(`/api/auth/device/${encodeURIComponent(request.user_code)}/decide`, { method: "POST", json: { allow } });
      setDone(allow ? "approved" : "denied");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save that. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page connect-page">
      <section className="panel connect-card" aria-labelledby="connect-title">
        <span className="connect-icon">{done === "approved" ? <IconCheck /> : <IconKey />}</span>
        {done === "approved" ? (
          <>
            <h1 id="connect-title">VS Code is signed in</h1>
            <p className="muted">
              Go back to VS Code: it signs in as {user?.full_name} within a few seconds. You can sign it out any time from the key
              icon in the sidebar.
            </p>
          </>
        ) : done === "denied" ? (
          <>
            <h1 id="connect-title">Not signed in</h1>
            <p className="muted">You refused that request, so nothing was signed in. If it wasn't you, nothing else needs doing.</p>
          </>
        ) : (
          <>
            <h1 id="connect-title">Sign in an editor</h1>
            {!code ? (
              <form
                className="stack"
                onSubmit={(e) => {
                  e.preventDefault();
                  setParams({ code: typed.trim() });
                }}
              >
                <label className="field">
                  <span>The code your editor shows</span>
                  <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="ABCD-EFGH" autoComplete="off" required />
                </label>
                <button className="btn primary">Continue</button>
              </form>
            ) : error ? (
              <>
                <p className="error" role="alert">
                  {error}
                </p>
                <button className="btn" onClick={() => setParams({})}>
                  Enter a different code
                </button>
              </>
            ) : !request ? (
              <p className="muted">Looking that code up…</p>
            ) : (
              <>
                <p>
                  <strong>{request.client}</strong> is asking to sign in to VaultMind as <strong>{user?.full_name}</strong>. It will see
                  the documents you can see and ask questions as you.
                </p>
                <p className="connect-code" aria-label={`Code ${request.user_code.split("").join(" ")}`}>
                  {request.user_code}
                </p>
                <p className="muted small">
                  Check this matches the code in VS Code. Requested at {time(request.requested_at)} from {request.ip || "an unknown address"}
                  . Only approve it if you started signing in just now; never for a code someone sent you.
                </p>
                <div className="row wrap">
                  <button className="btn primary" disabled={busy} onClick={() => decide(true)}>
                    Sign VS Code in
                  </button>
                  <button className="btn ghost" disabled={busy} onClick={() => decide(false)}>
                    It wasn't me
                  </button>
                </div>
              </>
            )}
          </>
        )}
        <p className="muted small">
          <Link to="/">Back to the assistant</Link>
        </p>
      </section>
    </div>
  );
}
