import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { IconArrowRight, IconExternal } from "./icons";
import { EDGES, LANES, NODES, REPO, ROUTES, type MapEdge, type MapNode } from "./systemMap.data";

// ---------------------------------------------------------------- geometry (SVG units)

const PAD = 20;
const LANE_W = 220;
const LANE_GAP = 36;
const NODE_W = 196;
const NODE_H = 60;
const ROW0 = 92;
const ROW_STEP = 96;
const LANE_BOTTOM = ROW0 + 3 * ROW_STEP + NODE_H + 18;
const VB_W = PAD * 2 + LANES.length * LANE_W + (LANES.length - 1) * LANE_GAP;
const VB_H = LANE_BOTTOM + 70;

const laneIndex = Object.fromEntries(LANES.map((l, i) => [l.id, i]));
const laneX = (i: number) => PAD + i * (LANE_W + LANE_GAP);
const gapAfter = (i: number) => laneX(i) + LANE_W + LANE_GAP / 2;
const nodeBox = (n: MapNode) => {
  const x = laneX(laneIndex[n.lane]) + (LANE_W - NODE_W) / 2;
  const y = ROW0 + n.row * ROW_STEP;
  return { x, y, cx: x + NODE_W / 2, cy: y + NODE_H / 2 };
};
const corridorY = (c: number) => (c >= 4 ? LANE_BOTTOM + 12 + (c - 4) * 40 : ROW0 + c * ROW_STEP - 18);

type Pt = [number, number];
const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));

/** The points an edge runs through: right angles, along the gaps between lanes and rows. */
function route(e: MapEdge): Pt[] {
  const a = byId[e.from];
  const b = byId[e.to];
  const A = nodeBox(a);
  const B = nodeBox(b);
  const la = laneIndex[a.lane];
  const lb = laneIndex[b.lane];
  if (la < lb) {
    const sx = A.x + NODE_W;
    const ex = B.x;
    if (e.corridor === undefined) {
      const gx = gapAfter(la);
      return [[sx, A.cy], [gx, A.cy], [gx, B.cy], [ex, B.cy]];
    }
    const cy = corridorY(e.corridor);
    const g1 = gapAfter(la);
    const g2 = gapAfter(lb - 1);
    return [[sx, A.cy], [g1, A.cy], [g1, cy], [g2, cy], [g2, B.cy], [ex, B.cy]];
  }
  if (la === lb) {
    if (b.row === a.row + 1) return [[A.cx, A.y + NODE_H], [B.cx, B.y]];
    if (b.row > a.row) {
      const sx = A.x - 7;
      return [[A.x, A.cy], [sx, A.cy], [sx, B.cy], [B.x, B.cy]];
    }
    const sx = A.x + NODE_W + 8;
    return [[A.x + NODE_W, A.cy], [sx, A.cy], [sx, B.cy], [B.x + NODE_W, B.cy]];
  }
  // Backwards (right to left): down the gap, along the bottom, up into the node.
  const cy = corridorY(e.corridor ?? 4);
  const gx = gapAfter(la - 1);
  return [[A.x, A.cy], [gx, A.cy], [gx, cy], [B.cx, cy], [B.cx, B.y + NODE_H]];
}

/** A path through the points with rounded corners. */
function pathOf(pts: Pt[], r = 9): string {
  const clean = pts.filter((p, i) => i === 0 || p[0] !== pts[i - 1][0] || p[1] !== pts[i - 1][1]);
  let d = `M${clean[0][0]},${clean[0][1]}`;
  for (let i = 1; i < clean.length - 1; i++) {
    const [px, py] = clean[i - 1];
    const [x, y] = clean[i];
    const [nx, ny] = clean[i + 1];
    const r1 = Math.min(r, Math.hypot(x - px, y - py) / 2, Math.hypot(nx - x, ny - y) / 2);
    const ax = x - Math.sign(x - px) * r1;
    const ay = y - Math.sign(y - py) * r1;
    const bx = x + Math.sign(nx - x) * r1;
    const by = y + Math.sign(ny - y) * r1;
    d += ` L${ax},${ay} Q${x},${y} ${bx},${by}`;
  }
  const last = clean[clean.length - 1];
  return d + ` L${last[0]},${last[1]}`;
}

/** Where an edge's label goes: the middle of its longest horizontal stretch, and how long
 *  that stretch is (a label that doesn't fit isn't drawn). */
function labelAt(pts: Pt[]): { at: Pt; room: number } {
  let at: Pt = pts[0];
  let room = 0;
  for (let i = 1; i < pts.length; i++) {
    const [x1, y1] = pts[i - 1];
    const [x2, y2] = pts[i];
    if (y1 === y2 && Math.abs(x2 - x1) > room) {
      room = Math.abs(x2 - x1);
      at = [(x1 + x2) / 2, y1];
    }
  }
  return { at, room };
}
const labelWidth = (text: string) => text.length * 6.6 + 12;

const edgeKey = (from: string, to: string) => `${from}>${to}`;
const GEOMETRY = EDGES.map((e) => {
  const pts = route(e);
  const { at, room } = labelAt(pts);
  return { e, key: edgeKey(e.from, e.to), d: pathOf(pts), label: at, fits: !!e.label && labelWidth(e.label) + 8 <= room };
});

// ---------------------------------------------------------------- view box (zoom and pan)

type View = { x: number; y: number; w: number; h: number };

function fitView(cw: number, ch: number): View {
  const aspect = ch / cw;
  if (VB_H / VB_W < aspect) {
    const h = VB_W * aspect;
    return { x: 0, y: (VB_H - h) / 2, w: VB_W, h };
  }
  const w = VB_H / aspect;
  return { x: (VB_W - w) / 2, y: 0, w, h: VB_H };
}

function clampView(v: View, fit: View): View {
  const w = Math.min(Math.max(v.w, fit.w / 4), fit.w);
  const h = (w / v.w) * v.h;
  const x = Math.min(Math.max(v.x, Math.min(0, VB_W - w) - w * 0.15), Math.max(0, VB_W - w) + w * 0.15);
  const y = Math.min(Math.max(v.y, Math.min(0, VB_H - h) - h * 0.15), Math.max(0, VB_H - h) + h * 0.15);
  return { x, y, w, h };
}

// ---------------------------------------------------------------- the component

export function SystemMap({ headingLevel = 2 }: { headingLevel?: 2 | 3 }) {
  const [routeId, setRouteId] = useState(ROUTES[0].id);
  const [step, setStep] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [isolate, setIsolate] = useState(false);
  const [follow, setFollow] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [size, setSize] = useState({ cw: VB_W, ch: VB_H });
  const [view, setView] = useState<View>({ x: 0, y: 0, w: VB_W, h: VB_H });
  const [full, setFull] = useState(false);
  const figureRef = useRef<HTMLElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; view: View } | null>(null);
  const stepsRef = useRef<HTMLOListElement>(null);

  const r = ROUTES.find((x) => x.id === routeId)!;
  const current = r.steps[step];
  const shown = byId[selected ?? current.node];
  const fit = useMemo(() => fitView(size.cw, size.ch), [size]);

  // Edges of the route: the one into each step (or the step's own `via`).
  const routeEdges = useMemo(() => {
    const out: string[] = [];
    r.steps.forEach((s, i) => {
      if (i === 0) return;
      const [from, to] = s.via ?? [r.steps[i - 1].node, s.node];
      const hit = EDGES.find((e) => (e.from === from && e.to === to) || (e.from === to && e.to === from));
      out[i] = hit ? edgeKey(hit.from, hit.to) : "";
    });
    return out;
  }, [r]);
  const routeNodes = useMemo(() => new Set(r.steps.map((s) => s.node)), [r]);
  const doneNodes = new Set(r.steps.slice(0, step + 1).map((s) => s.node));

  // Size of the stage, so the view keeps its shape when the page is resized.
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setSize({ cw: width, ch: height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // On small screens, follow the active step from the start.
  useEffect(() => {
    if (window.matchMedia?.("(max-width: 760px)").matches) setFollow(true);
  }, []);

  const focusOn = useCallback(
    (id: string) => {
      const b = nodeBox(byId[id]);
      const w = Math.min(fit.w, Math.max(560, fit.w / 2.4));
      const h = (w / fit.w) * fit.h;
      setView(clampView({ x: b.cx - w / 2, y: b.cy - h / 2, w, h }, fit));
    },
    [fit],
  );

  useEffect(() => {
    if (follow) focusOn(current.node);
    else setView(fit);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fit, follow]);

  useEffect(() => {
    if (follow) focusOn(current.node);
    const item = stepsRef.current?.children[step] as HTMLElement | undefined;
    item?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, routeId]);

  // Trace: walk through the steps, one every 1.8 seconds.
  useEffect(() => {
    if (!playing) return;
    const t = window.setTimeout(() => {
      if (step < r.steps.length - 1) {
        setSelected(null);
        setStep(step + 1);
      } else setPlaying(false);
    }, 1800);
    return () => window.clearTimeout(t);
  }, [playing, step, r]);

  useEffect(() => {
    const onChange = () => setFull(document.fullscreenElement === figureRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  function chooseRoute(id: string) {
    setRouteId(id);
    setStep(0);
    setSelected(null);
    setPlaying(false);
  }

  function goTo(i: number) {
    setStep(Math.max(0, Math.min(r.steps.length - 1, i)));
    setSelected(null);
  }

  function pick(id: string) {
    const i = r.steps.findIndex((s) => s.node === id);
    if (i >= 0) {
      setStep(i);
      setSelected(null);
    } else setSelected(id);
    setPlaying(false);
  }

  function zoom(f: number) {
    setView((v) => {
      const w = v.w * f;
      const h = v.h * f;
      return clampView({ x: v.x + (v.w - w) / 2, y: v.y + (v.h - h) / 2, w, h }, fit);
    });
  }

  function toggleFull() {
    const el = figureRef.current;
    if (!el) return;
    if (document.fullscreenElement) document.exitFullscreen?.();
    else el.requestFullscreen?.().catch(() => {});
  }

  function onPointerDown(e: PointerEvent<SVGSVGElement>) {
    if ((e.target as Element).closest(".sm-node")) return;
    drag.current = { x: e.clientX, y: e.clientY, view };
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  }
  function onPointerMove(e: PointerEvent<SVGSVGElement>) {
    const d = drag.current;
    if (!d) return;
    const scale = d.view.w / size.cw;
    setView(clampView({ ...d.view, x: d.view.x - (e.clientX - d.x) * scale, y: d.view.y - (e.clientY - d.y) * scale }, fit));
  }
  function onPointerUp() {
    drag.current = null;
  }

  function onNodeKey(e: KeyboardEvent<SVGGElement>, id: string) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      pick(id);
    }
  }

  const H = headingLevel === 2 ? "h2" : "h3";
  const D = headingLevel === 2 ? "h3" : "h4";
  const zoomed = view.w < fit.w - 1;
  const lane = LANES.find((l) => l.id === shown.lane)!;

  return (
    <figure className={`system-map ${full ? "is-full" : ""}`} ref={figureRef} aria-labelledby="sm-title">
      <div className="sm-head">
        <div>
          <span className="sm-kicker">vaultmind / system map</span>
          <H id="sm-title">Follow a question through VaultMind</H>
          <p className="sm-lede">
            Pick a route to trace it through the system, or select any part to see what it does, what goes in and out, and
            why it's safe.
          </p>
        </div>
        <div className="sm-tools" role="group" aria-label="Map view">
          <button type="button" className="sm-tool" onClick={() => zoom(1 / 1.35)} aria-label="Zoom in" title="Zoom in">
            +
          </button>
          <button type="button" className="sm-tool" onClick={() => zoom(1.35)} aria-label="Zoom out" title="Zoom out" disabled={!zoomed}>
            −
          </button>
          <button type="button" className="sm-tool wide" onClick={() => (setFollow(false), setView(fit))} disabled={!zoomed}>
            Fit
          </button>
          <button type="button" className="sm-tool wide" onClick={toggleFull} aria-pressed={full}>
            {full ? "Exit full screen" : "Full screen"}
          </button>
        </div>
      </div>

      <div className="sm-routes" role="group" aria-label="Routes">
        {ROUTES.map((x) => (
          <button key={x.id} type="button" className="sm-route" aria-pressed={x.id === routeId} onClick={() => chooseRoute(x.id)}>
            {x.label}
          </button>
        ))}
        <span className="sm-spacer" />
        <label className="sm-switch">
          <input type="checkbox" checked={isolate} onChange={(e) => setIsolate(e.target.checked)} /> Just this route
        </label>
        <label className="sm-switch">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow the step
        </label>
      </div>
      <p className="sm-summary">{r.summary}</p>

      <div className="sm-stage" ref={stageRef}>
        <svg
          className={`sm-svg ${isolate ? "isolate" : ""}`}
          viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
          preserveAspectRatio="xMidYMid meet"
          role="group"
          aria-label={`System map: ${LANES.map((l) => l.title).join(", ")}. Route: ${r.label}.`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <defs>
            <pattern id="sm-dots" width="22" height="22" patternUnits="userSpaceOnUse">
              <circle cx="1" cy="1" r="1" className="sm-dot" />
            </pattern>
            <marker id="sm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,1 L9,5 L0,9 z" className="sm-arrow" />
            </marker>
            <marker id="sm-arrow-on" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,1 L9,5 L0,9 z" className="sm-arrow on" />
            </marker>
          </defs>
          <rect x={-VB_W} y={-VB_H} width={VB_W * 3} height={VB_H * 3} fill="url(#sm-dots)" />

          {LANES.map((l, i) => (
            <g key={l.id} className={`sm-lane lane-${l.id}`} aria-hidden="true">
              <rect x={laneX(i)} y={PAD} width={LANE_W} height={LANE_BOTTOM - PAD} rx="14" className="sm-lane-box" />
              <text x={laneX(i) + 14} y={PAD + 24} className="sm-lane-title">
                {l.title.toUpperCase()}
              </text>
              <text x={laneX(i) + 14} y={PAD + 42} className="sm-lane-sub">
                {l.sub}
              </text>
            </g>
          ))}

          <g aria-hidden="true">
            {GEOMETRY.map(({ e, key, d, label, fits }) => {
              const at = routeEdges.indexOf(key);
              const state = at < 0 ? "" : at === step ? "now" : at < step ? "done" : "ahead";
              return (
                <g key={key} className={`sm-edge ${e.optional ? "optional" : ""} ${state} ${at < 0 ? "off" : "on-route"}`}>
                  <path d={d} markerEnd={`url(#${state === "now" || state === "done" ? "sm-arrow-on" : "sm-arrow"})`} />
                  {state === "now" && <path d={d} className="sm-flow" />}
                  {e.label && fits && at >= 0 && (
                    <g transform={`translate(${label[0]},${label[1]})`} className="sm-edge-label">
                      <rect x={-labelWidth(e.label) / 2} y="-9" width={labelWidth(e.label)} height="18" rx="5" />
                      <text textAnchor="middle" y="4">
                        {e.label}
                      </text>
                    </g>
                  )}
                </g>
              );
            })}
          </g>

          {NODES.map((n) => {
            const b = nodeBox(n);
            const Icon = n.icon;
            const isNow = !selected && n.id === current.node;
            const cls = [
              "sm-node",
              `lane-${n.lane}`,
              n.optional ? "optional" : "",
              routeNodes.has(n.id) ? "on-route" : "off",
              doneNodes.has(n.id) ? "done" : "",
              isNow ? "now" : "",
              shown.id === n.id ? "shown" : "",
            ].join(" ");
            return (
              <g
                key={n.id}
                className={cls}
                role="button"
                tabIndex={0}
                aria-label={`${n.title}: ${n.sub}`}
                aria-pressed={shown.id === n.id}
                onClick={() => pick(n.id)}
                onKeyDown={(e) => onNodeKey(e, n.id)}
              >
                <rect x={b.x} y={b.y} width={NODE_W} height={NODE_H} rx="10" className="sm-node-box" />
                <rect x={b.x + 10} y={b.y + 14} width="32" height="32" rx="8" className="sm-node-icon-box" />
                <Icon x={b.x + 17} y={b.y + 21} width="18" height="18" className="sm-node-icon" />
                <text x={b.x + 52} y={b.y + 27} className="sm-node-title">
                  {n.title}
                </text>
                <text x={b.x + 52} y={b.y + 44} className="sm-node-sub">
                  {n.sub}
                </text>
              </g>
            );
          })}

          <g className="sm-legend" aria-hidden="true" transform={`translate(${PAD + 4},${VB_H - 18})`}>
            <path d="M0,0 H26" className="sm-legend-line" />
            <text x="34" y="4">
              connection
            </text>
            <path d="M120,0 H146" className="sm-legend-line optional" />
            <text x="154" y="4">
              optional
            </text>
            <path d="M226,0 H252" className="sm-legend-line on" />
            <text x="260" y="4">
              this route
            </text>
            <text x={VB_W - PAD * 2 - 8} y="4" textAnchor="end">
              drag to move · select a part to inspect it
            </text>
          </g>
        </svg>
      </div>

      <div className="sm-steps-bar">
        <ol className="sm-steps" ref={stepsRef} aria-label={`Steps of “${r.label}”`}>
          {r.steps.map((s, i) => (
            <li key={`${s.node}-${i}`}>
              <button type="button" aria-current={i === step ? "step" : undefined} className={i < step ? "done" : ""} onClick={() => goTo(i)}>
                <span className="sm-step-n">{String(i + 1).padStart(2, "0")}</span> {s.title}
              </button>
            </li>
          ))}
        </ol>
        <div className="sm-step-controls">
          <button type="button" className="sm-tool" onClick={() => goTo(step - 1)} disabled={step === 0} aria-label="Previous step">
            ←
          </button>
          <button type="button" className="btn primary small sm-play" onClick={() => (step >= r.steps.length - 1 && !playing ? (goTo(0), setPlaying(true)) : setPlaying(!playing))}>
            {playing ? "Pause" : step >= r.steps.length - 1 ? "Trace again" : "Trace the route"}
          </button>
          <button
            type="button"
            className="sm-tool"
            onClick={() => goTo(step + 1)}
            disabled={step === r.steps.length - 1}
            aria-label="Next step"
          >
            <IconArrowRight width={16} height={16} />
          </button>
        </div>
      </div>

      <div className="sm-inspector">
        <p className="sm-status" role="status" aria-live="polite">
          {selected ? (
            <>Showing {shown.title}, which isn't on this route.</>
          ) : (
            <>
              Step {step + 1} of {r.steps.length}: <strong>{current.title}.</strong> {current.note}
            </>
          )}
        </p>
        <div className="sm-detail">
          <div>
            <span className="sm-kicker">
              {lane.title} · {shown.sub}
              {shown.optional ? " · off unless set up" : ""}
            </span>
            <D className="sm-detail-title">{shown.title}</D>
            <p>{shown.body}</p>
            <a className="sm-code" href={REPO + shown.code} target="_blank" rel="noreferrer">
              <span className="sm-kicker">in the code</span> {shown.code} <IconExternal width={13} height={13} />
            </a>
          </div>
          <dl className="sm-io">
            <div>
              <dt>In</dt>
              <dd>{shown.input}</dd>
            </div>
            <div>
              <dt>Out</dt>
              <dd>{shown.output}</dd>
            </div>
            {shown.safe && (
              <div>
                <dt>Why it's safe</dt>
                <dd>{shown.safe}</dd>
              </div>
            )}
            {shown.fails && (
              <div>
                <dt>If it fails</dt>
                <dd>{shown.fails}</dd>
              </div>
            )}
          </dl>
        </div>
      </div>
    </figure>
  );
}

export default SystemMap;
