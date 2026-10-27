import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { Avatar, ClassTag, Empty, Skeleton, timeAgo, until, usePageTitle } from "../components/bits";
import { IconCheck, IconInbox, IconLock } from "../components/icons";
import { useApp } from "../state";
import type { AccessRequest, Grant } from "../types";

const STATUS: Record<AccessRequest["status"], string> = {
  pending: "Waiting for a decision",
  approved: "Approved",
  denied: "Denied",
  cancelled: "Cancelled",
};

type Act = (path: string, done: string, opts?: { json?: unknown; method?: string }) => Promise<boolean>;

export default function Requests() {
  const { user, notify, refreshInbox } = useApp();
  const [inbox, setInbox] = useState<AccessRequest[]>([]);
  const [mine, setMine] = useState<AccessRequest[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  usePageTitle("Access requests");

  const load = useCallback(async () => {
    try {
      const [a, b, c] = await Promise.all([
        api<AccessRequest[]>("/api/access-requests/inbox"),
        api<AccessRequest[]>("/api/access-requests/mine"),
        api<Grant[]>("/api/grants"),
      ]);
      setInbox(a);
      setMine(b);
      setGrants(c);
      setState("ready");
    } catch {
      setState("failed");
    }
    refreshInbox();
  }, [refreshInbox]);

  useEffect(() => {
    load();
  }, [load]);

  const act: Act = async (path, done, opts = {}) => {
    try {
      await api(path, { method: opts.method ?? "POST", json: opts.json });
      notify(done);
      await load();
      return true;
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Something went wrong. Try again.", "error");
      return false;
    }
  };

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

      {state === "loading" && <Skeleton rows={3} />}
      {state === "failed" && (
        <Empty title="Requests didn't load." icon={<IconInbox />}>
          <button className="link-btn" onClick={load}>
            <u>Try again</u>
          </button>
        </Empty>
      )}

      {state === "ready" && (
        <>
          <section className="block">
            <h2>Waiting for your decision {inbox.length > 0 && <span className="pill pending">{inbox.length}</span>}</h2>
            {inbox.length === 0 ? (
              <Empty title="You're all caught up." icon={<IconCheck />}>
                Requests for documents your team owns, at or below your level, appear here.
              </Empty>
            ) : (
              <ul className="req-list">
                {inbox.map((r) => (
                  <Decide key={r.id} req={r} act={act} />
                ))}
              </ul>
            )}
          </section>

          <section className="block">
            <h2>Your requests</h2>
            {mine.length === 0 ? (
              <Empty title="You haven't asked for anything yet." icon={<IconLock />}>
                When the assistant or the library shows a document you can't open, use Request access.
              </Empty>
            ) : (
              <ul className="req-list">
                {mine.map((r) => (
                  <li key={r.id} className="req">
                    <div className="req-main">
                      <div className="row wrap">
                        <Link to={`/library/${r.document_id}`} className="req-title">
                          {r.document_title}
                        </Link>
                        <ClassTag value={r.classification} />
                        <span className={`pill ${r.status}`}>{STATUS[r.status]}</span>
                      </div>
                      <p className="req-reason">“{r.reason}”</p>
                      <p className="muted small">
                        Asked {timeAgo(r.created_at)} for {r.duration_days} day{r.duration_days > 1 ? "s" : ""}
                        {r.decided_by && `. ${r.status === "approved" ? "Approved" : "Decided"} by ${r.decided_by}`}
                        {r.decision_note && `: “${r.decision_note}”`}
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
                        {g.user_id === user?.id ? "You" : g.user_name}, granted by {g.granted_by ?? "an admin"}.{" "}
                        <span className="pill grant">{until(g.expires_at)}</span>
                      </p>
                    </div>
                    <button
                      className="btn small ghost"
                      onClick={() => act(`/api/grants/${g.id}`, "Access revoked", { method: "DELETE" })}
                    >
                      {g.user_id === user?.id ? "Give up access" : "Revoke"}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function Decide({ req, act }: { req: AccessRequest; act: Act }) {
  const [note, setNote] = useState("");
  const [days, setDays] = useState(req.duration_days);
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);

  async function decide(kind: "approve" | "deny") {
    setBusy(kind);
    const ok = await act(
      `/api/access-requests/${req.id}/${kind}`,
      kind === "approve" ? `Approved for ${days} day${days > 1 ? "s" : ""}` : "Request denied",
      { json: kind === "approve" ? { note, duration_days: days } : { note } },
    );
    if (!ok) setBusy(null);
  }

  return (
    <li className={`req ${busy ? "leaving" : ""}`}>
      <Avatar name={req.requester.full_name} team={req.requester.team_name} size={40} />
      <div className="req-main">
        <div className="row wrap">
          <strong>{req.requester.full_name}</strong>
          <span className="muted small">
            {req.requester.position_label}, {req.requester.team_name}
          </span>
        </div>
        <div className="row wrap">
          <span className="muted">wants</span>
          <Link to={`/library/${req.document_id}`} className="req-title">
            {req.document_title}
          </Link>
          <ClassTag value={req.classification} />
        </div>
        <p className="req-reason">“{req.reason}”</p>
        <div className="decide">
          <input
            placeholder="Note to the requester (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={1000}
            aria-label="Note to the requester"
          />
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Access length">
            {[...new Set([1, 3, 7, 14, 30, req.duration_days])]
              .sort((a, b) => a - b)
              .map((d) => (
                <option key={d} value={d}>
                  {d} day{d > 1 ? "s" : ""}
                </option>
              ))}
          </select>
          <button className="btn small success" onClick={() => decide("approve")} disabled={!!busy}>
            {busy === "approve" ? "Approving…" : "Approve"}
          </button>
          <button className="btn small ghost" onClick={() => decide("deny")} disabled={!!busy}>
            {busy === "deny" ? "Denying…" : "Deny"}
          </button>
        </div>
        <p className="muted small">Asked {timeAgo(req.created_at)}</p>
      </div>
    </li>
  );
}
