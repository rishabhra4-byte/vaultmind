import { useRef, useState } from "react";
import { api, ApiError } from "../../api";
import { useApp } from "../../state";
import type { DocDetail } from "../../types";
import { Modal } from "../bits";
import { IconUpload } from "../icons";

const ACCEPT = ".txt,.md,.pdf,.docx,.csv,.json";

/** Replace the document's file. Everyone who read it recently is told what changed. */
export function NewVersionDialog({ doc, onClose, onDone }: { doc: DocDetail; onClose: () => void; onDone: () => void }) {
  const { notify } = useApp();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  async function submit(acknowledge: boolean) {
    if (!file) return setError("Choose the new file first.");
    setBusy(true);
    setError("");
    const body = new FormData();
    body.append("file", file);
    body.append("acknowledge_sensitivity", String(acknowledge));
    try {
      const res = await api<{ version: number; change_summary: string }>(`/api/documents/${doc.id}/versions`, {
        method: "POST",
        body,
      });
      notify(`Saved version ${res.version}. Recent readers will be told what changed.`);
      onDone();
    } catch (err) {
      const detail = err instanceof ApiError ? (err.detail as { suggested_classification?: string }) : null;
      if (err instanceof ApiError && err.status === 409 && detail?.suggested_classification) setWarning(err.message);
      else setError(err instanceof ApiError ? err.message : "Upload failed. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Upload a new version" onClose={onClose}>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          submit(false);
        }}
      >
        <p className="muted">
          The new file replaces version {doc.version} of “{doc.title}”. Access settings stay the same, earlier versions are
          kept, and people who read it recently get a summary of what changed.
        </p>
        <button type="button" className="dropzone" onClick={() => inputRef.current?.click()}>
          <span className="dz-icon">
            <IconUpload />
          </span>
          <strong>{file ? file.name : "Choose the new file"}</strong>
          <span className="muted small">TXT, MD, PDF, DOCX, CSV or JSON</span>
        </button>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          hidden
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setError("");
            setWarning("");
          }}
        />
        {warning && (
          <div className="warning" role="alert">
            <p>{warning}</p>
            <button type="button" className="btn small danger" onClick={() => submit(true)} disabled={busy}>
              Upload it anyway
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
          <button className="btn primary" disabled={busy || !file}>
            {busy ? "Uploading…" : "Upload new version"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
