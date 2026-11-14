import { useEffect, useRef, useState, type CSSProperties, type DragEvent } from "react";
import { api, ApiError } from "../../api";
import { Modal, teamColor } from "../bits";
import { IconUpload } from "../icons";
import { useApp } from "../../state";
import type { Classification, ScanFinding } from "../../types";

interface Scan {
  suggested_classification: Classification;
  findings: ScanFinding[];
  summary: string;
  tags: string[];
}

const ACCEPT = ".txt,.md,.pdf,.docx,.xlsx,.pptx,.csv,.json";
const OLD_OFFICE: Record<string, string> = { ".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx" };
const MAX_MB = 20;

/** Upload one file: it is checked for sensitive information first, then classified and shared.
 *  `folder` is where it goes; `initialFile` is a file dropped on the library. */
export function UploadDialog({
  onClose,
  onDone,
  folder,
  initialFile,
}: {
  onClose: () => void;
  onDone: () => void;
  folder?: { id: number; name: string } | null;
  initialFile?: File | null;
}) {
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
  const started = useRef(false);

  useEffect(() => {
    if (initialFile && !started.current) {
      started.current = true;
      void pick(initialFile);
    }
  }, [initialFile]);

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
    if (folder) body.append("folder_id", String(folder.id));
    if (scan?.tags.length) body.append("tags", scan.tags.join(","));
    try {
      await api("/api/documents", { method: "POST", body });
      notify(folder ? `Uploaded “${title.trim()}” to ${folder.name}` : `Uploaded “${title.trim()}”`);
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
    <Modal title={folder ? `Upload a document to ${folder.name}` : "Upload a document"} onClose={onClose}>
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
