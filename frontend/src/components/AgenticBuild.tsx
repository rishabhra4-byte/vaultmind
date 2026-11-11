import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

/** The landing page builds itself in front of you, like a team of agents assembling it:
 *  coloured cursors fly to each block, draw its wireframe (with a label and its size, as in a
 *  design tool), and the real content wipes in. VaultMind's own steps are the agents: the
 *  retriever, the gatekeeper that checks permissions, the writer and the citer.
 *
 *  The content is in the page from the start (screen readers and search engines get all of it;
 *  only its opacity changes) and nothing moves the layout. People who prefer reduced motion get
 *  the finished page at once; anyone can skip; focusing something not yet built builds it. */

export interface Agent {
  name: string;
  color: string;
}

export const AGENTS: Agent[] = [
  { name: "Retriever", color: "#38bdf8" },
  { name: "Gatekeeper", color: "#f5b942" },
  { name: "Writer", color: "#05df72" },
  { name: "Citer", color: "#f472b6" },
];

const MOVE = 380;
const DRAW = 420;
const FILL = 520;

interface Job {
  el: HTMLElement;
  label: string;
  agent: number;
}

interface Ctx {
  register: (el: HTMLElement, label: string, agent: number, eager: boolean) => () => void;
  instant: boolean;
}

const BuildContext = createContext<Ctx | null>(null);

const prefersReduced = () =>
  typeof window === "undefined" ||
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ||
  new URLSearchParams(window.location.search).get("build") === "off";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

function markBuilt(el: HTMLElement) {
  el.dataset.build = "built";
}

export function BuildProvider({ children }: { children: ReactNode }) {
  const [instant, setInstant] = useState(prefersReduced);
  const skipped = useRef(instant);
  const queues = useRef<Job[][]>(AGENTS.map(() => []));
  const running = useRef<boolean[]>(AGENTS.map(() => false));
  const all = useRef(new Set<HTMLElement>());
  const [cursors, setCursors] = useState<({ x: number; y: number; task: string; on: boolean } | null)[]>(AGENTS.map(() => null));
  const [status, setStatus] = useState<{
    busy: boolean;
    done: number;
    total: number;
    started: number;
    took: number;
    doing: string;
  }>({
    busy: false,
    done: 0,
    total: 0,
    started: 0,
    took: 0,
    doing: "",
  });
  const [barVisible, setBarVisible] = useState(false);
  const hideTimer = useRef<number>();

  // Keep a cursor and its name tag on screen, even beside a full-width block on a phone.
  const setCursor = (i: number, v: { x: number; y: number; task: string; on: boolean } | null) => {
    const fit = v && {
      ...v,
      x: Math.max(4, Math.min(v.x, window.innerWidth - 104)),
      y: Math.max(4, Math.min(v.y, window.innerHeight - 40)),
    };
    setCursors((cs) => cs.map((c, j) => (j === i ? fit : c)));
  };

  const run = useCallback(async (i: number) => {
    if (running.current[i]) return;
    running.current[i] = true;
    while (queues.current[i].length && !skipped.current) {
      const job = queues.current[i].shift()!;
      if (job.el.dataset.build === "built") continue;
      const r = job.el.getBoundingClientRect();
      const frame = job.el.querySelector<HTMLElement>(":scope > .bld-frame .bld-tag");
      if (frame) frame.dataset.tag = `${job.label} · ${Math.round(r.width)}×${Math.round(r.height)}`; // drawn by CSS: decoration, not page text
      setStatus((s) => ({ ...s, doing: `${AGENTS[i].name} → ${job.label}` }));
      setCursor(i, {
        x: r.left + Math.min(28, r.width / 3),
        y: r.top + Math.min(20, r.height / 2),
        task: job.label,
        on: true,
      });
      await wait(MOVE);
      if (skipped.current) break;
      job.el.dataset.build = "drawing";
      setCursor(i, {
        x: r.right - Math.min(24, r.width / 4),
        y: r.bottom - Math.min(16, r.height / 3),
        task: job.label,
        on: true,
      });
      await wait(DRAW);
      if (skipped.current) break;
      job.el.dataset.build = "filling";
      await wait(FILL);
      markBuilt(job.el);
      setStatus((s) => ({ ...s, done: s.done + 1 }));
    }
    running.current[i] = false;
    setCursor(i, null);
    if (running.current.every((x) => !x)) {
      setStatus((s) => (s.busy ? { ...s, busy: false, took: Date.now() - s.started, doing: "" } : s));
      window.clearTimeout(hideTimer.current);
      hideTimer.current = window.setTimeout(() => setBarVisible(false), 4500);
    }
  }, []);

  const enqueue = useCallback(
    (job: Job) => {
      queues.current[job.agent].push(job);
      setStatus((s) =>
        s.busy
          ? { ...s, total: s.total + 1 }
          : {
              busy: true,
              done: 0,
              total: 1,
              started: Date.now(),
              took: 0,
              doing: "",
            },
      );
      setBarVisible(true);
      window.clearTimeout(hideTimer.current);
      run(job.agent);
    },
    [run],
  );

  const io = useMemo(() => {
    if (typeof IntersectionObserver === "undefined") return null;
    return new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          const el = e.target as HTMLElement;
          io?.unobserve(el);
          if (skipped.current) markBuilt(el);
          else
            enqueue({
              el,
              label: el.dataset.label ?? "block",
              agent: Number(el.dataset.agent ?? 0),
            });
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -8% 0px" },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enqueue]);

  const register = useCallback(
    (el: HTMLElement, label: string, agent: number, eager: boolean) => {
      all.current.add(el);
      el.dataset.label = label;
      el.dataset.agent = String(agent % AGENTS.length);
      if (skipped.current || !io) {
        markBuilt(el);
      } else {
        el.dataset.build = "waiting";
        // The hero builds straight away, wherever the fold is; the rest as it scrolls into view.
        if (eager) enqueue({ el, label, agent: agent % AGENTS.length });
        else io.observe(el);
      }
      // Tabbing to something not built yet builds it straight away.
      const onFocus = () => markBuilt(el);
      el.addEventListener("focusin", onFocus);
      return () => {
        all.current.delete(el);
        io?.unobserve(el);
        el.removeEventListener("focusin", onFocus);
      };
    },
    [io, enqueue],
  );

  const skip = () => {
    skipped.current = true;
    queues.current.forEach((q) => (q.length = 0));
    all.current.forEach(markBuilt);
    setCursors(AGENTS.map(() => null));
    setStatus((s) => ({
      ...s,
      busy: false,
      done: s.total,
      took: Date.now() - s.started,
      doing: "",
    }));
    setInstant(true);
    window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => setBarVisible(false), 2500);
  };

  const replay = () => {
    skipped.current = false;
    setInstant(false);
    window.scrollTo({ top: 0, behavior: "auto" });
    setStatus({
      busy: false,
      done: 0,
      total: 0,
      started: 0,
      took: 0,
      doing: "",
    });
    for (const el of all.current) {
      el.dataset.build = "waiting";
      io?.unobserve(el);
      io?.observe(el);
    }
  };

  useEffect(() => () => io?.disconnect(), [io]);
  useEffect(() => () => window.clearTimeout(hideTimer.current), []);

  const ctx = useMemo(() => ({ register, instant }), [register, instant]);
  const seconds = (status.took / 1000).toFixed(1);
  const used = new Set(cursors.map((c, i) => (c ? i : -1)).filter((i) => i >= 0)).size;

  return (
    <BuildContext.Provider value={ctx}>
      {children}
      {typeof document !== "undefined" &&
        createPortal(
          <>
            <div className="ab-agents" aria-hidden>
              {cursors.map((c, i) =>
                c ? (
                  <div
                    key={AGENTS[i].name}
                    className="ab-agent"
                    style={{
                      transform: `translate(${c.x}px, ${c.y}px)`,
                      ["--ag" as string]: AGENTS[i].color,
                    }}
                  >
                    <svg viewBox="0 0 16 16" width="16" height="16">
                      <path d="M1 1l5.5 13 2-5.5L14 6.5z" />
                    </svg>
                    <span>{AGENTS[i].name}</span>
                  </div>
                ) : null,
              )}
            </div>
            {barVisible && status.total > 0 && (
              <div className="ab-bar" role="group" aria-label="Page build">
                <span className={`ab-dot${status.busy ? " busy" : ""}`} aria-hidden />
                {status.busy ? (
                  <>
                    <span className="ab-doing">
                      <span className="ab-muted">Building page ·</span> {status.doing || "starting"}
                    </span>
                    <span className="ab-count">
                      {status.done}/{status.total}
                      {used > 1 ? ` · ${used} agents` : ""}
                    </span>
                    <button type="button" onClick={skip}>
                      Skip
                    </button>
                  </>
                ) : (
                  <>
                    <span className="ab-doing">
                      Built {status.done} {status.done === 1 ? "block" : "blocks"} in {seconds}s
                    </span>
                    <button type="button" onClick={replay}>
                      Replay
                    </button>
                  </>
                )}
              </div>
            )}
          </>,
          document.body,
        )}
    </BuildContext.Provider>
  );
}

/** One block of the page that gets built: wraps its content and draws its wireframe. */
export function Build({
  label,
  agent = 0,
  eager = false,
  className = "",
  children,
}: {
  label: string;
  agent?: number;
  /** Build on load rather than when scrolled into view. */
  eager?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const ctx = useContext(BuildContext);
  useLayoutEffect(() => {
    if (!ref.current || !ctx) return;
    return ctx.register(ref.current, label, agent, eager);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx?.register, label, agent]);
  return (
    <div ref={ref} className={`bld ${className}`} data-build={ctx && !ctx.instant ? "waiting" : "built"}>
      {children}
      <span className="bld-frame" aria-hidden>
        <span className="bld-tag" />
        <i className="h tl" />
        <i className="h tr" />
        <i className="h bl" />
        <i className="h br" />
      </span>
    </div>
  );
}
