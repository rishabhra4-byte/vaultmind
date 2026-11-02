import { useCallback, useEffect, useRef, useState, type CSSProperties, type DragEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import {
  ClassTag,
  Empty,
  Highlight,
  Modal,
  RequestAccessDialog,
  Skeleton,
  TeamChip,
  teamColor,
  timeAgo,
  usePageTitle,
} from "../components/bits";
import { IconCloud, IconFile, IconLock, IconSearch, IconUpload } from "../components/icons";
import { useApp } from "../state";
import type { Classification, Doc, ScanFinding } from "../types";

const CLASS_COLOR: Record<Classification, string> = {
  public: "var(--c-public)",
  internal: "var(--c-internal)",
  confidential: "var(--c-confidential)",
  restricted: "var(--c-restricted)",
};

export default function Library() {
  const { meta } = useApp();
  const [params, setParams] = useSearchParams();
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [q, setQ] = useState(params.get("q") ?? "");
  const [team, setTeam] = useState(params.get("team") ?? "");
  const [src, setSrc] = useState(params.get("source") ?? "");
  const [owner, setOwner] = useState(params.get("owner") ?? "");
  const [recent, setRecent] = useState(params.get("updated") ?? "");
  // Everyone who has uploaded something seen so far, so the list doesn't shrink as you filter.
  const owners = useRef(new Map<number, string>());
  const latest = useRef(0); // only the newest search's results are shown, however the answers arrive
  const [cls, setCls] = useState<Classification | "">("");
  const [showLocked, setShowLocked] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [requesting, setRequesting] = useState<Doc | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  usePageTitle("Library");

  // Team, owner and date are filtered by the server; classification and source are filtered
  // here so the chips can show counts for every level and the source list stays complete.
  const load = useCallback(() => {
    const p = new URLSearchParams({ include_locked: String(showLocked) });
    if (q.trim()) p.set("q", q.trim());
    if (team) p.set("team_id", team);
    if (owner) p.set("owner_id", owner);
    if (recent) p.set("updated_within_days", recent);
    setFailed(false);
    const n = ++latest.current;
    api<Doc[]>(`/api/documents?${p}`)
      .then((list) => {
        if (n !== latest.current) return;
        for (const d of list) if (d.uploaded_by_id && d.uploaded_by) owners.current.set(d.uploaded_by_id, d.uploaded_by);
        setDocs(list);
      })
      .catch(() => {
        if (n !== latest.current) return;
        setDocs([]);
        setFailed(true);
      });
  }, [q, team, owner, recent, showLocked]);

  useEffect(() => {
    const t = setTimeout(load, q ? 220 : 0);
    return () => clearTimeout(t);
  }, [load, q]);

  useEffect(() => {
    const next: Record<string, string> = {};
    if (q.trim()) next.q = q.trim();
    if (team) next.team = team;
    if (src) next.source = src;
    if (owner) next.owner = owner;
    if (recent) next.updated = recent;
    setParams(next, { replace: true });
  }, [q, team, src, owner, recent, setParams]);

  // "/" focuses search, like most document tools
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest("input, textarea, select, [contenteditable]");
      if (e.key === "/" && !typing && !document.querySelector("dialog[open]")) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Where documents come from: uploaded here, or synced from a connected drive or folder.
  const sources = [...new Map((docs ?? []).filter((d) => d.source).map((d) => [d.source!.id, d.source!])).values()];
  const fromSource = (d: Doc) => !src || (src === "uploaded" ? !d.source : String(d.source?.id) === src);
  const visible = docs?.filter((d) => (!cls || d.classification === cls) && fromSource(d)) ?? null;
  const count = (c: Classification) => docs?.filter((d) => d.classification === c).length ?? 0;
  const openable = visible?.filter((d) => d.access.allowed).length ?? 0;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Library</h1>
          <p className="muted">
            {visible
              ? `${openable} you can open${showLocked ? `, ${visible.length - openable} you can request` : ""}`
              : "Loading documents…"}
          </p>
        </div>
        <button className="btn primary" onClick={() => setUploading(true)}>
          <IconUpload /> Upload a document
        </button>
      </header>

      <div className="filters">
        <label className="search-box">
          <IconSearch />
          <input
            ref={searchRef}
            type="search"
            placeholder="Search titles and contents"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Search documents"
          />
          {!q && <span className="kbd" aria-hidden>/</span>}
        </label>
        <select value={team} onChange={(e) => setTeam(e.target.value)} aria-label="Filter by team">
          <option value="">All teams</option>
          {meta?.teams.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <select value={owner} onChange={(e) => setOwner(e.target.value)} aria-label="Filter by who added it">
          <option value="">Anyone</option>
          {[...owners.current.entries()]
            .sort((a, b) => a[1].localeCompare(b[1]))
            .map(([id, name]) => (
              <option key={id} value={id}>
                Added by {name}
              </option>
            ))}
        </select>
        <select value={recent} onChange={(e) => setRecent(e.target.value)} aria-label="Filter by when it was updated">
          <option value="">Updated any time</option>
          {[7, 30, 90, 365].map((d) => (
            <option key={d} value={d}>
              Updated in the last {d === 365 ? "year" : `${d} days`}
            </option>
          ))}
        </select>
        {(sources.length > 0 || src) && (
          <select value={src} onChange={(e) => setSrc(e.target.value)} aria-label="Filter by where documents come from">
            <option value="">All sources</option>
            <option value="uploaded">Uploaded to VaultMind</option>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.label})
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="chip-row" role="group" aria-label="Filter by classification">
        <button className="chip all" aria-pressed={cls === ""} onClick={() => setCls("")}>
          All <span className="chip-count">{docs?.length ?? ""}</span>
        </button>
        {(meta?.classifications ?? []).filter((c) => count(c.value) > 0 || cls === c.value).map((c) => (
          <button
            key={c.value}
            className="chip"
            aria-pressed={cls === c.value}
            onClick={() => setCls(cls === c.value ? "" : c.value)}
            style={{ "--chip": CLASS_COLOR[c.value] } as CSSProperties}
            title={c.help}
          >
            {c.label} <span className="chip-count">{count(c.value)}</span>
          </button>
        ))}
        <label className="check">
          <span className="switch">
            <input type="checkbox" checked={showLocked} onChange={(e) => setShowLocked(e.target.checked)} />
            <span />
          </span>
          Show documents I can request
        </label>
      </div>

      {visible === null && <Skeleton rows={5} />}

      {failed && (
        <Empty title="The library didn't load." icon={<IconFile />}>
          Check your connection, then{" "}
          <button className="link-btn" onClick={load}>
            <u>try again</u>
          </button>
          .
        </Empty>
      )}

      {!failed && visible && visible.length === 0 && (
        <Empty title={q ? `Nothing matches “${q}”` : "No documents here yet"} icon={<IconSearch />}>
          {q || cls || team || src || owner || recent ? (
            <>
              Try fewer words, or{" "}
              <button
                className="link-btn"
                onClick={() => {
                  setQ("");
                  setCls("");
                  setTeam("");
                  setSrc("");
                  setOwner("");
                  setRecent("");
                }}
              >
                <u>clear the filters</u>
              </button>
              .
            </>
          ) : (
            "Upload the first one for your team."
          )}
        </Empty>
      )}

      <ul className="doc-list">
        {visible?.map((d, i) => (
          <li
            key={d.id}
            className={`doc-row class-edge-${d.classification} ${d.access.allowed ? "" : "locked"}`}
            style={{ "--i": Math.min(i, 12) } as CSSProperties}
          >
            <span className="doc-icon">{d.access.allowed ? <IconFile /> : <IconLock />}</span>
            <div className="doc-main">
              <Link to={`/library/${d.id}`} className="doc-title">
                <Highlight text={d.title} query={q} />
                {!d.access.allowed && <span className="sr-only"> (locked)</span>}
              </Link>
              <div className="doc-meta">
                <ClassTag value={d.classification} />
                <TeamChip team={d.owner_team} />
                {d.min_position > 1 && <span>{d.min_position_label} and above</span>}
                {d.source && (
                  <span className="source-chip" title={`Synced from ${d.source.name}`}>
                    <IconCloud width={13} height={13} /> {d.source.label}
                  </span>
                )}
                <span>Updated {timeAgo(d.updated_at)}</span>
              </div>
              <p className="doc-summary">{d.access.allowed ? d.summary : d.access.reason}</p>
            </div>
            <div className="doc-side">
              {d.access.allowed ? (
                d.access.code === "grant" && <span className="pill grant">Temporary access</span>
              ) : d.access.request_pending ? (
                <span className="pill pending">Request pending</span>
              ) : (
                <button className="btn small" onClick={() => setRequesting(d)} aria-label={`Request access to ${d.title}`}>
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

const ACCEPT = ".txt,.md,.pdf,.docx,.xlsx,.pptx,.csv,.json";
const OLD_OFFICE: Record<string, string> = { ".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx" };
const MAX_MB = 20;

function UploadDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { meta, user, notify } = useApp();
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
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
  const inputRef = useRef<HTMLInputElement>(null);

  async function pick(f: File | null) {
    setScan(null);
    setWarning("");
    setError("");
    if (!f) return setFile(null);
    const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
    if (!ACCEPT.split(",").includes(ext)) {
      setFile(null);
      if (OLD_OFFICE[ext]) return setError(`Old Office files (${ext}) can't be read. Save it as ${OLD_OFFICE[ext]} in Office, then upload that.`);
      return setError(`“${f.name}” isn't a supported file. Use ${ACCEPT.replaceAll(",", ", ")}.`);
    }
    if (f.size > MAX_MB * 1024 * 1024) {
      setFile(null);
      return setError(`That file is ${(f.size / 1048576).toFixed(1)} MB. The limit is ${MAX_MB} MB.`);
    }
    setFile(f);
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

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    pick(e.dataTransfer.files?.[0] ?? null);
  }

  async function submit(acknowledge: boolean) {
    if (!file) return setError("Choose a file first.");
    if (!title.trim()) return setError("Give the document a title.");
    setBusy(true);
    setError("");
    const body = new FormData();
    body.append("file", file);
    body.append("title", title.trim());
    body.append("classification", cls);
    if (minPos) body.append("min_position", minPos);
    body.append("discoverable", String(discoverable));
    body.append("share_team_ids", shares.join(","));
    body.append("acknowledge_sensitivity", String(acknowledge));
    if (scan?.tags.length) body.append("tags", scan.tags.join(","));
    try {
      await api("/api/documents", { method: "POST", body });
      notify(`Uploaded “${title.trim()}”`);
      onDone();
    } catch (err) {
      const detail = err instanceof ApiError ? (err.detail as { suggested_classification?: string }) : null;
      if (err instanceof ApiError && err.status === 409 && detail?.suggested_classification) setWarning(err.message);
      else setError(err instanceof ApiError ? err.message : "Upload failed. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  const help = meta?.classifications.find((c) => c.value === cls)?.help;

  return (
    <Modal title="Upload a document" onClose={onClose}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          submit(false);
        }}
        noValidate
      >
        <div
          className={`dropzone ${over ? "over" : ""}`}
          role="button"
          tabIndex={0}
          aria-label="Choose a file to upload"
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), inputRef.current?.click())}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
        >
          <span className="dz-icon">
            <IconUpload />
          </span>
          {file ? (
            <>
              <strong>{file.name}</strong>
              <span className="muted small">{(file.size / 1024).toFixed(1)} KB. Click to choose a different file.</span>
            </>
          ) : (
            <>
              <strong>Drop a file here, or click to browse</strong>
              <span className="muted small">TXT, MD, PDF, DOCX, CSV or JSON, up to {MAX_MB} MB</span>
            </>
          )}
          <input ref={inputRef} type="file" accept={ACCEPT} onChange={(e) => pick(e.target.files?.[0] ?? null)} />
        </div>

        {scanning && <p className="muted">Checking the file for sensitive information…</p>}
        {scan && (
          <div className={`scan scan-${scan.suggested_classification}`}>
            {scan.findings.length === 0 ? (
              <p>No personal data or secrets found.</p>
            ) : (
              <p>
                Found {scan.findings.map((f) => `${f.label.toLowerCase()} (${f.count})`).join(", ")}. Suggested
                classification: <strong>{scan.suggested_classification}</strong>.
              </p>
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
            <small className="muted">{help}</small>
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
                  <label key={t.id} className="check-chip" style={{ "--tc": teamColor(t.name) } as CSSProperties}>
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
            <span className="switch">
              <input type="checkbox" checked={discoverable} onChange={(e) => setDiscoverable(e.target.checked)} />
              <span />
            </span>
            Let people without access see the title, so they can request it
          </label>
        )}

        {warning && (
          <div className="warning" role="alert">
            <p>{warning}</p>
            <button type="button" className="btn small danger" onClick={() => submit(true)} disabled={busy}>
              Upload as {cls} anyway
            </button>
          </div>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
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
