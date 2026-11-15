import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type DragEvent, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError, download, getToken } from "../api";
import { ClassTag, Empty, Highlight, RequestAccessDialog, Skeleton, TeamChip, timeAgo, usePageTitle } from "../components/bits";
import {
  IconArrowLeft,
  IconArrowUp,
  IconChevronRight,
  IconCloud,
  IconDownload,
  IconEye,
  IconFile,
  IconFolder,
  IconFolderMove,
  IconFolderPlus,
  IconGrid,
  IconInfo,
  IconLibrary,
  IconLink,
  IconLock,
  IconMore,
  IconPencil,
  IconPin,
  IconRows,
  IconSearch,
  IconSort,
  IconTrash,
  IconUpload,
} from "../components/icons";
import { ContextMenu, MenuButton, type MenuItem } from "../components/library/Menu";
import {
  ConfirmDialog,
  DRAG_TYPE,
  dropProps,
  MoveDialog,
  NameDialog,
  NavPane,
  Pinned,
  RecycleBin,
  SidePane,
  type Place,
} from "../components/library/Panels";
import { UploadDialog } from "../components/library/UploadDialog";
import {
  childrenOf,
  docKey,
  fileSize,
  folderKey,
  groupDocs,
  GROUPS,
  idOf,
  isDoc,
  pathTo,
  remember,
  remembered,
  select,
  shortDate,
  sortDocs,
  SORTS,
  typeName,
  VIEWS,
  type GroupKey,
  type Key,
  type Sort,
  type SortKey,
  type View,
} from "../components/library/explorer";
import { useApp } from "../state";
import type { Classification, Deleted, Doc, Folder, LibraryHome } from "../types";

const CLASS_COLOR: Record<Classification, string> = {
  public: "var(--c-public)",
  internal: "var(--c-internal)",
  confidential: "var(--c-confidential)",
  restricted: "var(--c-restricted)",
};

type Pane = "none" | "details" | "preview";
type Dialog =
  | { kind: "upload"; file?: File | null }
  | { kind: "new-folder" }
  | { kind: "rename"; key: Key }
  | { kind: "move"; keys: Key[] }
  | { kind: "delete"; keys: Key[] };

function placeOf(params: URLSearchParams): Place {
  const view = params.get("view");
  if (view === "home" || view === "bin") return { kind: view };
  const folder = Number(params.get("folder"));
  return params.get("folder") && Number.isInteger(folder) && folder > 0 ? { kind: "folder", id: folder } : { kind: "all" };
}

const interactive = (target: EventTarget | null) => !!(target as HTMLElement | null)?.closest("a, button, input, label, select, textarea");

export default function Library() {
  const { meta, notify } = useApp();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const place = useMemo(() => placeOf(params), [params]);
  const folderId = place.kind === "folder" ? place.id : null;

  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [home, setHome] = useState<LibraryHome | null>(null);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [binCount, setBinCount] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const [missing, setMissing] = useState(false); // the folder in the address isn't there (or isn't yours to see)
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
  const [requesting, setRequesting] = useState<Doc | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const [view, setView] = useState<View>(() => remembered("view", ["content", "details", "list", "tiles"] as const, "content"));
  const [sort, setSort] = useState<Sort>(() => ({
    key: remembered("sort", SORTS.map((s) => s.value), "default"),
    desc: remembered("dir", ["asc", "desc"] as const, "asc") === "desc",
  }));
  const [group, setGroup] = useState<GroupKey>(() => remembered("group", GROUPS.map((g) => g.value), "none"));
  const [pane, setPane] = useState<Pane>(() => remembered("pane", ["none", "details", "preview"] as const, "none"));
  const [selected, setSelected] = useState<Set<Key>>(new Set());
  const [cut, setCut] = useState<Set<Key>>(new Set());
  const anchor = useRef<Key | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; keys: Key[] } | null>(null);
  const [filesOver, setFilesOver] = useState(false);
  const [dropFolder, setDropFolder] = useState<number | "top" | null>(null);
  usePageTitle("Library");

  const browsing = place.kind === "all" || place.kind === "folder";

  // ---------------------------------------------------------------- loading

  const loadFolders = useCallback(() => {
    api<Folder[]>("/api/library/folders")
      .then(setFolders)
      .catch(() => {});
    api<Deleted[]>("/api/library/bin")
      .then((items) => setBinCount(items.length))
      .catch(() => {});
  }, []);
  useEffect(loadFolders, [loadFolders]);

  // Team, owner and date are filtered by the server; classification and source are filtered
  // here so the chips can show counts for every level and the source list stays complete.
  const load = useCallback(() => {
    const n = ++latest.current;
    setFailed(false);
    setMissing(false);
    if (place.kind === "bin") return;
    if (place.kind === "home") {
      api<LibraryHome>("/api/library/home")
        .then((h) => n === latest.current && setHome(h))
        .catch(() => n === latest.current && setFailed(true));
      return;
    }
    const p = new URLSearchParams({ include_locked: String(showLocked) });
    if (q.trim()) p.set("q", q.trim());
    if (team) p.set("team_id", team);
    if (owner) p.set("owner_id", owner);
    if (recent) p.set("updated_within_days", recent);
    if (folderId !== null) p.set("folder_id", String(folderId));
    api<Doc[]>(`/api/documents?${p}`)
      .then((list) => {
        if (n !== latest.current) return;
        for (const d of list) if (d.uploaded_by_id && d.uploaded_by) owners.current.set(d.uploaded_by_id, d.uploaded_by);
        setDocs(list);
      })
      .catch((err) => {
        if (n !== latest.current) return;
        setDocs([]);
        if (err instanceof ApiError && err.status === 404 && folderId !== null) setMissing(true);
        else setFailed(true);
      });
  }, [q, team, owner, recent, showLocked, place.kind, folderId]);

  useEffect(() => {
    const t = setTimeout(load, q ? 220 : 0);
    return () => clearTimeout(t);
  }, [load, q]);

  const reload = useCallback(() => {
    load();
    loadFolders();
  }, [load, loadFolders]);

  // Filters live in the address, next to where the library is looking.
  useEffect(() => {
    setParams(
      (prev) => {
        const next = new URLSearchParams();
        for (const k of ["view", "folder"]) if (prev.get(k)) next.set(k, prev.get(k)!);
        if (q.trim()) next.set("q", q.trim());
        if (team) next.set("team", team);
        if (src) next.set("source", src);
        if (owner) next.set("owner", owner);
        if (recent) next.set("updated", recent);
        return next.toString() === prev.toString() ? prev : next;
      },
      { replace: true },
    );
  }, [q, team, src, owner, recent, setParams]);

  /** Go somewhere else in the library. Like a file manager, this starts a fresh look: the search is cleared. */
  const go = useCallback(
    (to: Place) => {
      const next = new URLSearchParams();
      if (to.kind === "folder") next.set("folder", String(to.id));
      else if (to.kind !== "all") next.set("view", to.kind);
      setQ("");
      setCls("");
      setSelected(new Set());
      setDocs(null);
      setHome(null);
      setParams(next);
    },
    [setParams],
  );

  // ---------------------------------------------------------------- what is shown

  const sources = [...new Map((docs ?? []).filter((d) => d.source).map((d) => [d.source!.id, d.source!])).values()];
  const fromSource = (d: Doc) => !src || (src === "uploaded" ? !d.source : String(d.source?.id) === src);
  const filtered = useMemo(
    () => (browsing ? (docs?.filter((d) => (!cls || d.classification === cls) && fromSource(d)) ?? null) : null),
    [docs, cls, src, browsing],
  );
  const visible = useMemo(() => (filtered ? sortDocs(filtered, sort) : null), [filtered, sort]);
  const groups = useMemo(() => (visible ? groupDocs(visible, group) : []), [visible, group]);
  const count = (c: Classification) => docs?.filter((d) => d.classification === c).length ?? 0;
  const openable = visible?.filter((d) => d.access.allowed).length ?? 0;
  const filtering = !!(q || cls || team || src || owner || recent);

  const path = pathTo(folders, folderId);
  const here = path[path.length - 1] ?? null;
  // Folders are listed where you are, unless a search or filter has narrowed things to documents.
  const subfolders = useMemo(
    () => (browsing && !filtering ? childrenOf(folders, folderId) : place.kind === "home" ? (home?.pinned_folders ?? []) : []),
    [browsing, filtering, folders, folderId, place.kind, home],
  );
  const homeDocs = useMemo(() => {
    if (place.kind !== "home" || !home) return [];
    const seen = new Set<number>();
    return [...home.pinned_documents, ...home.recent].filter((d) => !seen.has(d.id) && seen.add(d.id));
  }, [place.kind, home]);
  const shownDocs: Doc[] = browsing ? groups.flatMap((g) => g.docs) : homeDocs;
  const order: Key[] = useMemo(
    () => [...subfolders.map((f) => folderKey(f.id)), ...shownDocs.map((d) => docKey(d.id))],
    [subfolders, visible, group, homeDocs],
  );
  const docById = useMemo(() => new Map(shownDocs.map((d) => [d.id, d])), [shownDocs]);
  const folderById = useMemo(() => new Map(folders.map((f) => [f.id, f])), [folders]);

  // What is selected must still be on the page.
  useEffect(() => {
    setSelected((s) => {
      const kept = [...s].filter((k) => order.includes(k));
      return kept.length === s.size ? s : new Set(kept);
    });
  }, [order]);

  const selDocs = [...selected].filter(isDoc).map((k) => docById.get(idOf(k))).filter((d): d is Doc => !!d);
  const selFolders = [...selected].filter((k) => !isDoc(k)).map((k) => folderById.get(idOf(k))).filter((f): f is Folder => !!f);

  // ---------------------------------------------------------------- what can be done

  const docsOf = (keys: Key[]) => keys.filter(isDoc).map((k) => docById.get(idOf(k))).filter((d): d is Doc => !!d);
  const foldersOf = (keys: Key[]) => keys.filter((k) => !isDoc(k)).map((k) => folderById.get(idOf(k))).filter((f): f is Folder => !!f);
  const movable = (keys: Key[]) => [
    ...docsOf(keys).filter((d) => d.access.can_manage).map((d) => docKey(d.id)),
    ...foldersOf(keys).filter((f) => f.can_edit).map((f) => folderKey(f.id)),
  ];
  const deletable = (keys: Key[]) => [
    ...docsOf(keys).filter((d) => d.access.can_manage && !d.source).map((d) => docKey(d.id)),
    ...foldersOf(keys).filter((f) => f.can_edit).map((f) => folderKey(f.id)),
  ];
  const renamable = (keys: Key[]) =>
    keys.length === 1 && (isDoc(keys[0]) ? !!docById.get(idOf(keys[0]))?.access.can_manage : !!folderById.get(idOf(keys[0]))?.can_edit);
  const downloadable = (keys: Key[]) => docsOf(keys).filter((d) => d.access.allowed);

  function open(key: Key) {
    if (isDoc(key)) navigate(`/library/${idOf(key)}`);
    else go({ kind: "folder", id: idOf(key) });
  }

  async function moveTo(keys: Key[], to: number | null) {
    const body = { document_ids: keys.filter(isDoc).map(idOf), folder_ids: keys.filter((k) => !isDoc(k)).map(idOf), to };
    if (body.folder_ids.includes(to ?? -1)) return;
    try {
      const out = await api<{ moved: number; skipped: { title: string; reason: string }[] }>("/api/library/move", { method: "POST", json: body });
      const where = to === null ? "the top of the library" : (folderById.get(to)?.name ?? "the folder");
      if (out.moved) notify(`Moved ${out.moved} ${out.moved === 1 ? "item" : "items"} to ${where}`);
      if (out.skipped.length)
        notify(
          out.skipped.length === 1 && out.skipped[0].title
            ? `“${out.skipped[0].title}” stayed where it is. ${out.skipped[0].reason}.`
            : `${out.skipped.length} stayed where they are: you can't move them.`,
          "error",
        );
      setCut(new Set());
      reload();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "The move didn't work. Try again.", "error");
    }
  }

  async function togglePin(keys: Key[]) {
    const allPinned = keys.every((k) => (isDoc(k) ? docById.get(idOf(k))?.pinned : folderById.get(idOf(k))?.pinned));
    try {
      for (const k of keys) {
        const field = isDoc(k) ? "document_id" : "folder_id";
        if (allPinned) await api(`/api/library/pins?${field}=${idOf(k)}`, { method: "DELETE" });
        else await api("/api/library/pins", { method: "PUT", json: { [field]: idOf(k) } });
      }
      notify(allPinned ? "Unpinned" : `Pinned to Home${keys.length > 1 ? ` (${keys.length})` : ""}`);
      reload();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't change the pin. Try again.", "error");
    }
  }

  async function downloadKeys(keys: Key[]) {
    const files = downloadable(keys);
    if (!files.length) return;
    try {
      if (files.length === 1) return await download(files[0].id, files[0].filename);
      const res = await fetch("/api/library/zip", {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken() ?? ""}`, "Content-Type": "application/json" },
        body: JSON.stringify({ ids: files.map((d) => d.id) }),
      });
      if (!res.ok) {
        let message = "The download didn't work. Try again.";
        try {
          message = (await res.json()).detail ?? message;
        } catch {
          /* not JSON */
        }
        throw new ApiError(res.status, String(message), null);
      }
      const url = URL.createObjectURL(await res.blob());
      Object.assign(document.createElement("a"), { href: url, download: "VaultMind documents.zip" }).click();
      URL.revokeObjectURL(url);
      notify(`Downloading ${files.length} documents as a ZIP`);
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "The download didn't work. Try again.", "error");
    }
  }

  async function copyLink(key: Key) {
    const url = `${location.origin}/library${isDoc(key) ? `/${idOf(key)}` : `?folder=${idOf(key)}`}`;
    try {
      await navigator.clipboard.writeText(url);
      notify("Link copied. It opens only for people who may see it.");
    } catch {
      notify(url);
    }
  }

  function selectAll() {
    setSelected(new Set(order));
  }

  function actionsFor(keys: Key[]): MenuItem[] {
    const one = keys.length === 1 ? keys[0] : null;
    const pinnedAll = keys.every((k) => (isDoc(k) ? docById.get(idOf(k))?.pinned : folderById.get(idOf(k))?.pinned));
    const files = downloadable(keys);
    const only = one && isDoc(one) ? docById.get(idOf(one)) : undefined;
    const lockedOne = only && !only.access.allowed && !only.access.request_pending ? only : null;
    return [
      { label: "Open", icon: <IconArrowUp style={{ rotate: "45deg" }} />, hint: "Enter", disabled: !one, onSelect: () => one && open(one) },
      {
        label: "Open in a new tab",
        icon: <IconGrid />,
        disabled: !one,
        onSelect: () => one && window.open(isDoc(one) ? `/library/${idOf(one)}` : `/library?folder=${idOf(one)}`, "_blank", "noopener"),
      },
      { separator: true },
      { label: "Rename", icon: <IconPencil />, hint: "F2", disabled: !renamable(keys), onSelect: () => one && setDialog({ kind: "rename", key: one }) },
      { label: "Move to…", icon: <IconFolderMove />, disabled: !movable(keys).length, onSelect: () => setDialog({ kind: "move", keys: movable(keys) }) },
      { label: "Cut", icon: <IconFolderMove />, hint: "Ctrl+X", disabled: !movable(keys).length, onSelect: () => setCut(new Set(movable(keys))) },
      { label: pinnedAll ? "Unpin from Home" : "Pin to Home", icon: <IconPin />, onSelect: () => togglePin(keys) },
      { separator: true },
      {
        label: files.length > 1 ? `Download ${files.length} as a ZIP` : "Download",
        icon: <IconDownload />,
        disabled: !files.length,
        onSelect: () => downloadKeys(keys),
      },
      { label: "Copy link", icon: <IconLink />, disabled: !one, onSelect: () => one && copyLink(one) },
      ...(lockedOne ? [{ label: "Request access", icon: <IconLock />, onSelect: () => setRequesting(lockedOne) }] : []),
      { label: "Details", icon: <IconInfo />, hint: "Alt+Enter", onSelect: () => choosePane("details") },
      { separator: true },
      {
        label: keys.every((k) => !isDoc(k)) ? "Remove folder" : "Delete",
        icon: <IconTrash />,
        hint: "Del",
        danger: true,
        disabled: !deletable(keys).length,
        onSelect: () => setDialog({ kind: "delete", keys: deletable(keys) }),
      },
    ];
  }

  const backgroundMenu = (): MenuItem[] => [
    { label: "New folder", icon: <IconFolderPlus />, disabled: !browsing, onSelect: () => setDialog({ kind: "new-folder" }) },
    { label: "Upload a document", icon: <IconUpload />, onSelect: () => setDialog({ kind: "upload" }) },
    { label: `Paste ${cut.size || ""}`.trim(), icon: <IconFolderMove />, hint: "Ctrl+V", disabled: !cut.size || !browsing, onSelect: () => moveTo([...cut], folderId) },
    { separator: true },
    { label: "Select all", hint: "Ctrl+A", disabled: !order.length, onSelect: selectAll },
    { label: "Refresh", onSelect: reload },
  ];

  function choosePane(next: Pane) {
    setPane(next);
    remember("pane", next);
  }
  function chooseView(next: View) {
    setView(next);
    remember("view", next);
  }
  function chooseSort(key: SortKey) {
    setSort((s) => {
      const next = { key, desc: s.key === key ? !s.desc : key === "modified" || key === "size" };
      remember("sort", next.key);
      remember("dir", next.desc ? "desc" : "asc");
      return next;
    });
  }

  // ---------------------------------------------------------------- pointer and keyboard

  function pick(key: Key, e: { ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean }, toggle = false) {
    setSelected((s) => select(s, order, key, anchor.current, { toggle: toggle || e.ctrlKey || e.metaKey, range: e.shiftKey }));
    if (!e.shiftKey) anchor.current = key;
  }

  function rowProps(key: Key, canDrag: boolean) {
    return {
      "data-key": key,
      draggable: canDrag,
      onClick: (e: React.MouseEvent) => {
        if (!interactive(e.target)) pick(key, e);
      },
      onDoubleClick: (e: React.MouseEvent) => {
        if (!interactive(e.target)) open(key);
      },
      onContextMenu: (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        const keys = selected.has(key) ? [...selected] : [key];
        if (!selected.has(key)) {
          setSelected(new Set([key]));
          anchor.current = key;
        }
        setMenu({ x: e.clientX, y: e.clientY, keys });
      },
      onDragStart: (e: DragEvent) => {
        const keys = selected.has(key) ? movable([...selected]) : movable([key]);
        if (!keys.length) return e.preventDefault();
        e.dataTransfer.setData(DRAG_TYPE, keys.join(","));
        e.dataTransfer.effectAllowed = "move";
      },
    };
  }

  function moreButton(key: Key, name: string) {
    return (
      <button
        type="button"
        className="icon-btn row-more"
        aria-label={`More actions for ${name}`}
        aria-haspopup="menu"
        onClick={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const keys = selected.has(key) && selected.size > 1 ? [...selected] : [key];
          if (!selected.has(key)) setSelected(new Set([key]));
          setMenu({ x: box.right - 4, y: box.bottom + 4, keys });
        }}
      >
        <IconMore />
      </button>
    );
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing = !!target?.closest("input:not([type=checkbox]), textarea, select, [contenteditable]");
      if (document.querySelector("dialog[open]") || document.querySelector(".menu")) return;
      // "/" focuses search, like most document tools
      if (e.key === "/" && !typing) {
        e.preventDefault();
        return searchRef.current?.focus();
      }
      if (typing || place.kind === "bin") return;
      const keys = [...selected];
      const mod = e.ctrlKey || e.metaKey;
      const row = target?.closest<HTMLElement>("[data-key]");
      if (mod && e.key.toLowerCase() === "a") {
        e.preventDefault();
        selectAll();
      } else if (mod && e.key.toLowerCase() === "x" && movable(keys).length) {
        e.preventDefault();
        setCut(new Set(movable(keys)));
        notify(`Cut ${movable(keys).length}. Open a folder and press Ctrl+V to move ${movable(keys).length === 1 ? "it" : "them"} there.`);
      } else if (mod && e.key.toLowerCase() === "v" && cut.size && browsing) {
        e.preventDefault();
        void moveTo([...cut], folderId);
      } else if (e.key === "Delete" && deletable(keys).length) {
        e.preventDefault();
        setDialog({ kind: "delete", keys: deletable(keys) });
      } else if (e.key === "F2" && renamable(keys)) {
        e.preventDefault();
        setDialog({ kind: "rename", key: keys[0] });
      } else if (e.altKey && e.key === "Enter" && keys.length) {
        e.preventDefault();
        choosePane("details");
      } else if (e.altKey && e.key.toLowerCase() === "p") {
        e.preventDefault();
        choosePane(e.shiftKey ? (pane === "details" ? "none" : "details") : pane === "preview" ? "none" : "preview");
      } else if ((e.altKey && e.key === "ArrowUp") || (e.key === "Backspace" && !mod)) {
        if (place.kind === "folder") {
          e.preventDefault();
          go(here?.parent_id ? { kind: "folder", id: here.parent_id } : { kind: "all" });
        }
      } else if (e.key === "Escape" && (selected.size || cut.size)) {
        setSelected(new Set());
        setCut(new Set());
      } else if (row && target instanceof HTMLInputElement && target.type === "checkbox") {
        // On a row's checkbox: Enter opens it, the arrows walk the list (Shift extends), and
        // the menu key or Shift+F10 opens its menu.
        const key = row.dataset.key!;
        const at = order.indexOf(key);
        if (e.key === "Enter") {
          e.preventDefault();
          open(key);
        } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          const next = order[at + (e.key === "ArrowDown" ? 1 : -1)];
          if (!next) return;
          e.preventDefault();
          document.querySelector<HTMLInputElement>(`[data-key="${next}"] input[type=checkbox]`)?.focus();
          if (e.shiftKey) setSelected((s) => select(s, order, next, anchor.current ?? key, { range: true }));
        } else if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
          e.preventDefault();
          const box = row.getBoundingClientRect();
          if (!selected.has(key)) setSelected(new Set([key]));
          setMenu({ x: box.left + 40, y: box.top + 30, keys: selected.has(key) ? keys : [key] });
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // Files dragged in from the computer are uploaded here.
  const fileDrop = {
    onDragOver: (e: DragEvent) => {
      if (!e.dataTransfer.types.includes("Files") || !browsing) return;
      e.preventDefault();
      setFilesOver(true);
    },
    onDragLeave: (e: DragEvent) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node)) setFilesOver(false);
    },
    onDrop: (e: DragEvent) => {
      if (!e.dataTransfer.types.includes("Files")) return;
      e.preventDefault();
      setFilesOver(false);
      const files = e.dataTransfer.files;
      if (files.length > 1) notify("One file at a time: each is checked and classified before it's uploaded.", "error");
      if (files[0]) setDialog({ kind: "upload", file: files[0] });
    },
  };

  // ---------------------------------------------------------------- rows

  function docRow(d: Doc, i: number): ReactNode {
    const key = docKey(d.id);
    return (
      <li
        key={key}
        className={`doc-row class-edge-${d.classification} ${d.access.allowed ? "" : "locked"} ${selected.has(key) ? "selected" : ""} ${cut.has(key) ? "cut" : ""}`}
        style={{ "--i": Math.min(i, 12) } as CSSProperties}
        {...rowProps(key, d.access.can_manage)}
      >
        <label className="row-check">
          <input
            type="checkbox"
            checked={selected.has(key)}
            onChange={(e) => pick(key, { shiftKey: (e.nativeEvent as MouseEvent).shiftKey }, true)}
            aria-label={`Select ${d.title}`}
          />
        </label>
        <span className="doc-icon">{d.access.allowed ? <IconFile /> : <IconLock />}</span>
        <div className="doc-main">
          <span className="doc-name">
            <Link to={`/library/${d.id}`} className="doc-title">
              <Highlight text={d.title} query={q} />
              {!d.access.allowed && <span className="sr-only"> (locked)</span>}
            </Link>
            {d.pinned && <Pinned />}
          </span>
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
        <span className="col col-date">{shortDate(d.updated_at)}</span>
        <span className="col col-type">{typeName(d.filename)}</span>
        <span className="col col-size">{fileSize(d.size)}</span>
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
          {moreButton(key, d.title)}
        </div>
      </li>
    );
  }

  function folderTile(f: Folder): ReactNode {
    const key = folderKey(f.id);
    return (
      <li
        key={key}
        className={`folder-tile ${selected.has(key) ? "selected" : ""} ${cut.has(key) ? "cut" : ""} ${dropFolder === f.id ? "drop-over" : ""}`}
        {...rowProps(key, f.can_edit)}
        {...dropProps(
          (keys) => moveTo(keys, f.id),
          (over) => setDropFolder(over ? f.id : null),
        )}
      >
        <label className="row-check">
          <input
            type="checkbox"
            checked={selected.has(key)}
            onChange={(e) => pick(key, { shiftKey: (e.nativeEvent as MouseEvent).shiftKey }, true)}
            aria-label={`Select the folder ${f.name}`}
          />
        </label>
        <button type="button" className="folder-open" onClick={() => go({ kind: "folder", id: f.id })}>
          <IconFolder />
          <span className="folder-name">{f.name}</span>
          {f.pinned && <Pinned />}
          <span className="folder-count">
            {f.documents} {f.documents === 1 ? "document" : "documents"}
          </span>
        </button>
        {moreButton(key, `the folder ${f.name}`)}
      </li>
    );
  }

  const selectedSize = selDocs.reduce((n, d) => n + d.size, 0);
  const placeName = place.kind === "home" ? "Home" : place.kind === "bin" ? "Recycle bin" : (here?.name ?? "All documents");
  const selKeys = [...selected];
  const dialogDoc = dialog?.kind === "rename" && isDoc(dialog.key) ? docById.get(idOf(dialog.key)) : null;
  const dialogFolder = dialog?.kind === "rename" && !isDoc(dialog.key) ? folderById.get(idOf(dialog.key)) : null;
  let rowIndex = 0;

  return (
    <div className="page explorer">
      <header className="page-head">
        <div>
          <h1>Library</h1>
          <p className="muted">
            {place.kind === "bin"
              ? "Deleted documents, kept for 30 days"
              : place.kind === "home"
                ? "What you pinned and opened lately"
                : visible
                  ? `${openable} you can open${showLocked ? `, ${visible.length - openable} you can request` : ""}`
                  : "Loading documents…"}
          </p>
        </div>
        <div className="row">
          {browsing && (
            <button className="btn" onClick={() => setDialog({ kind: "new-folder" })}>
              <IconFolderPlus /> New folder
            </button>
          )}
          <button className="btn primary" onClick={() => setDialog({ kind: "upload" })}>
            <IconUpload /> Upload a document
          </button>
        </div>
      </header>

      <div className="address-bar">
        <button type="button" className="tool" onClick={() => navigate(-1)} aria-label="Back" title="Back">
          <IconArrowLeft />
        </button>
        <button
          type="button"
          className="tool"
          disabled={place.kind !== "folder"}
          onClick={() => go(here?.parent_id ? { kind: "folder", id: here.parent_id } : { kind: "all" })}
          aria-label="Up one folder"
          title="Up one folder (Alt+Up)"
        >
          <IconArrowUp />
        </button>
        <nav className="crumbs" aria-label="Where you are">
          <ol>
            <li>
              <button
                type="button"
                className={`crumb ${dropFolder === "top" ? "drop-over" : ""}`}
                aria-current={place.kind === "all" ? "page" : undefined}
                onClick={() => go({ kind: "all" })}
                {...dropProps(
                  (keys) => moveTo(keys, null),
                  (over) => setDropFolder(over ? "top" : null),
                )}
              >
                <IconLibrary width={15} height={15} /> All documents
              </button>
            </li>
            {place.kind === "folder" &&
              path.map((f, i) => (
                <li key={f.id}>
                  <IconChevronRight width={13} height={13} />
                  <button
                    type="button"
                    className="crumb"
                    aria-current={i === path.length - 1 ? "page" : undefined}
                    onClick={() => go({ kind: "folder", id: f.id })}
                    {...dropProps(
                      (keys) => moveTo(keys, f.id),
                      () => {},
                    )}
                  >
                    {f.name}
                  </button>
                </li>
              ))}
            {(place.kind === "home" || place.kind === "bin") && (
              <li>
                <IconChevronRight width={13} height={13} />
                <span className="crumb" aria-current="page">
                  {placeName}
                </span>
              </li>
            )}
          </ol>
        </nav>
        {browsing && (
          <label className="search-box">
            <IconSearch />
            <input
              ref={searchRef}
              type="search"
              placeholder={here ? `Search ${here.name} and the folders in it` : "Search titles and contents"}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label="Search documents"
            />
            {!q && <span className="kbd" aria-hidden>/</span>}
          </label>
        )}
      </div>

      {place.kind !== "bin" && (
        <div className="command-bar" role="toolbar" aria-label="Actions">
          <button type="button" className="tool" disabled={!renamable(selKeys)} onClick={() => setDialog({ kind: "rename", key: selKeys[0] })}>
            <IconPencil /> <span>Rename</span>
          </button>
          <button
            type="button"
            className="tool"
            disabled={!movable(selKeys).length}
            onClick={() => setDialog({ kind: "move", keys: movable(selKeys) })}
          >
            <IconFolderMove /> <span>Move to</span>
          </button>
          <button type="button" className="tool" disabled={!selKeys.length} onClick={() => togglePin(selKeys)}>
            <IconPin /> <span>{selKeys.length && selKeys.every((k) => (isDoc(k) ? docById.get(idOf(k))?.pinned : folderById.get(idOf(k))?.pinned)) ? "Unpin" : "Pin"}</span>
          </button>
          <button type="button" className="tool" disabled={!downloadable(selKeys).length} onClick={() => downloadKeys(selKeys)}>
            <IconDownload /> <span>{downloadable(selKeys).length > 1 ? "Download ZIP" : "Download"}</span>
          </button>
          <button
            type="button"
            className="tool danger"
            disabled={!deletable(selKeys).length}
            onClick={() => setDialog({ kind: "delete", keys: deletable(selKeys) })}
          >
            <IconTrash /> <span>Delete</span>
          </button>
          {cut.size > 0 && browsing && (
            <button type="button" className="tool" onClick={() => moveTo([...cut], folderId)}>
              <IconFolderMove /> <span>Paste {cut.size} here</span>
            </button>
          )}
          <span className="tool-gap" />
          {browsing && (
            <>
              <MenuButton
                label="Sort and group"
                icon={<IconSort />}
                text={<span>Sort</span>}
                items={[
                  ...SORTS.map((s) => ({
                    label: s.label + (sort.key === s.value && s.value !== "default" ? (sort.desc ? " ↓" : " ↑") : ""),
                    checked: sort.key === s.value,
                    onSelect: () => chooseSort(s.value),
                  })),
                  { separator: true },
                  ...GROUPS.map((g) => ({
                    label: g.value === "none" ? g.label : `Group by ${g.label.toLowerCase()}`,
                    checked: group === g.value,
                    onSelect: () => {
                      setGroup(g.value);
                      remember("group", g.value);
                    },
                  })),
                ]}
              />
              <MenuButton
                label="View"
                icon={view === "tiles" ? <IconGrid /> : <IconRows />}
                text={<span>View</span>}
                items={VIEWS.map((v) => ({ label: v.label, hint: v.hint, checked: view === v.value, onSelect: () => chooseView(v.value) }))}
              />
            </>
          )}
          <button
            type="button"
            className="tool"
            aria-pressed={pane === "details"}
            onClick={() => choosePane(pane === "details" ? "none" : "details")}
            title="Details pane (Alt+Shift+P)"
          >
            <IconInfo /> <span>Details</span>
          </button>
          <button
            type="button"
            className="tool"
            aria-pressed={pane === "preview"}
            onClick={() => choosePane(pane === "preview" ? "none" : "preview")}
            title="Preview pane (Alt+P)"
          >
            <IconEye /> <span>Preview</span>
          </button>
        </div>
      )}

      {browsing && (
        <>
          <div className="filters">
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
        </>
      )}

      <div className={`explorer-body ${pane !== "none" && place.kind !== "bin" ? "with-pane" : ""}`}>
        <NavPane folders={folders} place={place} go={go} onDropKeys={moveTo} binCount={binCount} />

        <section
          className={`explorer-main ${filesOver ? "files-over" : ""}`}
          aria-label={placeName}
          {...fileDrop}
          onContextMenu={(e) => {
            if (place.kind === "bin" || interactive(e.target)) return;
            e.preventDefault();
            setMenu({ x: e.clientX, y: e.clientY, keys: [] });
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) setSelected(new Set());
          }}
        >
          {place.kind === "bin" && <RecycleBin onChanged={reload} />}

          {place.kind !== "bin" && subfolders.length > 0 && (
            <>
              {place.kind === "home" && <h2 className="explorer-h">Pinned folders</h2>}
              <ul className="folder-strip" aria-label={place.kind === "home" ? "Pinned folders" : "Folders here"}>
                {subfolders.map(folderTile)}
              </ul>
            </>
          )}

          {browsing && visible === null && <Skeleton rows={5} />}
          {place.kind === "home" && home === null && !failed && <Skeleton rows={4} />}

          {failed && (
            <Empty title="The library didn't load." icon={<IconFile />}>
              Check your connection, then{" "}
              <button className="link-btn" onClick={load}>
                <u>try again</u>
              </button>
              .
            </Empty>
          )}

          {missing && (
            <Empty title="That folder isn't here" icon={<IconFolder />}>
              It was removed, or nothing in it is yours to see.{" "}
              <button className="link-btn" onClick={() => go({ kind: "all" })}>
                <u>Go to all documents</u>
              </button>
              .
            </Empty>
          )}

          {browsing && !failed && !missing && visible && visible.length === 0 && (filtering || subfolders.length === 0) && (
            <Empty title={q ? `Nothing matches “${q}”` : here ? "This folder is empty" : "No documents here yet"} icon={<IconSearch />}>
              {filtering ? (
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
              ) : here ? (
                "Upload a document here, or move some in: drag them onto the folder, or use Move to."
              ) : (
                "Upload the first one for your team."
              )}
            </Empty>
          )}

          {browsing && view === "details" && visible && visible.length > 0 && (
            <div className="details-head">
              <span />
              {(
                [
                  ["name", "Name"],
                  ["modified", "Date modified"],
                  ["type", "Type"],
                  ["size", "Size"],
                ] as [SortKey, string][]
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={`col-head col-${key}`}
                  onClick={() => chooseSort(key)}
                  aria-label={`Sort by ${label.toLowerCase()}${sort.key === key ? (sort.desc ? ", now descending" : ", now ascending") : ""}`}
                >
                  {label} {sort.key === key && <span aria-hidden>{sort.desc ? "↓" : "↑"}</span>}
                </button>
              ))}
              <span />
            </div>
          )}

          {browsing && (
            <ul className="doc-list" data-view={view}>
              {groups.flatMap((g) => [
                ...(g.label
                  ? [
                      <li key={`g:${g.label}`} className="doc-group">
                        <h2>
                          {g.label} <span className="chip-count">{g.docs.length}</span>
                        </h2>
                      </li>,
                    ]
                  : []),
                ...g.docs.map((d) => docRow(d, rowIndex++)),
              ])}
            </ul>
          )}

          {place.kind === "home" && home && (
            <>
              <h2 className="explorer-h">Pinned</h2>
              {home.pinned_documents.length === 0 && home.pinned_folders.length === 0 ? (
                <p className="muted home-hint">
                  Nothing pinned yet. Select a document or folder and choose Pin: it stays here, one click away.
                </p>
              ) : (
                <ul className="doc-list" data-view="content">
                  {home.pinned_documents.map((d, i) => docRow(d, i))}
                </ul>
              )}
              <h2 className="explorer-h">Recent</h2>
              {home.recent.filter((d) => !d.pinned).length === 0 ? (
                <p className="muted home-hint">Documents you open will be listed here.</p>
              ) : (
                <ul className="doc-list" data-view="content">
                  {home.recent.filter((d) => !d.pinned).map((d, i) => docRow(d, i))}
                </ul>
              )}
            </>
          )}

          {filesOver && (
            <div className="drop-hint" aria-hidden>
              <IconUpload /> Drop the file to upload it{here ? ` to ${here.name}` : ""}
            </div>
          )}
        </section>

        {pane !== "none" && place.kind !== "bin" && (
          <SidePane mode={pane} docs={selDocs} folders={selFolders} allFolders={folders} onClose={() => choosePane("none")} place={placeName} />
        )}
      </div>

      {place.kind !== "bin" && (
        <p className="status-bar" role="status">
          <span>
            {order.length} {order.length === 1 ? "item" : "items"}
          </span>
          {selected.size > 0 && (
            <span>
              {selected.size} selected{selDocs.length > 0 ? ` (${fileSize(selectedSize)})` : ""}
            </span>
          )}
          {cut.size > 0 && <span>{cut.size} cut, ready to paste</span>}
          <span className="status-hint">Right-click for more. Drag onto a folder to move.</span>
        </p>
      )}

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          label={menu.keys.length ? "Actions for the selection" : "Library actions"}
          items={menu.keys.length ? actionsFor(menu.keys) : backgroundMenu()}
          onClose={() => setMenu(null)}
        />
      )}

      {dialog?.kind === "upload" && (
        <UploadDialog
          folder={here ? { id: here.id, name: here.name } : null}
          initialFile={dialog.file}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null);
            reload();
          }}
        />
      )}
      {dialog?.kind === "new-folder" && (
        <NameDialog
          title={here ? `New folder in ${here.name}` : "New folder"}
          label="Folder name"
          initial="New folder"
          action="Create folder"
          onClose={() => setDialog(null)}
          onSubmit={async (name) => {
            await api("/api/library/folders", { method: "POST", json: { name, parent_id: folderId } });
            setDialog(null);
            notify(`Created the folder “${name}”`);
            reload();
          }}
        />
      )}
      {dialog?.kind === "rename" && (dialogDoc || dialogFolder) && (
        <NameDialog
          title={dialogDoc ? "Rename the document" : "Rename the folder"}
          label={dialogDoc ? "Title" : "Folder name"}
          initial={dialogDoc ? dialogDoc.title : dialogFolder!.name}
          action="Rename"
          onClose={() => setDialog(null)}
          onSubmit={async (name) => {
            if (dialogDoc) await api(`/api/documents/${dialogDoc.id}`, { method: "PATCH", json: { title: name } });
            else await api(`/api/library/folders/${dialogFolder!.id}`, { method: "PATCH", json: { name } });
            setDialog(null);
            notify(`Renamed to “${name}”`);
            reload();
          }}
        />
      )}
      {dialog?.kind === "move" && (
        <MoveDialog
          count={dialog.keys.length}
          folders={folders}
          moving={dialog.keys.filter((k) => !isDoc(k)).map(idOf)}
          from={folderId}
          onClose={() => setDialog(null)}
          onMove={async (to) => {
            await moveTo(dialog.keys, to);
            setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "delete" && (
        <ConfirmDialog
          title={
            dialog.keys.length === 1
              ? isDoc(dialog.keys[0])
                ? `Delete “${docById.get(idOf(dialog.keys[0]))?.title ?? "this document"}”?`
                : `Remove the folder “${folderById.get(idOf(dialog.keys[0]))?.name ?? ""}”?`
              : `Delete ${dialog.keys.length} items?`
          }
          action={dialog.keys.every((k) => !isDoc(k)) ? "Remove" : "Delete"}
          danger
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            const gone = dialog.keys;
            for (const k of gone) await api(isDoc(k) ? `/api/documents/${idOf(k)}` : `/api/library/folders/${idOf(k)}`, { method: "DELETE" });
            setDialog(null);
            const n = gone.filter(isDoc).length;
            notify(n ? `Moved ${n} ${n === 1 ? "document" : "documents"} to the recycle bin` : "Folder removed");
            setSelected(new Set());
            reload();
          }}
        >
          {dialog.keys.some(isDoc) && (
            <p>
              {dialog.keys.filter(isDoc).length === 1 ? "The document goes" : "Documents go"} to the recycle bin. It disappears from search,
              the assistant and everyone's library at once, and can be restored for 30 days.
            </p>
          )}
          {dialog.keys.some((k) => !isDoc(k)) && <p>Removing a folder deletes nothing: what's in it moves up to where the folder was.</p>}
        </ConfirmDialog>
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
