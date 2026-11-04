import { useEffect, useMemo, useRef, useState } from "react";
import type { FileKind } from "../types";
import { amount, colorClass, HEADER, KIND_LABELS, layout, share, type Cell, type ColorBy, type SizeBy, type TNode } from "./libraryTree";

const KIND_LABEL: Record<string, string> = {
  team: "Team",
  source: "Synced source",
  uploads: "Uploaded here",
  folder: "Folder",
};

function fit(text: string, width: number, px = 6.4): string {
  const max = Math.floor((width - 10) / px);
  if (max < 3) return "";
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/** A treemap of one part of the library: every document a rectangle, its area by the chosen
 *  size, grouped by team, source and folder. Click a document to select it; click a group's
 *  name to zoom in. The same figures are in the list next to it, for keyboard and screen
 *  reader use (the drawing itself is described in one sentence). */
export function Treemap({
  root,
  sizeBy,
  colorBy,
  kinds,
  highlight,
  selected,
  onSelect,
  onZoom,
  label,
}: {
  root: TNode;
  sizeBy: SizeBy;
  colorBy: ColorBy;
  kinds: Map<FileKind, string>;
  highlight: Set<number> | null;
  selected: number | null;
  onSelect: (docId: number) => void;
  onZoom: (path: string[]) => void;
  label: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [tip, setTip] = useState<{ cell: Cell; x: number; y: number } | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const measure = () => setWidth(Math.floor(el.getBoundingClientRect().width));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const height = Math.round(Math.min(560, Math.max(300, width * 0.56)));
  const cells = useMemo(() => (width ? layout(root, width, height) : []), [root, width, height]);
  const chosen = cells.find((c) => c.node.doc?.id === selected);

  function cellAt(target: EventTarget | null): Cell | null {
    const i = (target as Element | null)?.closest?.("[data-i]")?.getAttribute("data-i");
    return i == null ? null : cells[Number(i)];
  }

  function move(e: React.PointerEvent) {
    const cell = cellAt(e.target);
    const box = wrap.current?.getBoundingClientRect();
    if (!cell || !box) return setTip(null);
    setTip({ cell, x: e.clientX - box.left, y: e.clientY - box.top });
  }

  function click(e: React.MouseEvent) {
    const cell = cellAt(e.target);
    if (!cell) return;
    if (cell.node.doc) onSelect(cell.node.doc.id);
    else onZoom(cell.path);
  }

  // Documents and sub-groups are drawn inside their group's element, as in the tree.
  const inside = useMemo(() => {
    const m = new Map<number, number[]>();
    cells.forEach((c, i) => m.set(c.parent, [...(m.get(c.parent) ?? []), i]));
    return m;
  }, [cells]);

  function draw(i: number): React.ReactNode {
    const c = cells[i];
    const { x, y, w, h } = c.rect;
    if (c.node.doc) {
      const d = c.node.doc;
      const tone = colorClass(d, colorBy, kinds);
      const dim = highlight !== null && !highlight.has(d.id);
      const name = w > 46 && h > 18 ? fit(d.title, w, 7) : "";
      return (
        <g key={c.node.key} data-i={i} className={`tm-doc tone-${tone}${dim ? " dim" : ""}`}>
          <rect x={x + 1} y={y + 1} width={Math.max(0, w - 2)} height={Math.max(0, h - 2)} rx={Math.min(3, w / 4, h / 4)} />
          {name && (
            <text x={x + 6} y={y + 14}>
              {name}
            </text>
          )}
        </g>
      );
    }
    const title = c.header ? fit(`${c.node.name} · ${amount(c.node.value, sizeBy)}`, w, 6.6) : "";
    return (
      <g key={c.node.key} data-i={i} className="tm-group">
        <rect className="tm-group-box" x={x + 1} y={y + 1} width={Math.max(0, w - 2)} height={Math.max(0, h - 2)} rx={4} />
        {c.header && (
          // The name band zooms in, from the keyboard too. It's a sibling of what's inside the
          // group, so no control sits inside another.
          <g
            className="tm-zoom"
            tabIndex={0}
            role="button"
            aria-label={`Zoom into ${c.node.name}, ${c.node.docs} documents, ${amount(c.node.value, sizeBy)}`}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onZoom(c.path);
              }
            }}
          >
            <rect className="tm-head" x={x + 1} y={y + 1} width={Math.max(0, w - 2)} height={HEADER - 2} rx={4} />
            <text x={x + 7} y={y + 13}>
              {title}
            </text>
          </g>
        )}
        {(inside.get(i) ?? []).map(draw)}
      </g>
    );
  }

  return (
    <div className="treemap" ref={wrap} style={{ height }} onPointerLeave={() => setTip(null)}>
      {root.value === 0 ? (
        <p className="treemap-empty muted">
          {sizeBy === "use" ? "Nothing here was opened or used in an answer in this period." : "Nothing to draw here."}
        </p>
      ) : (
        width > 0 && (
          <svg width={width} height={height} role="group" aria-label={label} onPointerMove={move} onClick={click}>
            {(inside.get(-1) ?? []).map(draw)}
            {chosen && (
              <rect
                className="tm-selected"
                x={chosen.rect.x + 1}
                y={chosen.rect.y + 1}
                width={Math.max(0, chosen.rect.w - 2)}
                height={Math.max(0, chosen.rect.h - 2)}
                rx={3}
              />
            )}
          </svg>
        )
      )}
      {tip && <Tip cell={tip.cell} x={tip.x} y={tip.y} width={width} height={height} sizeBy={sizeBy} total={root.value} />}
    </div>
  );
}

function Tip({
  cell,
  x,
  y,
  width,
  height,
  sizeBy,
  total,
}: {
  cell: Cell;
  x: number;
  y: number;
  width: number;
  height: number;
  sizeBy: SizeBy;
  total: number;
}) {
  const n = cell.node;
  const d = n.doc;
  const left = x > width - 260 ? Math.max(8, x - 252) : x + 14;
  return (
    // Above the pointer near the bottom, so the map's edge doesn't cut it off.
    <div className="tm-tip" style={y > height - 100 ? { left, bottom: height - y + 12 } : { left, top: y + 16 }} role="presentation">
      <strong>{amount(n.value, sizeBy)}</strong>
      <span className="muted"> · {share(n.value, total)} of this view</span>
      <div className="tm-tip-name">{n.name}</div>
      <div className="muted small">
        {d
          ? `${KIND_LABELS[d.kind]}${d.folder ? ` in ${d.folder}` : ""} · ${d.classification}`
          : `${KIND_LABEL[n.kind] ?? ""} · ${n.docs} ${n.docs === 1 ? "document" : "documents"} · click to zoom in`}
      </div>
    </div>
  );
}
