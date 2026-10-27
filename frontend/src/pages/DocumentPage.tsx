import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, download } from "../api";
import { ClassTag, Empty, RequestAccessDialog, timeAgo } from "../components/bits";
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

  const load = useCallback(() => {
    api<DocDetail>(`/api/documents/${id}`)
      .then(setDoc)
      .catch(() => setMissing(true));
  }, [id]);
  useEffect(load, [load]);

  if (missing)
    return (
      <div className="page">
        <Empty title="This document doesn't exist, or you don't have permission to know about it.">
          <Link to="/library">Back to the library</Link>
        </Empty>
      </div>
    );
  if (!doc) return <div className="page muted">Loading…</div>;

  const { access } = doc;

  async function remove() {
    if (!doc || !window.confirm(`Delete “${doc.title}”? This can't be undone.`)) return;
    await api(`/api/documents/${doc.id}`, { method: "DELETE" });
    notify("Document deleted");
    navigate("/library");
  }

  return (
    <div className="page doc-page">
      <Link to="/library" className="back">
        Library
      </Link>
      <header className={`doc-head class-edge-${doc.classification}`}>
        <h1>{doc.title}</h1>
        <div className="doc-meta">
          <ClassTag value={doc.classification} />
          <span>{doc.owner_team}</span>
          {doc.min_position > 1 && <span>{doc.min_position_label} and above</span>}
          {doc.shared_with.length > 0 && <span>Shared with {doc.shared_with.join(", ")}</span>}
          <span>Updated {timeAgo(doc.updated_at)}</span>
          {doc.uploaded_by && <span>Uploaded by {doc.uploaded_by}</span>}
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
            <div className={`sealed sealed-lg class-edge-${doc.classification}`}>
              <div>
                <div className="sealed-title">You can't open this document</div>
                <div className="sealed-reason">{access.reason}</div>
              </div>
              {access.request_pending ? (
                <span className="pill">Request pending</span>
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
            <h2>Your access</h2>
            <p className={access.allowed ? "ok" : "error"}>{access.allowed ? "You can open this." : "No access."}</p>
            <p className="muted">{access.reason}</p>
            {access.allowed && (
              <button
                className="btn block"
                onClick={() => download(doc.id, doc.filename).catch(() => notify("Download failed", "error"))}
              >
                Download {doc.filename.split(".").pop()?.toUpperCase()}
              </button>
            )}
          </section>

          {doc.tags.length > 0 && (
            <section>
              <h2>Tags</h2>
              <div className="tags">
                {doc.tags.map((t) => (
                  <span key={t} className="tag">
                    {t}
                  </span>
                ))}
              </div>
            </section>
          )}

          {doc.sensitivity_flags.length > 0 && access.allowed && (
            <section>
              <h2>Sensitive content found</h2>
              <p className="muted">{doc.sensitivity_flags.map((f) => f.replace(/_/g, " ")).join(", ")}</p>
            </section>
          )}

          {access.can_manage && (
            <section>
              <h2>Manage</h2>
              <div className="stack-sm">
                <button className="btn block" onClick={() => setEditing((e) => !e)}>
                  {editing ? "Close settings" : "Change access settings"}
                </button>
                <button className="btn block ghost danger-text" onClick={remove}>
                  Delete document
                </button>
              </div>
              {editing && <AccessSettings doc={doc} onSaved={load} />}
              <WhoCanAccess docId={doc.id} key={doc.updated_at} />
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
            <span>
              {user.full_name}
              <span className="muted">
                {" "}
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
