import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { ClassTag, Empty, timeAgo, until } from "../components/bits";
import { useApp } from "../state";
import type { AccessRequest, Grant } from "../types";

const STATUS: Record<AccessRequest["status"], string> = {
  pending: "Waiting for a decision",
  approved: "Approved",
  denied: "Denied",
  cancelled: "Cancelled",
};

export default function Requests() {
  const { user, notify, refreshInbox } = useApp();
  const [inbox, setInbox] = useState<AccessRequest[]>([]);
  const [mine, setMine] = useState<AccessRequest[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    const [a, b, c] = await Promise.all([
      api<AccessRequest[]>("/api/access-requests/inbox"),
      api<AccessRequest[]>("/api/access-requests/mine"),
      api<Grant[]>("/api/grants"),
    ]);
    setInbox(a);
    setMine(b);
    setGrants(c);
    setLoaded(true);
    refreshInbox();
  }, [refreshInbox]);

  useEffect(() => {
    load().catch(() => setLoaded(true));
  }, [load]);

  async function act(path: string, done: string, json?: unknown) {
    try {
      await api(path, { method: path.startsWith("/api/grants") ? "DELETE" : "POST", json });
      notify(done);
      load();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Something went wrong", "error");
    }
  }

  if (!loaded) return <div className="page muted">Loading…</div>;
  const myGrants = grants.filter((g) => g.user_id === user?.id);
  const issued = grants.filter((g) => g.user_id !== user?.id);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Access requests</h1>
          <p className="muted">Ask for documents you can't open, and decide on requests for documents you're responsible for.</p>
        </div>
      </header>

      <section className="block">
        <h2>Waiting for your decision</h2>
        {inbox.length === 0 ? (
          <Empty title="Nothing to review.">Requests for documents your team owns at your level show up here.</Empty>
        ) : (
          <ul className="req-list">
            {inbox.map((r) => (
              <Decide key={r.id} req={r} onAct={act} />
            ))}
          </ul>
        )}
      </section>

      <section className="block">
        <h2>Your requests</h2>
        {mine.length === 0 ? (
          <Empty title="You haven't asked for anything yet.">
            When the assistant or the library shows a document you can't open, use Request access.
          </Empty>
        ) : (
          <ul className="req-list">
            {mine.map((r) => (
              <li key={r.id} className={`req class-edge-${r.classification}`}>
                <div className="req-main">
                  <Link to={`/library/${r.document_id}`} className="req-title">
                    {r.document_title}
                  </Link>{" "}
                  <ClassTag value={r.classification} />
                  <p className="req-reason">“{r.reason}”</p>
                  <p className="muted small">
                    {STATUS[r.status]}
                    {r.decided_by && ` by ${r.decided_by}`}
                    {r.decision_note && `: “${r.decision_note}”`}. Asked {timeAgo(r.created_at)} for {r.duration_days} days.
                  </p>
                </div>
                {r.status === "pending" && (
                  <button className="btn small ghost" onClick={() => act(`/api/access-requests/${r.id}/cancel`, "Request cancelled")}>
                    Cancel request
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {(myGrants.length > 0 || issued.length > 0) && (
        <section className="block">
          <h2>Temporary access</h2>
          <ul className="req-list">
            {[...myGrants, ...issued].map((g) => (
              <li key={g.id} className="req">
                <div className="req-main">
                  <Link to={`/library/${g.document_id}`} className="req-title">
                    {g.document_title}
                  </Link>
                  <p className="muted small">
                    {g.user_id === user?.id ? "You" : g.user_name}, granted by {g.granted_by ?? "an admin"}. {until(g.expires_at)}.
                  </p>
                </div>
                <button className="btn small ghost" onClick={() => act(`/api/grants/${g.id}`, "Access revoked")}>
                  {g.user_id === user?.id ? "Give up access" : "Revoke"}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Decide({ req, onAct }: { req: AccessRequest; onAct: (path: string, done: string, json?: unknown) => void }) {
  const [note, setNote] = useState("");
  const [days, setDays] = useState(req.duration_days);
  return (
    <li className={`req class-edge-${req.classification}`}>
      <div className="req-main">
        <div>
          <strong>{req.requester.full_name}</strong>{" "}
          <span className="muted">
            ({req.requester.position_label}, {req.requester.team_name})
          </span>{" "}
          wants{" "}
          <Link to={`/library/${req.document_id}`} className="req-title">
            {req.document_title}
          </Link>{" "}
          <ClassTag value={req.classification} />
        </div>
        <p className="req-reason">“{req.reason}”</p>
        <div className="decide">
          <input placeholder="Note to the requester (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Access length">
            {[...new Set([1, 3, 7, 14, 30, req.duration_days])]
              .sort((a, b) => a - b)
              .map((d) => (
                <option key={d} value={d}>
                  {d} day{d > 1 ? "s" : ""}
                </option>
              ))}
          </select>
          <button
            className="btn small primary"
            onClick={() => onAct(`/api/access-requests/${req.id}/approve`, `Approved for ${days} days`, { note, duration_days: days })}
          >
            Approve
          </button>
          <button className="btn small ghost" onClick={() => onAct(`/api/access-requests/${req.id}/deny`, "Request denied", { note })}>
            Deny
          </button>
        </div>
        <p className="muted small">Asked {timeAgo(req.created_at)}</p>
      </div>
    </li>
  );
}
