import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { ClassTag, Empty, Modal, RequestAccessDialog, timeAgo } from "../components/bits";
import { useApp } from "../state";
import type { Classification, Doc, ScanFinding } from "../types";

export default function Library() {
  const { meta } = useApp();
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [q, setQ] = useState("");
  const [team, setTeam] = useState("");
  const [cls, setCls] = useState("");
  const [showLocked, setShowLocked] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [requesting, setRequesting] = useState<Doc | null>(null);

  const load = useCallback(() => {
    const p = new URLSearchParams({ include_locked: String(showLocked) });
    if (q.trim()) p.set("q", q.trim());
    if (team) p.set("team_id", team);
    if (cls) p.set("classification", cls);
    api<Doc[]>(`/api/documents?${p}`).then(setDocs).catch(() => setDocs([]));
  }, [q, team, cls, showLocked]);

  useEffect(() => {
    const t = setTimeout(load, q ? 250 : 0);
    return () => clearTimeout(t);
  }, [load, q]);

  const openable = docs?.filter((d) => d.access.allowed).length ?? 0;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Library</h1>
          <p className="muted">
            {docs ? `${openable} you can open${showLocked ? `, ${docs.length - openable} you can request` : ""}` : "Loading…"}
          </p>
        </div>
        <button className="btn primary" onClick={() => setUploading(true)}>
          Upload a document
        </button>
      </header>

      <div className="filters">
        <input
          type="search"
          placeholder="Search titles and contents"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search documents"
        />
        <select value={team} onChange={(e) => setTeam(e.target.value)} aria-label="Team">
          <option value="">All teams</option>
          {meta?.teams.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <select value={cls} onChange={(e) => setCls(e.target.value)} aria-label="Classification">
          <option value="">Any classification</option>
          {meta?.classifications.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
        <label className="check">
          <input type="checkbox" checked={showLocked} onChange={(e) => setShowLocked(e.target.checked)} />
          Show documents I can request
        </label>
      </div>

      {docs && docs.length === 0 && (
        <Empty title={q ? `Nothing matches “${q}”` : "No documents here yet"}>
          {q ? "Try fewer words, or clear the filters." : "Upload the first one for your team."}
        </Empty>
      )}

      <ul className="doc-list">
        {docs?.map((d) => (
          <li key={d.id} className={`doc-row class-edge-${d.classification} ${d.access.allowed ? "" : "locked"}`}>
            <div className="doc-main">
              <Link to={`/library/${d.id}`} className="doc-title">
                {d.title}
              </Link>
              <div className="doc-meta">
                <ClassTag value={d.classification} />
                <span>{d.owner_team}</span>
                {d.min_position > 1 && <span>{d.min_position_label}+</span>}
                <span>Updated {timeAgo(d.updated_at)}</span>
              </div>
              <p className="doc-summary">{d.access.allowed ? d.summary : d.access.reason}</p>
            </div>
            <div className="doc-side">
              {d.access.allowed ? (
                d.access.code === "grant" && <span className="pill">Temporary access</span>
              ) : d.access.request_pending ? (
                <span className="pill">Request pending</span>
              ) : (
                <button className="btn small" onClick={() => setRequesting(d)}>
                  Request access
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>

      {uploading && (
        <UploadDialog
          onClose={() => setUploading(false)}
          onDone={() => {
            setUploading(false);
            load();
          }}
        />
      )}
      {requesting && (
        <RequestAccessDialog
          doc={{ id: requesting.id, title: requesting.title, team: requesting.owner_team }}
          onClose={() => setRequesting(null)}
          onDone={load}
        />
      )}
    </div>
  );
}

interface Scan {
  suggested_classification: Classification;
  findings: ScanFinding[];
  summary: string;
  tags: string[];
}

function UploadDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { meta, user, notify } = useApp();
  const [file, setFile] = useState<File | null>(null);
  const [scan, setScan] = useState<Scan | null>(null);
  const [scanning, setScanning] = useState(false);
  const [title, setTitle] = useState("");
  const [cls, setCls] = useState<Classification>("internal");
  const [minPos, setMinPos] = useState("");
  const [shares, setShares] = useState<number[]>([]);
  const [discoverable, setDiscoverable] = useState(true);
  const [warning, setWarning] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function pick(f: File | null) {
    setFile(f);
    setScan(null);
    setWarning("");
    setError("");
    if (!f) return;
    setTitle(f.name.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " "));
    setScanning(true);
    const body = new FormData();
    body.append("file", f);
    try {
      const s = await api<Scan>("/api/documents/scan", { method: "POST", body });
      setScan(s);
      setCls(s.suggested_classification === "public" ? "internal" : s.suggested_classification);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't read that file");
    } finally {
      setScanning(false);
    }
  }

  async function submit(acknowledge: boolean) {
    if (!file) return;
    setBusy(true);
    setError("");
    const body = new FormData();
    body.append("file", file);
    body.append("title", title);
    body.append("classification", cls);
    if (minPos) body.append("min_position", minPos);
    body.append("discoverable", String(discoverable));
    body.append("share_team_ids", shares.join(","));
    body.append("acknowledge_sensitivity", String(acknowledge));
    if (scan?.tags.length) body.append("tags", scan.tags.join(","));
    try {
      await api("/api/documents", { method: "POST", body });
      notify(`Uploaded “${title}”`);
      onDone();
    } catch (err) {
      const detail = err instanceof ApiError ? (err.detail as { suggested_classification?: string }) : null;
      if (err instanceof ApiError && err.status === 409 && detail?.suggested_classification) setWarning(err.message);
      else setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Upload a document" onClose={onClose}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          submit(false);
        }}
      >
        <label className="field">
          <span>File (txt, md, pdf, docx, csv)</span>
          <input type="file" accept=".txt,.md,.pdf,.docx,.csv,.json" onChange={(e) => pick(e.target.files?.[0] ?? null)} required />
        </label>

        {scanning && <p className="muted">Checking the file for sensitive information…</p>}
        {scan && (
          <div className={`scan scan-${scan.suggested_classification}`}>
            {scan.findings.length === 0 ? (
              <p>No personal data or secrets found.</p>
            ) : (
              <>
                <p>
                  Found {scan.findings.map((f) => `${f.label.toLowerCase()} (${f.count})`).join(", ")}. Suggested
                  classification: <strong>{scan.suggested_classification}</strong>.
                </p>
              </>
            )}
            {scan.summary && <p className="scan-summary">{scan.summary}</p>}
          </div>
        )}

        <label className="field">
          <span>Title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={255} />
        </label>

        <div className="grid-2">
          <label className="field">
            <span>Classification</span>
            <select value={cls} onChange={(e) => setCls(e.target.value as Classification)}>
              {meta?.classifications.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
            <small className="muted">{meta?.classifications.find((c) => c.value === cls)?.help}</small>
          </label>
          <label className="field">
            <span>Minimum position</span>
            <select value={minPos} onChange={(e) => setMinPos(e.target.value)} disabled={cls === "restricted"}>
              <option value="">Default for classification</option>
              {meta?.positions.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label} and above
                </option>
              ))}
            </select>
          </label>
        </div>

        {cls !== "public" && cls !== "restricted" && (
          <fieldset className="field">
            <legend>Also share with</legend>
            <div className="checks">
              {meta?.teams
                .filter((t) => t.id !== user?.team_id)
                .map((t) => (
                  <label key={t.id} className="check">
                    <input
                      type="checkbox"
                      checked={shares.includes(t.id)}
                      onChange={(e) => setShares((s) => (e.target.checked ? [...s, t.id] : s.filter((x) => x !== t.id)))}
                    />
                    {t.name}
                  </label>
                ))}
            </div>
          </fieldset>
        )}
        {cls !== "restricted" && (
          <label className="check">
            <input type="checkbox" checked={discoverable} onChange={(e) => setDiscoverable(e.target.checked)} />
            Let people without access see the title, so they can request it
          </label>
        )}

        {warning && (
          <div className="warning">
            <p>{warning}</p>
            <button type="button" className="btn small danger" onClick={() => submit(true)} disabled={busy}>
              Upload as {cls} anyway
            </button>
          </div>
        )}
        {error && <p className="error">{error}</p>}
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy || scanning || !file}>
            {busy ? "Uploading…" : "Upload"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
