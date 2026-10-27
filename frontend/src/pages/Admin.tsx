import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { timeAgo } from "../components/bits";
import { useApp } from "../state";
import type { AuditEvent, Classification, User } from "../types";

interface Stats {
  documents: number;
  users: number;
  queries_7d: number;
  denied_7d: number;
  pending_requests: number;
  by_classification: Record<Classification, number>;
  top_documents: { id: number; title: string; views: number }[];
  flagged_documents: { id: number; title: string; classification: Classification; flags: string[] }[];
}

const ACTIONS: Record<string, string> = {
  "auth.login": "signed in",
  "document.view": "opened",
  "document.view_denied": "was refused",
  "document.download": "downloaded",
  "document.download_denied": "was refused a download of",
  "document.upload": "uploaded",
  "document.update": "changed access on",
  "document.delete": "deleted",
  "assistant.query": "asked the assistant",
  "access.request": "requested access to",
  "access.approve": "approved access to",
  "access.deny": "denied access to",
  "access.cancel": "cancelled a request for",
  "access.revoke": "revoked access to",
  "user.update": "updated user",
};

const FILTERS = [
  ["", "Everything"],
  ["document.view_denied", "Refused access"],
  ["assistant", "Assistant questions"],
  ["access", "Requests and grants"],
  ["document.upload", "Uploads"],
];

export default function Admin() {
  const { meta, notify } = useApp();
  const [stats, setStats] = useState<Stats | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [filter, setFilter] = useState("");
  const [users, setUsers] = useState<User[]>([]);

  useEffect(() => {
    api<Stats>("/api/admin/stats").then(setStats).catch(() => {});
    api<User[]>("/api/admin/users").then(setUsers).catch(() => {});
  }, []);
  useEffect(() => {
    api<AuditEvent[]>(`/api/admin/audit?limit=150${filter ? `&action=${filter}` : ""}`).then(setEvents).catch(() => {});
  }, [filter]);

  async function updateUser(u: User, change: Partial<User>) {
    try {
      const updated = await api<User>(`/api/admin/users/${u.id}`, { method: "PATCH", json: change });
      setUsers((list) => list.map((x) => (x.id === u.id ? updated : x)));
      notify(`Updated ${u.full_name}`);
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't update user", "error");
    }
  }

  const maxClass = stats ? Math.max(1, ...Object.values(stats.by_classification)) : 1;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Admin</h1>
          <p className="muted">Usage, refused access and every action, in one place.</p>
        </div>
      </header>

      {stats && (
        <div className="admin-top">
          <dl className="figures">
            <div>
              <dt>Assistant questions, last 7 days</dt>
              <dd>{stats.queries_7d}</dd>
            </div>
            <div>
              <dt>Refused opens, last 7 days</dt>
              <dd>{stats.denied_7d}</dd>
            </div>
            <div>
              <dt>Requests waiting</dt>
              <dd>{stats.pending_requests}</dd>
            </div>
            <div>
              <dt>Documents / active people</dt>
              <dd>
                {stats.documents} / {stats.users}
              </dd>
            </div>
          </dl>

          <section className="panel">
            <h2>Documents by classification</h2>
            <ul className="bars">
              {(Object.keys(stats.by_classification) as Classification[]).map((c) => (
                <li key={c}>
                  <span className="bar-label">{c[0].toUpperCase() + c.slice(1)}</span>
                  <span className="bar-track">
                    <span className={`bar class-bg-${c}`} style={{ width: `${(stats.by_classification[c] / maxClass) * 100}%` }} />
                  </span>
                  <span className="bar-value">{stats.by_classification[c]}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="panel">
            <h2>Most opened this week</h2>
            <ol className="plain">
              {stats.top_documents.map((d) => (
                <li key={d.id}>
                  <Link to={`/library/${d.id}`}>{d.title}</Link> <span className="muted">{d.views === 1 ? "opened once" : `${d.views} opens`}</span>
                </li>
              ))}
            </ol>
            <h2>Flagged by the scanner</h2>
            <ul className="plain">
              {stats.flagged_documents.map((d) => (
                <li key={d.id}>
                  <Link to={`/library/${d.id}`}>{d.title}</Link>{" "}
                  <span className="muted">{d.flags.map((f) => f.replace(/_/g, " ")).join(", ")}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}

      <section className="block">
        <div className="row between">
          <h2>Activity</h2>
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter activity">
            {FILTERS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <ul className="audit">
          {events.map((e) => (
            <li key={e.id} className={e.action.endsWith("_denied") ? "denied" : ""}>
              <time dateTime={e.created_at}>{timeAgo(e.created_at)}</time>
              <span>
                <strong>{e.actor ?? "System"}</strong> {ACTIONS[e.action] ?? e.action}{" "}
                {e.target_type === "document" && e.target_id && typeof e.detail.title === "string" ? (
                  <Link to={`/library/${e.target_id}`}>{e.detail.title}</Link>
                ) : null}
                {e.action === "assistant.query" && typeof e.detail.question === "string" && (
                  <span className="muted"> “{e.detail.question}”</span>
                )}
                {typeof e.detail.reason === "string" && <span className="muted"> ({e.detail.reason})</span>}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="block">
        <h2>People</h2>
        <div className="table-wrap">
          <table className="people">
            <thead>
              <tr>
                <th>Name</th>
                <th>Team</th>
                <th>Position</th>
                <th>Active</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    {u.full_name}
                    <div className="muted small">{u.title}</div>
                  </td>
                  <td>
                    <select value={u.team_id} onChange={(e) => updateUser(u, { team_id: Number(e.target.value) })} aria-label={`Team for ${u.full_name}`}>
                      {meta?.teams.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select value={u.position} onChange={(e) => updateUser(u, { position: Number(e.target.value) })} aria-label={`Position for ${u.full_name}`}>
                      {meta?.positions.map((p) => (
                        <option key={p.value} value={p.value}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input type="checkbox" checked={u.is_active} onChange={(e) => updateUser(u, { is_active: e.target.checked })} aria-label={`${u.full_name} active`} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
