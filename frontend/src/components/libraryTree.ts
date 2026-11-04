/** The library map's data: documents arranged as a tree (team, source, folder), sized and
 *  coloured, and laid out as a squarified treemap (Bruls, Huizing and van Wijk, 2000): each
 *  rectangle's area is its share of the total, and rectangles stay close to square so they're
 *  easy to compare and to click. */
import type { Classification, FileKind, LibraryDoc, LibraryMapData } from "../types";

export type GroupBy = "team" | "source";

export const KIND_LABELS: Record<FileKind, string> = {
  pdf: "PDF",
  word: "Word",
  sheet: "Spreadsheet",
  slides: "Slides",
  markdown: "Markdown",
  text: "Plain text",
  web: "Web page",
  data: "Data",
  other: "Other",
};
export type SizeBy = "bytes" | "passages" | "use";
export type ColorBy = "kind" | "classification" | "age";

export interface TNode {
  key: string; // unique within the tree; a path of keys says where you are
  name: string;
  kind: "root" | "team" | "source" | "uploads" | "folder" | "doc";
  value: number; // by the chosen size
  bytes: number;
  docs: number;
  updated: string; // newest change inside
  children: TNode[];
  doc?: LibraryDoc;
}

export const use = (d: LibraryDoc) => d.opens + d.cited;

export function sizeOf(d: LibraryDoc, by: SizeBy): number {
  return by === "bytes" ? d.bytes : by === "passages" ? d.passages : use(d);
}

/** Team › source › folder › document (uploads sit right under their team), or source › folder. */
export function buildTree(data: LibraryMapData, groupBy: GroupBy, sizeBy: SizeBy): TNode {
  const teams = new Map(data.teams.map((t) => [t.id, t.name]));
  const sources = new Map(data.sources.map((s) => [s.id, s.name]));
  const root = node("root", "Whole library", "root");
  for (const d of data.documents) {
    let at = root;
    if (groupBy === "team") at = child(at, `t${d.team_id}`, teams.get(d.team_id) ?? "Unknown team", "team");
    if (d.source_id !== null) at = child(at, `${at.key}/s${d.source_id}`, sources.get(d.source_id) ?? "A removed source", "source");
    else if (groupBy === "source") at = child(at, "u", "Uploaded here", "uploads");
    for (const part of d.folder.split("/").filter(Boolean)) at = child(at, `${at.key}/f:${part}`, part, "folder");
    at.children.push({ ...node(`d${d.id}`, d.title, "doc"), doc: d });
  }
  total(root, sizeBy);
  return root;
}

function node(key: string, name: string, kind: TNode["kind"]): TNode {
  return { key, name, kind, value: 0, bytes: 0, docs: 0, updated: "", children: [] };
}

function child(parent: TNode, key: string, name: string, kind: TNode["kind"]): TNode {
  let found = parent.children.find((c) => c.key === key);
  if (!found) parent.children.push((found = node(key, name, kind)));
  return found;
}

function total(n: TNode, sizeBy: SizeBy) {
  if (n.doc) {
    n.value = sizeOf(n.doc, sizeBy);
    n.bytes = n.doc.bytes;
    n.docs = 1;
    n.updated = n.doc.updated_at;
    return;
  }
  for (const c of n.children) {
    total(c, sizeBy);
    n.value += c.value;
    n.bytes += c.bytes;
    n.docs += c.docs;
    if (c.updated > n.updated) n.updated = c.updated;
  }
  n.children.sort((a, b) => b.value - a.value || b.bytes - a.bytes || a.name.localeCompare(b.name));
}

/** The node at a path of keys, and the nodes on the way there (for the breadcrumb). */
export function walk(root: TNode, path: string[]): TNode[] {
  const trail = [root];
  for (const key of path) {
    const next = trail[trail.length - 1].children.find((c) => c.key === key);
    if (!next) break;
    trail.push(next);
  }
  return trail;
}

export function docsUnder(n: TNode, out: LibraryDoc[] = []): LibraryDoc[] {
  if (n.doc) out.push(n.doc);
  for (const c of n.children) docsUnder(c, out);
  return out;
}

/** Where a document lives, as the keys from the root (to zoom to it). */
export function pathTo(root: TNode, docId: number): string[] | null {
  const key = `d${docId}`;
  const search = (n: TNode, path: string[]): string[] | null => {
    for (const c of n.children) {
      if (c.key === key) return path;
      const found = c.doc ? null : search(c, [...path, c.key]);
      if (found) return found;
    }
    return null;
  };
  return search(root, []);
}

// ---------------------------------------------------------------- layout

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function squarify<T>(items: T[], value: (t: T) => number, r: Rect): { item: T; rect: Rect }[] {
  const list = items
    .map((item) => ({ item, v: value(item) }))
    .filter((d) => d.v > 0)
    .sort((a, b) => b.v - a.v);
  const sum = list.reduce((s, d) => s + d.v, 0);
  const out: { item: T; rect: Rect }[] = [];
  if (!sum || r.w <= 0 || r.h <= 0) return out;
  const scale = (r.w * r.h) / sum;
  let { x, y, w, h } = r;
  let i = 0;
  const worst = (s: number, min: number, max: number, side: number) =>
    Math.max((side * side * max) / (s * s), (s * s) / (side * side * min));
  while (i < list.length) {
    const side = Math.min(w, h);
    let s = list[i].v * scale;
    let min = s;
    let max = s;
    let ratio = worst(s, min, max, side);
    let j = i + 1;
    for (; j < list.length; j++) {
      const a = list[j].v * scale;
      const next = worst(s + a, Math.min(min, a), Math.max(max, a), side);
      if (next > ratio) break;
      s += a;
      min = Math.min(min, a);
      max = Math.max(max, a);
      ratio = next;
    }
    const thickness = s / side;
    let at = w >= h ? y : x;
    for (let k = i; k < j; k++) {
      const len = (list[k].v * scale) / thickness;
      out.push({ item: list[k].item, rect: w >= h ? { x, y: at, w: thickness, h: len } : { x: at, y, w: len, h: thickness } });
      at += len;
    }
    if (w >= h) {
      x += thickness;
      w -= thickness;
    } else {
      y += thickness;
      h -= thickness;
    }
    i = j;
  }
  return out;
}

export interface Cell {
  node: TNode;
  path: string[]; // keys from the drawn root to this node
  parent: number; // index of the group it's drawn inside, or -1
  rect: Rect;
  depth: number;
  header: boolean; // a group with room for its name across the top
}

const HEADER = 18;
const PAD = 3;

/** Every rectangle to draw, groups before what's inside them; each knows the group it's in, so
 *  a group's drawing can contain its documents (as the tree does). */
export function layout(root: TNode, width: number, height: number): Cell[] {
  const cells: Cell[] = [];
  const place = (n: TNode, r: Rect, depth: number, path: string[], parent: number) => {
    for (const { item, rect } of squarify(n.children, (c) => c.value, r)) {
      if (item.doc) {
        cells.push({ node: item, path: [...path, item.key], parent, rect, depth, header: false });
        continue;
      }
      const header = rect.w > 56 && rect.h > HEADER + 14;
      const at = cells.push({ node: item, path: [...path, item.key], parent, rect, depth, header }) - 1;
      const inner = {
        x: rect.x + PAD,
        y: rect.y + (header ? HEADER : PAD),
        w: rect.w - 2 * PAD,
        h: rect.h - (header ? HEADER : PAD) - PAD,
      };
      if (inner.w > 3 && inner.h > 3) place(item, inner, depth + 1, [...path, item.key], at);
    }
  };
  place(root, { x: 0, y: 0, w: width, h: height }, 0, [], -1);
  return cells;
}

// ---------------------------------------------------------------- colour

/** Classes for a document's fill, by what the colour shows. The colours themselves are CSS
 *  variables (light and dark steps of a palette checked for colour-blind separation). */
export const CLASS_ORDER: Classification[] = ["public", "internal", "confidential", "restricted"];
export const AGE_BUCKETS: { label: string; days: number }[] = [
  { label: "This month", days: 30 },
  { label: "1 to 3 months", days: 91 },
  { label: "3 to 12 months", days: 365 },
  { label: "1 to 2 years", days: 730 },
  { label: "Older", days: Infinity },
];

export function ageBucket(iso: string, now = Date.now()): number {
  const days = (now - new Date(iso).getTime()) / 86400000;
  return AGE_BUCKETS.findIndex((b) => days < b.days);
}

/** The three file types with the most bytes get a colour each; the rest share a neutral one
 *  (more colours than three can't all be told apart where any two may touch). */
export function kindColors(data: LibraryMapData): Map<FileKind, string> {
  const slots = new Map<FileKind, string>();
  data.kinds.forEach((k, i) => slots.set(k.kind, i < 3 ? `k${i + 1}` : "other"));
  return slots;
}

export function colorClass(d: LibraryDoc, by: ColorBy, kinds: Map<FileKind, string>): string {
  if (by === "kind") return kinds.get(d.kind) ?? "other";
  if (by === "classification") return `c${CLASS_ORDER.indexOf(d.classification) + 1}`;
  return `a${ageBucket(d.updated_at) + 1}`;
}

// ---------------------------------------------------------------- numbers

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u++;
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[u]}`;
}

export function amount(n: number, by: SizeBy): string {
  if (by === "bytes") return bytes(n);
  if (by === "passages") return n === 1 ? "1 passage" : `${n.toLocaleString()} passages`;
  return n === 1 ? "used once" : `used ${n.toLocaleString()} times`;
}

export function share(part: number, whole: number): string {
  if (!whole) return "0%";
  const p = (part / whole) * 100;
  return p >= 10 || p === 0 ? `${Math.round(p)}%` : p < 0.1 ? "<0.1%" : `${p.toFixed(1)}%`;
}
