/** What the library's explorer view works out for itself: file types, sizes, sorting, grouping,
 *  folder paths and selection. Nothing here talks to the server or to React. */

import type { Doc, Folder } from "../../types";

export type View = "content" | "details" | "list" | "tiles";
export type SortKey = "default" | "name" | "modified" | "type" | "size" | "classification" | "team";
export type GroupKey = "none" | "team" | "classification" | "type" | "modified";
export interface Sort {
  key: SortKey;
  desc: boolean;
}

export const VIEWS: { value: View; label: string; hint: string }[] = [
  { value: "content", label: "Content", hint: "Each document with its summary" },
  { value: "details", label: "Details", hint: "A table: type, size, date, team" },
  { value: "list", label: "List", hint: "Names only, close together" },
  { value: "tiles", label: "Tiles", hint: "Cards side by side" },
];

export const SORTS: { value: SortKey; label: string }[] = [
  { value: "default", label: "Best match" },
  { value: "name", label: "Name" },
  { value: "modified", label: "Date modified" },
  { value: "type", label: "Type" },
  { value: "size", label: "Size" },
  { value: "classification", label: "Classification" },
  { value: "team", label: "Team" },
];

export const GROUPS: { value: GroupKey; label: string }[] = [
  { value: "none", label: "No groups" },
  { value: "team", label: "Team" },
  { value: "classification", label: "Classification" },
  { value: "type", label: "Type" },
  { value: "modified", label: "Date modified" },
];

const TYPES: Record<string, string> = {
  md: "Markdown document",
  txt: "Text document",
  pdf: "PDF document",
  docx: "Word document",
  xlsx: "Excel workbook",
  pptx: "PowerPoint presentation",
  csv: "CSV table",
  json: "JSON file",
};

export function extension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot > 0 ? filename.slice(dot + 1).toLowerCase() : "";
}

export function typeName(filename: string): string {
  const ext = extension(filename);
  return TYPES[ext] ?? (ext ? `${ext.toUpperCase()} file` : "File");
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

const CLASS_ORDER = ["public", "internal", "confidential", "restricted"];

function compare(a: Doc, b: Doc, key: SortKey): number {
  switch (key) {
    case "name":
      return a.title.localeCompare(b.title, undefined, { numeric: true, sensitivity: "base" });
    case "modified":
      return a.updated_at.localeCompare(b.updated_at);
    case "type":
      return typeName(a.filename).localeCompare(typeName(b.filename));
    case "size":
      return a.size - b.size;
    case "classification":
      return CLASS_ORDER.indexOf(a.classification) - CLASS_ORDER.indexOf(b.classification);
    case "team":
      return a.owner_team.localeCompare(b.owner_team);
    default:
      return 0;
  }
}

/** The documents in the chosen order. "Best match" keeps the server's order (relevance when
 *  searching; otherwise what you can open first, newest first). Ties fall back to the name. */
export function sortDocs(docs: Doc[], sort: Sort): Doc[] {
  if (sort.key === "default") return sort.desc ? [...docs].reverse() : docs;
  const sign = sort.desc ? -1 : 1;
  return [...docs].sort((a, b) => sign * compare(a, b, sort.key) || compare(a, b, "name"));
}

function modifiedBucket(iso: string, now: Date): [number, string] {
  const days = (now.getTime() - new Date(iso).getTime()) / 86_400_000;
  if (days < 1) return [0, "Today"];
  if (days < 7) return [1, "In the last week"];
  if (days < 31) return [2, "In the last month"];
  if (days < 366) return [3, "In the last year"];
  return [4, "Older"];
}

export interface Group {
  label: string;
  docs: Doc[];
}

/** The documents split into labelled groups, each keeping the order it was given. */
export function groupDocs(docs: Doc[], by: GroupKey, now = new Date()): Group[] {
  if (by === "none") return [{ label: "", docs }];
  const groups = new Map<string, { rank: number | string; docs: Doc[] }>();
  for (const d of docs) {
    let label: string;
    let rank: number | string;
    if (by === "team") [rank, label] = [d.owner_team, d.owner_team];
    else if (by === "classification") [rank, label] = [CLASS_ORDER.indexOf(d.classification), d.classification[0].toUpperCase() + d.classification.slice(1)];
    else if (by === "type") [rank, label] = [typeName(d.filename), typeName(d.filename)];
    else [rank, label] = modifiedBucket(d.updated_at, now);
    if (!groups.has(label)) groups.set(label, { rank, docs: [] });
    groups.get(label)!.docs.push(d);
  }
  return [...groups.entries()]
    .sort((a, b) => (a[1].rank < b[1].rank ? -1 : a[1].rank > b[1].rank ? 1 : 0))
    .map(([label, g]) => ({ label, docs: g.docs }));
}

// ---------------------------------------------------------------- folders

/** The folder and the folders above it, top first: what the address bar shows. */
export function pathTo(folders: Folder[], id: number | null): Folder[] {
  const byId = new Map(folders.map((f) => [f.id, f]));
  const out: Folder[] = [];
  let at = id === null ? undefined : byId.get(id);
  while (at && !out.includes(at)) {
    out.unshift(at);
    at = at.parent_id === null ? undefined : byId.get(at.parent_id);
  }
  return out;
}

export function childrenOf(folders: Folder[], parent: number | null): Folder[] {
  return folders.filter((f) => f.parent_id === parent).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
}

/** The folder and every folder inside it: where it can't be moved to. */
export function within(folders: Folder[], id: number): Set<number> {
  const out = new Set<number>([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const f of folders)
      if (f.parent_id !== null && out.has(f.parent_id) && !out.has(f.id)) {
        out.add(f.id);
        grew = true;
      }
  }
  return out;
}

// ---------------------------------------------------------------- selection

/** "d:12" for a document, "f:3" for a folder. */
export type Key = string;
export const docKey = (id: number): Key => `d:${id}`;
export const folderKey = (id: number): Key => `f:${id}`;
export const idOf = (key: Key): number => Number(key.slice(2));
export const isDoc = (key: Key): boolean => key.startsWith("d:");

/** What a click does to the selection, as in a file manager: a plain click chooses one, Ctrl
 *  (or the checkbox) adds or removes one, and Shift chooses everything from the last one to here. */
export function select(
  current: Set<Key>,
  order: Key[],
  key: Key,
  anchor: Key | null,
  how: { toggle?: boolean; range?: boolean },
): Set<Key> {
  if (how.range && anchor && order.includes(anchor) && order.includes(key)) {
    const [a, b] = [order.indexOf(anchor), order.indexOf(key)];
    const span = order.slice(Math.min(a, b), Math.max(a, b) + 1);
    return new Set(how.toggle ? [...current, ...span] : span);
  }
  if (how.toggle) {
    const next = new Set(current);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  }
  return new Set([key]);
}

/** Read a saved choice, falling back when storage is unavailable or holds something else. */
export function remembered<T extends string>(name: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = localStorage.getItem(`vaultmind.library.${name}`) as T | null;
    return v && allowed.includes(v) ? v : fallback;
  } catch {
    return fallback;
  }
}

export function remember(name: string, value: string) {
  try {
    localStorage.setItem(`vaultmind.library.${name}`, value);
  } catch {
    /* not remembered; the view still works */
  }
}
