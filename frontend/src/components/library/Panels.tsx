import { useEffect, useRef, useState, type CSSProperties, type DragEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../api";
import { ClassTag, Empty, Modal, Skeleton, TeamChip, timeAgo } from "../bits";
import { IconChevronRight, IconFile, IconFolder, IconHome, IconLibrary, IconLock, IconPin, IconRestore, IconTrash } from "../icons";
import { useApp } from "../../state";
import type { Deleted, Doc, DocDetail, Folder } from "../../types";
import { childrenOf, fileSize, pathTo, shortDate, typeName, within } from "./explorer";

/** Where the library is looking: everything, the home view, one folder, or the recycle bin. */
export type Place = { kind: "all" } | { kind: "home" } | { kind: "bin" } | { kind: "folder"; id: number };

export const DRAG_TYPE = "application/x-vaultmind-items";

/** Makes an element a place to drop documents and folders on. */
export function dropProps(onDropKeys: (keys: string[]) => void, setOver: (over: boolean) => void) {
  return {
    onDragOver: (e: DragEvent) => {
      if (!e.dataTransfer.types.includes(DRAG_TYPE)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      setOver(true);
    },
    onDragLeave: () => setOver(false),
    onDrop: (e: DragEvent) => {
      const raw = e.dataTransfer.getData(DRAG_TYPE);
      setOver(false);
      if (!raw) return;
      e.preventDefault();
      e.stopPropagation();
      onDropKeys(raw.split(","));
    },
  };
}

// ---------------------------------------------------------------- navigation pane

function TreeNode({
  folder,
  folders,
  place,
  open,
  toggle,
  go,
  onDropKeys,
  depth,
}: {
  folder: Folder;
  folders: Folder[];
  place: Place;
  open: Set<number>;
  toggle: (id: number) => void;
  go: (p: Place) => void;
  onDropKeys: (keys: string[], to: number | null) => void;
  depth: number;
}) {
  const kids = childrenOf(folders, folder.id);
  const [over, setOver] = useState(false);
  const here = place.kind === "folder" && place.id === folder.id;
  const expanded = open.has(folder.id);
  return (
    <li>
      <div
        className={`nav-item ${here ? "here" : ""} ${over ? "drop-over" : ""}`}
        style={{ "--depth": depth } as CSSProperties}
        {...dropProps((keys) => onDropKeys(keys, folder.id), setOver)}
      >
        {kids.length > 0 ? (
          <button
            type="button"
            className="nav-twist"
            aria-expanded={expanded}
            aria-label={`${expanded ? "Close" : "Open"} ${folder.name}`}
            onClick={() => toggle(folder.id)}
          >
            <IconChevronRight width={14} height={14} />
          </button>
        ) : (
          <span className="nav-twist" aria-hidden />
        )}
        <button type="button" className="nav-go" aria-current={here ? "page" : undefined} onClick={() => go({ kind: "folder", id: folder.id })}>
          <IconFolder width={16} height={16} />
          <span className="nav-name">{folder.name}</span>
          {folder.documents > 0 && <span className="nav-count">{folder.documents}</span>}
        </button>
      </div>
      {expanded && kids.length > 0 && (
        <ul>
          {kids.map((k) => (
            <TreeNode key={k.id} folder={k} folders={folders} place={place} open={open} toggle={toggle} go={go} onDropKeys={onDropKeys} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

/** The left pane: Home, the whole library, the folder tree and the recycle bin. */
export function NavPane({
  folders,
  place,
  go,
  onDropKeys,
  binCount,
}: {
  folders: Folder[];
  place: Place;
  go: (p: Place) => void;
  onDropKeys: (keys: string[], to: number | null) => void;
  binCount: number | null;
}) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [overTop, setOverTop] = useState(false);
  const current = place.kind === "folder" ? place.id : null;

  // The folder being looked at is always shown: open everything above it.
  useEffect(() => {
    if (current === null) return;
    const above = pathTo(folders, current).map((f) => f.id);
    setOpen((o) => (above.every((id) => o.has(id)) ? o : new Set([...o, ...above])));
  }, [current, folders]);

  const toggle = (id: number) =>
    setOpen((o) => {
      const next = new Set(o);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const top = (kind: "home" | "all" | "bin", icon: ReactNode, label: string, count?: number | null) => (
    <div
      className={`nav-item ${place.kind === kind ? "here" : ""} ${kind === "all" && overTop ? "drop-over" : ""}`}
      {...(kind === "all" ? dropProps((keys) => onDropKeys(keys, null), setOverTop) : {})}
    >
      <span className="nav-twist" aria-hidden />
      <button type="button" className="nav-go" aria-current={place.kind === kind ? "page" : undefined} onClick={() => go({ kind })}>
        {icon}
        <span className="nav-name">{label}</span>
        {!!count && <span className="nav-count">{count}</span>}
      </button>
    </div>
  );

  return (
    <nav className="nav-pane" aria-label="Library places">
      {top("home", <IconHome width={16} height={16} />, "Home")}
      {top("all", <IconLibrary width={16} height={16} />, "All documents")}
      {folders.length > 0 && (
        <ul className="nav-tree">
          {childrenOf(folders, null).map((f) => (
            <TreeNode key={f.id} folder={f} folders={folders} place={place} open={open} toggle={toggle} go={go} onDropKeys={onDropKeys} depth={0} />
          ))}
        </ul>
      )}
      <div className="nav-gap" />
      {top("bin", <IconTrash width={16} height={16} />, "Recycle bin", binCount)}
    </nav>
  );
}

// ---------------------------------------------------------------- details and preview pane

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  );
}

/** The right pane: what the chosen thing is (details), or the start of what it says (preview). */
export function SidePane({
  mode,
  docs,
  folders,
  allFolders,
  onClose,
  place,
}: {
  mode: "details" | "preview";
  docs: Doc[];
  folders: Folder[];
  allFolders: Folder[];
  onClose: () => void;
  place: string;
}) {
  const one = docs.length === 1 && folders.length === 0 ? docs[0] : null;
  const folder = folders.length === 1 && docs.length === 0 ? folders[0] : null;
  const [preview, setPreview] = useState<{ id: number; text: string | null; failed?: boolean } | null>(null);

  // The preview is read a moment after the choice settles, so moving down a list with the
  // arrow keys doesn't open (and log a view of) every document on the way.
  useEffect(() => {
    if (mode !== "preview" || !one || !one.access.allowed) return;
    const id = one.id;
    const t = setTimeout(() => {
      api<DocDetail>(`/api/documents/${id}`)
        .then((d) => setPreview({ id, text: d.preview }))
        .catch(() => setPreview({ id, text: null, failed: true }));
    }, 350);
    return () => clearTimeout(t);
  }, [mode, one]);

  let body: ReactNode;
  if (one) {
    const where = pathTo(allFolders, one.folder_id).map((f) => f.name).join(" › ") || "Top of the library";
    body = (
      <>
        <div className="pane-title">
          <span className="doc-icon">{one.access.allowed ? <IconFile /> : <IconLock />}</span>
          <h3>{one.title}</h3>
        </div>
        {mode === "preview" ? (
          !one.access.allowed ? (
            <p className="muted">{one.access.reason}</p>
          ) : preview?.id !== one.id ? (
            <Skeleton rows={3} />
          ) : preview.failed ? (
            <p className="muted">The preview didn't load.</p>
          ) : (
            <pre className="pane-preview" tabIndex={0} aria-label={`Start of ${one.title}`}>
              {preview.text || "This document has no text to show."}
            </pre>
          )
        ) : (
          <>
            {one.access.allowed && one.summary && <p className="pane-summary">{one.summary}</p>}
            <dl className="pane-facts">
              <Fact label="Type">{typeName(one.filename)}</Fact>
              <Fact label="Size">{fileSize(one.size)}</Fact>
              <Fact label="Classification">
                <ClassTag value={one.classification} />
              </Fact>
              <Fact label="Team">
                <TeamChip team={one.owner_team} />
              </Fact>
              {one.min_position > 1 && <Fact label="Who can open">{one.min_position_label} and above</Fact>}
              {one.shared_with.length > 0 && <Fact label="Shared with">{one.shared_with.join(", ")}</Fact>}
              <Fact label="Folder">{where}</Fact>
              <Fact label="Modified">
                {shortDate(one.updated_at)} ({timeAgo(one.updated_at)})
              </Fact>
              <Fact label="Added">
                {shortDate(one.created_at)}
                {one.uploaded_by ? ` by ${one.uploaded_by}` : ""}
              </Fact>
              <Fact label="Version">{one.version}</Fact>
              {one.source && <Fact label="Synced from">{one.source.name}</Fact>}
              {one.access.allowed && one.tags.length > 0 && <Fact label="Tags">{one.tags.join(", ")}</Fact>}
              <Fact label="File name">{one.filename}</Fact>
            </dl>
            {!one.access.allowed && <p className="muted small">{one.access.reason}</p>}
          </>
        )}
        <Link className="btn small block" to={`/library/${one.id}`}>
          Open
        </Link>
      </>
    );
  } else if (folder) {
    const inside = childrenOf(allFolders, folder.id).length;
    body = (
      <>
        <div className="pane-title">
          <span className="doc-icon folder">
            <IconFolder />
          </span>
          <h3>{folder.name}</h3>
        </div>
        <dl className="pane-facts">
          <Fact label="Type">Folder</Fact>
          <Fact label="Holds">
            {folder.documents} {folder.documents === 1 ? "document" : "documents"}
            {inside > 0 ? `, ${inside} ${inside === 1 ? "folder" : "folders"}` : ""}
          </Fact>
          <Fact label="Inside">{pathTo(allFolders, folder.parent_id).map((f) => f.name).join(" › ") || "Top of the library"}</Fact>
          <Fact label="You can">{folder.can_edit ? "Rename, move and remove it" : "Open it"}</Fact>
        </dl>
        <p className="muted small">A folder only arranges documents. Who can open each one is set on the document.</p>
      </>
    );
  } else if (docs.length + folders.length > 1) {
    body = (
      <>
        <div className="pane-title">
          <h3>{docs.length + folders.length} items selected</h3>
        </div>
        <dl className="pane-facts">
          {docs.length > 0 && <Fact label="Documents">{docs.length}</Fact>}
          {folders.length > 0 && <Fact label="Folders">{folders.length}</Fact>}
          {docs.length > 0 && <Fact label="Total size">{fileSize(docs.reduce((n, d) => n + d.size, 0))}</Fact>}
        </dl>
      </>
    );
  } else {
    body = (
      <p className="muted pane-empty">
        Select a document or folder to see {mode === "preview" ? "the start of what it says" : "its details"}. In {place}.
      </p>
    );
  }

  return (
    <aside className="side-pane" aria-label={mode === "preview" ? "Preview" : "Details"}>
      <div className="pane-head">
        <strong>{mode === "preview" ? "Preview" : "Details"}</strong>
        <button type="button" className="icon-btn" onClick={onClose} aria-label={`Close the ${mode} pane`}>
          ×
        </button>
      </div>
      {body}
    </aside>
  );
}

// ---------------------------------------------------------------- dialogs

/** Ask for a name: a new folder's, or a new one for a folder or document. */
export function NameDialog({
  title,
  label,
  initial,
  action,
  onClose,
  onSubmit,
}: {
  title: string;
  label: string;
  initial: string;
  action: string;
  onClose: () => void;
  onSubmit: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState(initial);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.select(), []);
  return (
    <Modal title={title} onClose={onClose}>
      <form
        className="stack"
        noValidate
        onSubmit={async (e) => {
          e.preventDefault();
          if (!name.trim()) return setError("Type a name.");
          setBusy(true);
          setError("");
          try {
            await onSubmit(name.trim());
          } catch (err) {
            setError(err instanceof ApiError ? err.message : "That didn't work. Check your connection and try again.");
            setBusy(false);
          }
        }}
      >
        <label className="field">
          <span>{label}</span>
          <input ref={input} value={name} onChange={(e) => setName(e.target.value)} maxLength={255} autoComplete="off" />
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy}>
            {action}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** Choose where to move things: the top of the library or any folder that isn't one of them. */
export function MoveDialog({
  count,
  folders,
  moving,
  from,
  onClose,
  onMove,
}: {
  count: number;
  folders: Folder[];
  moving: number[]; // folders being moved: they can't go inside themselves
  from: number | null;
  onClose: () => void;
  onMove: (to: number | null) => Promise<void>;
}) {
  const [to, setTo] = useState<number | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const banned = new Set<number>();
  for (const id of moving) for (const inner of within(folders, id)) banned.add(inner);

  const rows: ReactNode[] = [];
  const walk = (parent: number | null, depth: number) => {
    for (const f of childrenOf(folders, parent)) {
      rows.push(
        <label key={f.id} className={`move-row ${banned.has(f.id) ? "off" : ""}`} style={{ "--depth": depth } as CSSProperties}>
          <input type="radio" name="move-to" checked={to === f.id} disabled={banned.has(f.id)} onChange={() => setTo(f.id)} />
          <IconFolder width={16} height={16} />
          <span>{f.name}</span>
          {f.id === from && <span className="muted small">(where it is now)</span>}
        </label>,
      );
      walk(f.id, depth + 1);
    }
  };
  walk(null, 1);

  return (
    <Modal title={`Move ${count} ${count === 1 ? "item" : "items"}`} onClose={onClose}>
      <form
        className="stack"
        onSubmit={async (e) => {
          e.preventDefault();
          if (to === undefined) return setError("Choose where to move to.");
          setBusy(true);
          try {
            await onMove(to);
          } catch (err) {
            setError(err instanceof ApiError ? err.message : "The move didn't work. Try again.");
            setBusy(false);
          }
        }}
      >
        <fieldset className="move-list">
          <legend>Move to</legend>
          <label className="move-row" style={{ "--depth": 0 } as CSSProperties}>
            <input type="radio" name="move-to" checked={to === null} onChange={() => setTo(null)} />
            <IconLibrary width={16} height={16} />
            <span>Top of the library</span>
            {from === null && <span className="muted small">(where it is now)</span>}
          </label>
          {rows}
        </fieldset>
        <p className="muted small">Moving changes where a document is found, never who can open it.</p>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy}>
            Move here
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function ConfirmDialog({
  title,
  children,
  action,
  danger,
  onClose,
  onConfirm,
}: {
  title: string;
  children: ReactNode;
  action: string;
  danger?: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <Modal title={title} onClose={onClose}>
      <div className="stack">
        <div>{children}</div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className={`btn ${danger ? "danger" : "primary"}`}
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
              } catch (err) {
                setError(err instanceof ApiError ? err.message : "That didn't work. Try again.");
                setBusy(false);
              }
            }}
          >
            {action}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- recycle bin

function daysLeft(iso: string): string {
  const days = Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);
  return days <= 1 ? "Removed for good within a day" : `Kept for ${days} more days`;
}

/** Deleted documents the person may restore. */
export function RecycleBin({ onChanged }: { onChanged: () => void }) {
  const { notify } = useApp();
  const [items, setItems] = useState<Deleted[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [confirm, setConfirm] = useState<Deleted | "all" | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = () => {
    setFailed(false);
    api<Deleted[]>("/api/library/bin")
      .then(setItems)
      .catch(() => {
        setItems([]);
        setFailed(true);
      });
  };
  useEffect(load, []);

  async function restore(item: Deleted) {
    setBusy(item.id);
    try {
      await api(`/api/library/bin/${item.id}/restore`, { method: "POST" });
      notify(`Restored “${item.title}”`);
      load();
      onChanged();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't restore it. Try again.", "error");
    } finally {
      setBusy(null);
    }
  }

  if (items === null) return <Skeleton rows={3} />;
  if (failed)
    return (
      <Empty title="The recycle bin didn't load." icon={<IconTrash />}>
        Check your connection, then{" "}
        <button className="link-btn" onClick={load}>
          <u>try again</u>
        </button>
        .
      </Empty>
    );
  if (items.length === 0)
    return (
      <Empty title="The recycle bin is empty" icon={<IconTrash />}>
        Documents you delete wait here for 30 days, so a mistake can be undone.
      </Empty>
    );

  return (
    <>
      <div className="bin-head">
        <p className="muted">
          Deleted documents are gone from search, the assistant and everyone's library. They can be restored from here for 30 days, with
          their sharing and earlier versions.
        </p>
        <button type="button" className="btn small danger" onClick={() => setConfirm("all")}>
          <IconTrash /> Empty the recycle bin
        </button>
      </div>
      <ul className="doc-list bin-list">
        {items.map((item) => (
          <li key={item.id} className={`bin-row class-edge-${item.classification}`}>
            <span className="doc-icon">
              <IconFile />
            </span>
            <div className="doc-main">
              <strong className="doc-title">{item.title}</strong>
              <div className="doc-meta">
                <ClassTag value={item.classification} />
                <TeamChip team={item.owner_team} />
                <span>{fileSize(item.size)}</span>
                {item.folder && <span>Was in {item.folder}</span>}
                <span>
                  Deleted {timeAgo(item.deleted_at)}
                  {item.deleted_by ? ` by ${item.deleted_by}` : ""}
                </span>
                <span>{daysLeft(item.expires_at)}</span>
              </div>
            </div>
            <div className="doc-side row">
              <button type="button" className="btn small" disabled={busy === item.id} onClick={() => restore(item)} aria-label={`Restore ${item.title}`}>
                <IconRestore /> Restore
              </button>
              <button type="button" className="btn small ghost" onClick={() => setConfirm(item)} aria-label={`Delete ${item.title} for good`}>
                Delete for good
              </button>
            </div>
          </li>
        ))}
      </ul>
      {confirm && (
        <ConfirmDialog
          title={confirm === "all" ? "Empty the recycle bin?" : `Delete “${confirm.title}” for good?`}
          action={confirm === "all" ? `Delete ${items.length} for good` : "Delete for good"}
          danger
          onClose={() => setConfirm(null)}
          onConfirm={async () => {
            await api(confirm === "all" ? "/api/library/bin" : `/api/library/bin/${confirm.id}`, { method: "DELETE" });
            notify(confirm === "all" ? "The recycle bin is empty" : `Deleted “${confirm.title}” for good`);
            setConfirm(null);
            load();
            onChanged();
          }}
        >
          <p>
            {confirm === "all" ? `These ${items.length} documents` : "This document"} and {confirm === "all" ? "their" : "its"} earlier versions
            can't be brought back after this.
          </p>
        </ConfirmDialog>
      )}
    </>
  );
}

/** A pin marker next to a title. */
export function Pinned() {
  return (
    <span className="pinned" title="Pinned">
      <IconPin width={13} height={13} />
      <span className="sr-only"> (pinned)</span>
    </span>
  );
}
