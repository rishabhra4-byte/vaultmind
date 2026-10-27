import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { Avatar, Empty, Skeleton, timeAgo, usePageTitle } from "../components/bits";
import { IconActivity, IconChat, IconFile, IconInbox, IconLock } from "../components/icons";
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
  "user.update": "updated a user",
};

const FILTERS = [
  ["", "Everything"],
  ["document.view_denied", "Refused access"],
  ["assistant", "Assistant questions"],
  ["access", "Requests and grants"],
  ["document.upload", "Uploads"],
];

/** Animate a number from 0 to its value. */
function CountUp({ value }: { value: number }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return setN(value);
    let raf = 0;
    const start = performance.now();
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / 700);
      setN(Math.round(value * (1 - Math.pow(1 - p, 3))));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return <>{n}</>;
}

function Figure({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="figure">
      <dt>
        <span className="fig-icon">{icon}</span>
        {label}
      </dt>
      <dd>{children}</dd>
    </div>
  );
}

export default function Admin() {
  const { meta, notify, user: me } = useApp();
  const [stats, setStats] = useState<Stats | null>(null);
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [filter, setFilter] = useState("");
  const [users, setUsers] = useState<User[]>([]);
  const [barsReady, setBarsReady] = useState(false);
  usePageTitle("Admin");

  useEffect(() => {
    api<Stats>("/api/admin/stats")
      .then((s) => {
        setStats(s);
        requestAnimationFrame(() => setBarsReady(true));
      })
      .catch(() => notify("Couldn't load the dashboard", "error"));
    api<User[]>("/api/admin/users").then(setUsers).catch(() => {});
  }, [notify]);

  useEffect(() => {
    setEvents(null);
    api<AuditEvent[]>(`/api/admin/audit?limit=150${filter ? `&action=${encodeURIComponent(filter)}` : ""}`)
      .then(setEvents)
      .catch(() => setEvents([]));
  }, [filter]);

  const byId = useMemo(() => new Map(users.map((u) => [u.id, u])), [users]);

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

      {!stats ? (
        <Skeleton rows={2} />
      ) : (
        <div className="admin-top">
          <dl className="figures">
            <Figure icon={<IconChat />} label="Assistant questions, 7 days">
              <CountUp value={stats.queries_7d} />
            </Figure>
            <Figure icon={<IconLock />} label="Refused opens, 7 days">
              <CountUp value={stats.denied_7d} />
            </Figure>
            <Figure icon={<IconInbox />} label="Requests waiting">
              <CountUp value={stats.pending_requests} />
            </Figure>
            <Figure icon={<IconFile />} label="Documents, active people">
              <CountUp value={stats.documents} /> / <CountUp value={stats.users} />
            </Figure>
          </dl>

          <section className="panel">
            <h2>Documents by classification</h2>
            <ul className="bars">
              {(Object.keys(stats.by_classification) as Classification[]).map((c) => (
                <li key={c}>
                  <span className="bar-label">{c[0].toUpperCase() + c.slice(1)}</span>
                  <span className="bar-track">
                    <span
                      className={`bar class-bg-${c}`}
                      style={{ width: barsReady ? `${(stats.by_classification[c] / maxClass) * 100}%` : 0 }}
                    />
                  </span>
                  <span className="bar-value">{stats.by_classification[c]}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="panel">
            <h2>Most opened this week</h2>
            {stats.top_documents.length === 0 ? (
              <p className="muted">No documents opened this week.</p>
            ) : (
              <ol className="plain">
                {stats.top_documents.map((d) => (
                  <li key={d.id}>
                    <Link to={`/library/${d.id}`}>{d.title}</Link>{" "}
                    <span className="muted">{d.views === 1 ? "opened once" : `${d.views} opens`}</span>
                  </li>
                ))}
              </ol>
            )}
            <h2>Flagged by the scanner</h2>
            <ul className="plain">
              {stats.flagged_documents.map((d) => (
                <li key={d.id} className="flag-row">
                  <Link to={`/library/${d.id}`}>{d.title}</Link>
                  <span className="muted small">{d.flags.map((f) => f.replace(/_/g, " ")).join(", ")}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}

      <section className="block">
        <div className="row between wrap">
          <h2>
            <IconActivity /> Activity
          </h2>
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter activity">
            {FILTERS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
        {events === null ? (
          <Skeleton rows={4} />
        ) : events.length === 0 ? (
          <Empty title="Nothing here yet." icon={<IconActivity />}>
            Activity matching this filter will appear as people use VaultMind.
          </Empty>
        ) : (
          <ul className="audit">
            {events.map((e) => {
              const actor = e.actor_id ? byId.get(e.actor_id) : undefined;
              return (
                <li key={e.id} className={e.action.endsWith("_denied") ? "denied" : ""}>
                  <Avatar name={e.actor ?? "System"} team={actor?.team_name ?? "System"} size={30} />
                  <span>
                    <strong>{e.actor ?? "System"}</strong> {ACTIONS[e.action] ?? e.action}{" "}
                    {e.target_type === "document" && e.target_id && typeof e.detail.title === "string" ? (
                      <Link to={`/library/${e.target_id}`}>{e.detail.title}</Link>
                    ) : null}
                    {e.action === "assistant.query" && typeof e.detail.question === "string" && (
                      <span className="muted"> “{e.detail.question}”</span>
                    )}
                    {typeof e.detail.reason === "string" && <span className="muted"> ({e.detail.reason} rule)</span>}
                  </span>
                  <time dateTime={e.created_at} title={new Date(e.created_at).toLocaleString()}>
                    {timeAgo(e.created_at)}
                  </time>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="block">
        <h2>People</h2>
        <p className="muted small" style={{ marginBottom: 12 }}>
          Changing someone's team or position changes what they can open immediately.
        </p>
        <div className="table-wrap">
          <table className="people">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Team</th>
                <th scope="col">Position</th>
                <th scope="col">Active</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <span className="person-cell">
                      <Avatar name={u.full_name} team={u.team_name} size={32} />
                      <span>
                        <strong>{u.full_name}</strong>
                        <span className="muted small" style={{ display: "block" }}>
                          {u.title}
                        </span>
                      </span>
                    </span>
                  </td>
                  <td>
                    <select
                      value={u.team_id}
                      onChange={(e) => updateUser(u, { team_id: Number(e.target.value) })}
                      aria-label={`Team for ${u.full_name}`}
                    >
                      {meta?.teams.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select
                      value={u.position}
                      onChange={(e) => updateUser(u, { position: Number(e.target.value) })}
                      aria-label={`Position for ${u.full_name}`}
                    >
                      {meta?.positions.map((p) => (
                        <option key={p.value} value={p.value}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <label className="switch" title={u.id === me?.id ? "You can't deactivate yourself" : undefined}>
                      <input
                        type="checkbox"
                        checked={u.is_active}
                        disabled={u.id === me?.id}
                        onChange={(e) => updateUser(u, { is_active: e.target.checked })}
                        aria-label={`${u.full_name} is active`}
                      />
                      <span />
                    </label>
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
