import { useEffect, useRef } from "react";

/** A faint field of drifting, twinkling stars behind the page, in the dark theme only.
 *  Still for people who ask for reduced motion, paused while the tab is hidden, and never
 *  in the way: it ignores the pointer and is hidden from assistive technology. */
export function Starfield({ density = 1 }: { density?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    type Star = { x: number; y: number; r: number; a: number; tw: number; ph: number; vy: number };
    let stars: Star[] = [];
    let w = 0;
    let h = 0;
    let frame = 0;
    let last = 0;

    const dark = () => document.documentElement.dataset.theme !== "light";

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth;
      h = window.innerHeight;
      canvas!.width = Math.round(w * dpr);
      canvas!.height = Math.round(h * dpr);
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.round(((w * h) / 9000) * density);
      stars = Array.from({ length: Math.min(n, 320) }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        r: Math.random() < 0.08 ? 1.3 : Math.random() * 0.8 + 0.35,
        a: Math.random() * 0.5 + 0.2,
        tw: Math.random() * 0.8 + 0.2,
        ph: Math.random() * Math.PI * 2,
        vy: Math.random() * 0.012 + 0.004,
      }));
    }

    function draw(t: number) {
      ctx!.clearRect(0, 0, w, h);
      if (!dark()) return;
      for (const s of stars) {
        const alpha = still ? s.a : s.a * (0.65 + 0.35 * Math.sin(t / 1000 * s.tw + s.ph));
        ctx!.fillStyle = `rgba(255,255,255,${alpha.toFixed(3)})`;
        ctx!.beginPath();
        ctx!.arc(s.x, s.y, s.r, 0, Math.PI * 2);
        ctx!.fill();
      }
    }

    function tick(t: number) {
      frame = requestAnimationFrame(tick);
      if (document.hidden || t - last < 50) return; // about 20 frames a second is plenty
      const dt = last ? t - last : 16;
      last = t;
      for (const s of stars) {
        s.y -= s.vy * dt * 0.06;
        if (s.y < -2) {
          s.y = h + 2;
          s.x = Math.random() * w;
        }
      }
      draw(t);
    }

    resize();
    draw(0);
    const onResize = () => {
      resize();
      draw(performance.now());
    };
    window.addEventListener("resize", onResize);
    const themeWatch = new MutationObserver(() => draw(performance.now()));
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    if (!still) frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
      themeWatch.disconnect();
    };
  }, [density]);

  return <canvas ref={ref} className="starfield" aria-hidden="true" />;
}
