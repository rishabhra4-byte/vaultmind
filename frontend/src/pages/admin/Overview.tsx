import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { Skeleton, timeAgo } from "../../components/bits";
import { IconChat, IconCloud, IconFile, IconInbox, IconLock, IconSparkle } from "../../components/icons";
import { useApp } from "../../state";
import type { Classification, Insights } from "../../types";

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

const STATUS: Record<string, string> = { ok: "Synced", error: "Failing", syncing: "Syncing", idle: "Not synced yet" };

interface Operations {
  ai: { max_classification: Classification; daily_limit: number; daily_limit_per_person: number; used_today: number | null };
  scanner: { enabled: boolean; reachable: boolean; required: boolean };
  backups: {
    enabled: boolean;
    interval_hours: number;
    keep: number;
    recent: { name: string; created_at: string; size: number; verified: boolean | null; checks: { name: string; ok: boolean; detail: string }[] }[];
  };
}

/** Whether uploads and synced files are scanned, and whether the scanner answers. */
function ScannerStatus({ ops }: { ops: Operations | null }) {
  if (!ops) return <p className="muted small">Checking…</p>;
  const { scanner } = ops;
  if (!scanner.enabled)
    return (
      <p className="muted small">
        <span className="status status-error">Off</span> Files aren't scanned for malware. Set <code>VAULTMIND_CLAMAV_ADDRESS</code> to
        a ClamAV server (see the operations guide).
      </p>
    );
  return (
    <p className="muted small">
      <span className={`status status-${scanner.reachable ? "ok" : "error"}`}>{scanner.reachable ? "Scanning" : "Not answering"}</span>{" "}
      {scanner.reachable
        ? "Every upload and synced file is checked by ClamAV before it's stored."
        : scanner.required
          ? "Uploads and syncs are paused until ClamAV answers again."
          : "Files are being accepted without a scan until ClamAV answers again."}
    </p>
  );
}

/** Whether backups run, and whether the newest one passed its restore check. */
function BackupStatus({ ops }: { ops: Operations | null }) {
  if (!ops) return <p className="muted small">Checking…</p>;
  const { backups } = ops;
  if (!backups.enabled)
    return (
      <p className="muted small">
        <span className="status status-error">Off</span> Scheduled backups aren't set up. Set <code>VAULTMIND_BACKUP_DIR</code> (see the
        operations guide), or run <code>python -m app.cli backup</code>.
      </p>
    );
  const latest = backups.recent[0];
  if (!latest) return <p className="muted small">The first backup runs within the hour.</p>;
  const failed = latest.checks.filter((c) => !c.ok);
  return (
    <p className="muted small">
      <span className={`status status-${latest.verified ? "ok" : latest.verified === null ? "idle" : "error"}`}>
        {latest.verified ? "Restore check passed" : latest.verified === null ? "Not checked yet" : "Restore check failed"}
      </span>{" "}
      Last backup {timeAgo(latest.created_at)}, {(latest.size / 1024 / 1024).toFixed(1)} MB. Every {backups.interval_hours} h, newest{" "}
      {backups.keep} kept.
      {failed.length > 0 && ` Failed: ${failed.map((c) => c.name.toLowerCase()).join("; ")}.`}
    </p>
  );
}

export default function Overview() {
  const { notify } = useApp();
  const [stats, setStats] = useState<Stats | null>(null);
  const [insights, setInsights] = useState<Insights | null>(null);
  const [days, setDays] = useState(30);
  const [barsReady, setBarsReady] = useState(false);
  const [ops, setOps] = useState<Operations | null>(null);

  useEffect(() => {
    api<Stats>("/api/admin/stats")
      .then((s) => {
        setStats(s);
        requestAnimationFrame(() => setBarsReady(true));
      })
      .catch(() => notify("Couldn't load the dashboard", "error"));
  }, [notify]);

  useEffect(() => {
    api<Operations>("/api/admin/operations")
      .then(setOps)
      .catch(() => {});
  }, []);

  useEffect(() => {
    let current = true; // switching periods quickly: only the last one's figures are shown
    setInsights(null);
    api<Insights>(`/api/admin/insights?days=${days}`)
      .then((i) => current && setInsights(i))
      .catch(() => current && notify("Couldn't load insights", "error"));
    return () => {
      current = false;
    };
  }, [days, notify]);

  const maxClass = stats ? Math.max(1, ...Object.values(stats.by_classification)) : 1;

  return (
    <>
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
            <IconSparkle /> What people ask
          </h2>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Time period for insights">
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
        </div>
        {!insights ? (
          <Skeleton rows={2} />
        ) : (
          <div className="insights">
            <section className="panel">
              <p className="insight-figure">
                <strong>{insights.questions}</strong> questions
                {Object.keys(insights.channels).length > 1 &&
                  ` (${Object.entries(insights.channels)
                    .map(([c, n]) => `${n} in ${c === "web" ? "VaultMind" : c[0].toUpperCase() + c.slice(1)}`)
                    .join(", ")})`}
                , <strong>{insights.unanswered}</strong> without an answer
              </p>
              <h3>Asked most</h3>
              {insights.top_questions.length === 0 ? (
                <p className="muted small">No questions yet.</p>
              ) : (
                <ol className="plain">
                  {insights.top_questions.map((q) => (
                    <li key={q.question}>
                      “{q.question}” <span className="muted small">{q.count === 1 ? "once" : `${q.count} times`}</span>
                    </li>
                  ))}
                </ol>
              )}
              <p className="muted small">Questions are shown without who asked them.</p>
            </section>
            <section className="panel">
              <h3>Asked, but nothing matched</h3>
              {insights.unanswered_questions.length === 0 ? (
                <p className="muted small">Every question found something.</p>
              ) : (
                <>
                  <ul className="plain">
                    {insights.unanswered_questions.map((q) => (
                      <li key={q.question}>
                        “{q.question}” <span className="muted small">{q.count === 1 ? "once" : `${q.count} times`}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="muted small">
                    A missing document, or a word people use that documents don't? Add it to the{" "}
                    <Link to="/admin/glossary">glossary</Link>.
                  </p>
                </>
              )}
            </section>
            <section className="panel">
              <h3>Possibly out of date</h3>
              {insights.stale_documents.length === 0 ? (
                <p className="muted small">Nothing older than six months.</p>
              ) : (
                <ul className="plain">
                  {insights.stale_documents.map((d) => (
                    <li key={d.id}>
                      <Link to={`/library/${d.id}`}>{d.title}</Link>{" "}
                      <span className="muted small">
                        {d.team}, updated {timeAgo(d.updated_at)}
                        {d.views > 0 && `, ${d.views} opens`}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section className="panel">
              <h3>
                <IconCloud width={16} height={16} /> Connected sources
              </h3>
              {insights.sources.length === 0 ? (
                <p className="muted small">
                  None yet. <Link to="/admin/sources">Connect a drive or folder</Link>.
                </p>
              ) : (
                <ul className="plain">
                  {insights.sources.map((s) => (
                    <li key={s.id} className="source-health">
                      <Link to="/admin/sources">{s.name}</Link>{" "}
                      <span className={`status status-${s.overdue && s.status === "ok" ? "late" : s.status}`}>
                        {!s.enabled
                          ? "Paused"
                          : s.overdue && s.status === "ok"
                            ? "Behind schedule"
                            : (STATUS[s.status] ?? s.status)}
                      </span>
                      <span className="muted small">
                        {s.documents} documents
                        {s.last_synced_at && `, synced ${timeAgo(s.last_synced_at)}`}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <h3>AI</h3>
              <p className="muted small">
                {insights.ai.mode === "ai" ? "AI answers are on." : "Offline mode: no AI service is set up."}{" "}
                {insights.ai.documents_ai_off} document{insights.ai.documents_ai_off === 1 ? "" : "s"} and{" "}
                {insights.ai.sources_ai_off} source{insights.ai.sources_ai_off === 1 ? "" : "s"} are kept away from the AI.
                {ops && ops.ai.max_classification !== "restricted" && (
                  <> Nothing above {ops.ai.max_classification} is ever sent to it.</>
                )}
                {ops && ops.ai.daily_limit > 0 && (
                  <>
                    {" "}
                    Today: {ops.ai.used_today ?? 0} of {ops.ai.daily_limit} AI calls
                    {ops.ai.daily_limit_per_person > 0 && `, at most ${ops.ai.daily_limit_per_person} answers per person`}.
                  </>
                )}
              </p>
              <h3>Backups</h3>
              <BackupStatus ops={ops} />
              <h3>Malware scanning</h3>
              <ScannerStatus ops={ops} />
            </section>
          </div>
        )}
      </section>
    </>
  );
}
