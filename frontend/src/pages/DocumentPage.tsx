import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, download } from "../api";
import { Avatar, ClassTag, Empty, Modal, RequestAccessDialog, Skeleton, TeamChip, teamColor, timeAgo } from "../components/bits";
import { IconDownload, IconFile, IconLock, IconShield, IconSparkle, IconTrash } from "../components/icons";
import { RichText } from "../components/RichText";
import { useApp } from "../state";
import type { Classification, DocDetail, User } from "../types";

const VIA: Record<string, string> = {
  admin: "Administrator",
  uploader: "Uploaded it",
  grant: "Temporary grant",
  team: "Owning team",
  shared: "Shared team",
  public: "Everyone",
  executive: "Executive",
};

export default function DocumentPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { notify } = useApp();
  const [doc, setDoc] = useState<DocDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const [version, setVersion] = useState(0);

  useEffect(() => {
    setDoc(null); // don't show the previous document while the next one loads
    setEditing(false);
  }, [id]);

  const load = useCallback(() => {
    setMissing(false);
    setVersion((v) => v + 1);
    api<DocDetail>(`/api/documents/${id}`)
      .then(setDoc)
      .catch(() => setMissing(true));
  }, [id]);
  useEffect(load, [load]);

  if (missing)
    return (
      <div className="page">
        <Empty title="This document doesn't exist, or you don't have permission to know about it." icon={<IconLock />}>
          <Link to="/library">Back to the library</Link>
        </Empty>
      </div>
    );
  if (!doc)
    return (
      <div className="page">
        <Skeleton rows={3} />
      </div>
    );

  const { access } = doc;

  async function remove() {
    if (!doc) return;
    try {
      await api(`/api/documents/${doc.id}`, { method: "DELETE" });
      notify(`Deleted “${doc.title}”`);
      navigate("/library");
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't delete the document", "error");
    }
  }

  async function onDownload() {
    if (!doc) return;
    setDownloading(true);
    try {
      await download(doc.id, doc.filename);
    } catch {
      notify("Download failed. Try again.", "error");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className={`page doc-page class-edge-${doc.classification}`}>
      <Link to="/library" className="back">
        Library
      </Link>
      <header className="doc-hero">
        <span className="doc-icon">{access.allowed ? <IconFile /> : <IconLock />}</span>
        <div>
          <h1>{doc.title}</h1>
          <div className="doc-meta">
            <ClassTag value={doc.classification} />
            <TeamChip team={doc.owner_team} />
            {doc.min_position > 1 && <span>{doc.min_position_label} and above</span>}
            {doc.shared_with.length > 0 && <span>Shared with {doc.shared_with.join(", ")}</span>}
            <span>Updated {timeAgo(doc.updated_at)}</span>
            {doc.uploaded_by && <span>Uploaded by {doc.uploaded_by}</span>}
          </div>
        </div>
      </header>

      <div className="doc-layout">
        <article className="doc-body">
          {access.allowed ? (
            <>
              {doc.summary && <p className="doc-abstract">{doc.summary}</p>}
              <div className="paper">
                <RichText text={doc.preview ?? ""} cites={new Map()} />
              </div>
            </>
          ) : (
            <div className="sealed sealed-lg">
              <div className="sealed-main">
                <span className="doc-icon">
                  <IconLock />
                </span>
                <div>
                  <div className="sealed-title">You can't open this document yet</div>
                  <div className="sealed-reason">{access.reason}</div>
                </div>
              </div>
              {access.request_pending ? (
                <span className="pill pending">Request pending. You'll get access once it's approved.</span>
              ) : (
                doc.classification !== "restricted" && (
                  <button className="btn primary" onClick={() => setRequesting(true)}>
                    Request access
                  </button>
                )
              )}
            </div>
          )}
        </article>

        <aside className="doc-aside">
          <section>
            <div className={`access-badge ${access.allowed ? "" : "no"}`}>
              <span className="dot">{access.allowed ? <IconShield width={16} height={16} /> : <IconLock width={16} height={16} />}</span>
              {access.allowed ? "You can open this" : "No access"}
            </div>
            <p className="muted small">{access.reason}</p>
            {access.allowed && (
              <div className="stack-sm">
                <button className="btn block" onClick={onDownload} disabled={downloading}>
                  <IconDownload /> {downloading ? "Downloading…" : `Download ${doc.filename.split(".").pop()?.toUpperCase()}`}
                </button>
                <button
                  className="btn block"
                  onClick={() => navigate(`/?ask=${encodeURIComponent(`What are the key points of “${doc.title}”?`)}`)}
                >
                  <IconSparkle /> Ask the assistant about this
                </button>
              </div>
            )}
          </section>

          {doc.tags.length > 0 && (
            <section>
              <h3>Tags</h3>
              <div className="tags">
                {doc.tags.map((t) => (
                  <Link key={t} to={`/library?q=${encodeURIComponent(t)}`} className="tag">
                    {t}
                  </Link>
                ))}
              </div>
            </section>
          )}

          {doc.sensitivity_flags.length > 0 && access.allowed && (
            <section>
              <h3>Sensitive content found</h3>
              <p className="muted small">{doc.sensitivity_flags.map((f) => f.replace(/_/g, " ")).join(", ")}</p>
            </section>
          )}

          {access.can_manage && (
            <section>
              <h3>Manage</h3>
              <div className="stack-sm">
                <button className="btn block" onClick={() => setEditing((e) => !e)} aria-expanded={editing}>
                  {editing ? "Close access settings" : "Change access settings"}
                </button>
                <button className="btn block ghost danger-text" onClick={() => setConfirmDelete(true)}>
                  <IconTrash /> Delete document
                </button>
              </div>
              {editing && <AccessSettings doc={doc} onSaved={load} />}
              <WhoCanAccess docId={doc.id} key={`${doc.id}-${version}`} />
            </section>
          )}
        </aside>
      </div>

      {requesting && (
        <RequestAccessDialog
          doc={{ id: doc.id, title: doc.title, team: doc.owner_team }}
          onClose={() => setRequesting(false)}
          onDone={load}
        />
      )}
      {confirmDelete && (
        <Modal title="Delete this document?" onClose={() => setConfirmDelete(false)}>
          <div className="stack">
            <p className="muted">
              “{doc.title}” will be removed for everyone, including people with temporary access. This can't be undone.
            </p>
            <div className="row end">
              <button className="btn ghost" onClick={() => setConfirmDelete(false)}>
                Keep it
              </button>
              <button className="btn danger" onClick={remove}>
                Delete document
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function AccessSettings({ doc, onSaved }: { doc: DocDetail; onSaved: () => void }) {
  const { meta, notify } = useApp();
  const [cls, setCls] = useState<Classification>(doc.classification);
  const [minPos, setMinPos] = useState(doc.min_position);
  const [shares, setShares] = useState<number[]>(
    meta?.teams.filter((t) => doc.shared_with.includes(t.name)).map((t) => t.id) ?? [],
  );
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await api(`/api/documents/${doc.id}`, {
        method: "PATCH",
        json: { classification: cls, min_position: minPos, share_team_ids: shares },
      });
      notify("Access settings saved");
      onSaved();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't save", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack settings">
      <label className="field">
        <span>Classification</span>
        <select value={cls} onChange={(e) => setCls(e.target.value as Classification)}>
          {meta?.classifications.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Minimum position</span>
        <select value={minPos} onChange={(e) => setMinPos(Number(e.target.value))}>
          {meta?.positions.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label} and above
            </option>
          ))}
        </select>
      </label>
      <fieldset className="field">
        <legend>Shared with</legend>
        <div className="checks">
          {meta?.teams
            .filter((t) => t.id !== doc.owner_team_id)
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
      <button className="btn primary" onClick={save} disabled={busy}>
        {busy ? "Saving…" : "Save access settings"}
      </button>
    </div>
  );
}

function WhoCanAccess({ docId }: { docId: number }) {
  const [people, setPeople] = useState<{ user: User; via: string }[] | null>(null);
  useEffect(() => {
    api<{ user: User; via: string }[]>(`/api/documents/${docId}/who-can-access`)
      .then(setPeople)
      .catch(() => setPeople([]));
  }, [docId]);
  if (!people) return null;
  return (
    <div className="who">
      <h3>Who can open it ({people.length})</h3>
      <ul>
        {people.map(({ user, via }) => (
          <li key={user.id}>
            <Avatar name={user.full_name} team={user.team_name} size={28} />
            <span>
              <strong>{user.full_name}</strong>
              <span className="muted small">
                {user.position_label}, {user.team_name}
              </span>
            </span>
            <span className="via">{VIA[via] ?? via}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
