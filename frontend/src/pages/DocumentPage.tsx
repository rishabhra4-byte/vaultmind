import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, download } from "../api";
import {
  Avatar,
  ClassTag,
  Empty,
  Modal,
  RequestAccessDialog,
  Skeleton,
  TeamChip,
  teamColor,
  timeAgo,
  usePageTitle,
} from "../components/bits";
import { DocAsk } from "../components/doc/DocAsk";
import { NewVersionDialog } from "../components/doc/NewVersionDialog";
import { VersionsSection } from "../components/doc/Versions";
import {
  IconCloud,
  IconDownload,
  IconExternal,
  IconFile,
  IconLock,
  IconShield,
  IconSparkle,
  IconTrash,
  IconUpload,
} from "../components/icons";
import { RichText } from "../components/RichText";
import { useApp } from "../state";
import type { Classification, DocDetail, Group, User } from "../types";

const VIA: Record<string, string> = {
  admin: "Administrator",
  uploader: "Uploaded it",
  grant: "Temporary grant",
  team: "Owning team",
  shared: "Shared team",
  group: "Shared group",
  source: "Shared in source",
  public: "Everyone",
  executive: "Executive",
};

export default function DocumentPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const { notify } = useApp();
  const [doc, setDoc] = useState<DocDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [asking, setAsking] = useState(false);
  const [newVersion, setNewVersion] = useState(false);
  usePageTitle(missing ? "Document not found" : (doc?.title ?? "Document"));

  const [version, setVersion] = useState(0);

  useEffect(() => {
    setDoc(null); // don't show the previous document while the next one loads
    setEditing(false);
    setAsking(false);
  }, [id]);

  const load = useCallback(() => {
    setMissing(false);
    setVersion((v) => v + 1);
    api<DocDetail>(`/api/documents/${id}`)
      .then(setDoc)
      .catch(() => setMissing(true));
  }, [id]);
  useEffect(load, [load]);

  // A citation link (/library/12#p3) opens the document at that paragraph and marks it.
  const target = /^#p(\d+)$/.exec(location.hash)?.[1];
  const highlighted = target !== undefined ? Number(target) : null;
  useEffect(() => {
    if (highlighted === null || !doc) return;
    const el = document.getElementById(`p${highlighted}`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
    el?.focus({ preventScroll: true });
  }, [highlighted, doc]);

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
  const synced = doc.source !== null;

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

  async function setAi(on: boolean) {
    if (!doc) return;
    try {
      await api(`/api/documents/${doc.id}`, { method: "PATCH", json: { ai_enabled: on } });
      notify(on ? "The assistant can read this document again" : "The assistant won't read this document");
      load();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't save that", "error");
    }
  }

  return (
    <div className={`page doc-page class-edge-${doc.classification} ${asking ? "with-drawer" : ""}`}>
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
            {doc.shared_with_groups.length > 0 && <span>Shared with {doc.shared_with_groups.join(", ")}</span>}
            <span>Updated {timeAgo(doc.updated_at)}</span>
            {doc.version > 1 && <span>Version {doc.version}</span>}
            {doc.uploaded_by && !synced && <span>Uploaded by {doc.uploaded_by}</span>}
          </div>
        </div>
      </header>

      <div className="doc-layout">
        <article className="doc-body">
          {access.allowed ? (
            <>
              {doc.summary && <p className="doc-abstract">{doc.summary}</p>}
              <div className="paper">
                {doc.passages.length > 0 ? (
                  doc.passages.map((p) => (
                    <section
                      key={p.ordinal}
                      id={`p${p.ordinal}`}
                      tabIndex={-1}
                      className={`passage ${highlighted === p.ordinal ? "hl" : ""}`}
                      aria-label={highlighted === p.ordinal ? "Cited paragraph" : undefined}
                    >
                      <RichText text={p.text} cites={new Map()} headingLevel={2} />
                    </section>
                  ))
                ) : (
                  <RichText text={doc.preview ?? ""} cites={new Map()} headingLevel={2} />
                )}
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
                doc.classification !== "restricted" &&
                doc.acl_mode !== "mirror" && (
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
                <button className="btn block primary" onClick={() => setAsking(true)} aria-expanded={asking}>
                  <IconSparkle /> Ask about this document
                </button>
                <button className="btn block" onClick={onDownload} disabled={downloading}>
                  <IconDownload /> {downloading ? "Downloading…" : `Download ${doc.filename.split(".").pop()?.toUpperCase()}`}
                </button>
              </div>
            )}
          </section>

          {synced && doc.source && (
            <section>
              <h3>
                <IconCloud width={16} height={16} /> From {doc.source.label}
              </h3>
              <p className="muted small">
                Synced from <strong>{doc.source.name}</strong>
                {doc.source_path && (
                  <>
                    {" "}
                    at <span className="path">{doc.source_path}</span>
                  </>
                )}
                .{" "}
                {doc.acl_mode === "mirror"
                  ? `Who can open it follows its sharing in ${doc.source.label}.`
                  : "Who can open it follows the source's folder rules."}
              </p>
              {doc.external_url && (
                <a className="btn block" href={doc.external_url} target="_blank" rel="noopener noreferrer">
                  <IconExternal /> Open in {doc.source.label}
                </a>
              )}
            </section>
          )}

          {access.allowed && <VersionsSection doc={doc} openChanges={params.get("changes") === "1"} />}

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
              <label className="check">
                <span className="switch">
                  <input type="checkbox" checked={doc.ai_enabled} onChange={(e) => setAi(e.target.checked)} />
                  <span />
                </span>
                Let the assistant read this document
              </label>
              <p className="muted small">
                {doc.ai_enabled
                  ? "Passages can be sent to the AI to answer questions, only for people who can open it."
                  : "The AI never sees this document. People who can open it still find it in search."}
              </p>
              <div className="stack-sm">
                {synced ? (
                  <p className="muted small">Sharing and new versions are managed in {doc.source?.label}.</p>
                ) : (
                  <>
                    <button className="btn block" onClick={() => setEditing((e) => !e)} aria-expanded={editing}>
                      {editing ? "Close access settings" : "Change access settings"}
                    </button>
                    <button className="btn block" onClick={() => setNewVersion(true)}>
                      <IconUpload /> Upload a new version
                    </button>
                    <button className="btn block ghost danger-text" onClick={() => setConfirmDelete(true)}>
                      <IconTrash /> Delete document
                    </button>
                  </>
                )}
              </div>
              {editing && !synced && <AccessSettings doc={doc} onSaved={load} />}
              <WhoCanAccess docId={doc.id} key={`${doc.id}-${version}`} />
            </section>
          )}
        </aside>
      </div>

      {asking && access.allowed && <DocAsk doc={doc} onClose={() => setAsking(false)} />}
      {requesting && (
        <RequestAccessDialog
          doc={{ id: doc.id, title: doc.title, team: doc.owner_team }}
          onClose={() => setRequesting(false)}
          onDone={load}
        />
      )}
      {newVersion && (
        <NewVersionDialog
          doc={doc}
          onClose={() => setNewVersion(false)}
          onDone={() => {
            setNewVersion(false);
            load();
          }}
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
  const [groups, setGroups] = useState<Group[]>([]);
  const [groupShares, setGroupShares] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Group[]>("/api/groups")
      .then((list) => {
        setGroups(list);
        setGroupShares(list.filter((g) => doc.shared_with_groups.includes(g.name)).map((g) => g.id));
      })
      .catch(() => {});
  }, [doc.shared_with_groups]);

  async function save() {
    setBusy(true);
    try {
      await api(`/api/documents/${doc.id}`, {
        method: "PATCH",
        json: { classification: cls, min_position: minPos, share_team_ids: shares, share_group_ids: groupShares },
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
        <legend>Shared with teams</legend>
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
      {groups.length > 0 && (
        <fieldset className="field">
          <legend>Shared with groups</legend>
          <div className="checks">
            {groups.map((g) => (
              <label key={g.id} className="check-chip" style={{ "--tc": teamColor(g.name) } as CSSProperties}>
                <input
                  type="checkbox"
                  checked={groupShares.includes(g.id)}
                  onChange={(e) =>
                    setGroupShares((s) => (e.target.checked ? [...s, g.id] : s.filter((x) => x !== g.id)))
                  }
                />
                {g.name}
              </label>
            ))}
          </div>
        </fieldset>
      )}
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
