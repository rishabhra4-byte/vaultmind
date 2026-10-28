import { useEffect, useState } from "react";
import { api } from "../../api";
import type { Changes, DocDetail, Version } from "../../types";
import { Modal, timeAgo } from "../bits";
import { IconHistory } from "../icons";

/** The document's versions, newest first, each with a one-line summary of what changed. */
export function VersionsSection({ doc, openChanges }: { doc: DocDetail; openChanges: boolean }) {
  const [versions, setVersions] = useState<Version[] | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    api<Version[]>(`/api/documents/${doc.id}/versions`)
      .then((v) => {
        setVersions(v);
        if (openChanges && v.length > 1) setComparing(true);
      })
      .catch(() => setVersions([]));
  }, [doc.id, doc.version, openChanges]);

  if (!versions || versions.length === 0) return null;
  const shown = showAll ? versions : versions.slice(0, 3);
  return (
    <section>
      <h3>
        <IconHistory width={16} height={16} /> Versions
      </h3>
      <ol className="versions">
        {shown.map((v) => (
          <li key={v.version}>
            <div className="version-line">
              <strong>Version {v.version}</strong>
              <span className="muted small">
                {timeAgo(v.created_at)}
                {v.created_by ? `, ${v.created_by}` : ""}
              </span>
            </div>
            {v.change_summary && <p className="small">{v.change_summary}</p>}
          </li>
        ))}
      </ol>
      {versions.length > 3 && (
        <button className="link-btn small" onClick={() => setShowAll((s) => !s)}>
          {showAll ? "Show fewer" : `Show all ${versions.length} versions`}
        </button>
      )}
      {versions.length > 1 && (
        <button className="btn block" onClick={() => setComparing(true)}>
          See what changed
        </button>
      )}
      {comparing && <ChangesDialog doc={doc} versions={versions} onClose={() => setComparing(false)} />}
    </section>
  );
}

function ChangesDialog({ doc, versions, onClose }: { doc: DocDetail; versions: Version[]; onClose: () => void }) {
  const [to, setTo] = useState(versions[0].version);
  const [from, setFrom] = useState(versions[1].version);
  const [changes, setChanges] = useState<Changes | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setChanges(null);
    setError("");
    if (from >= to) {
      setError("Pick an earlier version on the left.");
      return;
    }
    api<Changes>(`/api/documents/${doc.id}/changes?from=${from}&to=${to}`)
      .then(setChanges)
      .catch(() => setError("Couldn't load the changes."));
  }, [doc.id, from, to]);

  const diff = changes?.diff;
  const more = (shown: number, total: number) =>
    total > shown ? <li className="muted small">…and {total - shown} more</li> : null;

  return (
    <Modal title={`What changed in “${doc.title}”`} onClose={onClose}>
      <div className="stack changes">
        <div className="row wrap changes-pick">
          <label className="field inline-field">
            <span>From</span>
            <select value={from} onChange={(e) => setFrom(Number(e.target.value))}>
              {versions.map((v) => (
                <option key={v.version} value={v.version}>
                  Version {v.version}
                </option>
              ))}
            </select>
          </label>
          <label className="field inline-field">
            <span>To</span>
            <select value={to} onChange={(e) => setTo(Number(e.target.value))}>
              {versions.map((v) => (
                <option key={v.version} value={v.version}>
                  Version {v.version}
                </option>
              ))}
            </select>
          </label>
        </div>
        {error && <p className="error">{error}</p>}
        {!error && !changes && <p className="muted">Comparing…</p>}
        {changes?.summary && <p className="doc-abstract">{changes.summary}</p>}
        {diff && diff.changed.length + diff.added.length + diff.removed.length === 0 && (
          <p className="muted">The text didn't change between these versions.</p>
        )}
        {diff && diff.changed.length > 0 && (
          <section>
            <h3>Changed</h3>
            <ul className="diff">
              {diff.changed.map((c, i) => (
                <li key={i}>
                  <del>{c.before}</del>
                  <ins>{c.after}</ins>
                </li>
              ))}
              {more(diff.changed.length, diff.counts.changed)}
            </ul>
          </section>
        )}
        {diff && diff.added.length > 0 && (
          <section>
            <h3>Added</h3>
            <ul className="diff">
              {diff.added.map((s, i) => (
                <li key={i}>
                  <ins>{s}</ins>
                </li>
              ))}
              {more(diff.added.length, diff.counts.added)}
            </ul>
          </section>
        )}
        {diff && diff.removed.length > 0 && (
          <section>
            <h3>Removed</h3>
            <ul className="diff">
              {diff.removed.map((s, i) => (
                <li key={i}>
                  <del>{s}</del>
                </li>
              ))}
              {more(diff.removed.length, diff.counts.removed)}
            </ul>
          </section>
        )}
      </div>
    </Modal>
  );
}
