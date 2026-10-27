// Performance checks (Chromium only: uses the DevTools protocol for throttling and heap stats).
//   VAULTMIND_URL=http://localhost:8000 node perf.js
// - Page loads on a throttled "slow 3G" connection: time to first paint, LCP, layout shift.
// - A memory soak: 40 rounds of navigating every page and opening the palette, heap measured
//   after forced garbage collection, to catch leaks from listeners, timers or caches.
const { chromium } = require('playwright');
const BASE = process.env.VAULTMIND_URL || 'http://localhost:8000';

const SLOW_3G = { offline: false, latency: 400, downloadThroughput: (400 * 1024) / 8, uploadThroughput: (400 * 1024) / 8 };

async function vitals(page) {
  return page.evaluate(
    () =>
      new Promise((resolve) => {
        let lcp = 0;
        let cls = 0;
        new PerformanceObserver((l) => l.getEntries().forEach((e) => (lcp = Math.max(lcp, e.startTime)))).observe({ type: 'largest-contentful-paint', buffered: true });
        new PerformanceObserver((l) => l.getEntries().forEach((e) => !e.hadRecentInput && (cls += e.value))).observe({ type: 'layout-shift', buffered: true });
        setTimeout(() => {
          const fcp = performance.getEntriesByName('first-contentful-paint')[0]?.startTime || 0;
          const bytes = performance.getEntriesByType('resource').reduce((s, e) => s + (e.transferSize || 0), 0);
          resolve({ fcp: Math.round(fcp), lcp: Math.round(lcp), cls: +cls.toFixed(3), transferKB: Math.round(bytes / 1024) });
        }, 1500);
      }),
  );
}

(async () => {
  const browser = await chromium.launch();
  const results = { base: BASE };

  // ---- slow network: first visit (cold cache) to the landing page and sign-in
  for (const path of ['/', '/signin']) {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    const cdp = await ctx.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.emulateNetworkConditions', SLOW_3G);
    const t0 = Date.now();
    await page.goto(BASE + path, { waitUntil: 'load', timeout: 60000 });
    await page.waitForSelector('h1, h2', { timeout: 60000 });
    const visible = Date.now() - t0;
    results[`slow3g ${path}`] = { headingVisibleMs: visible, ...(await vitals(page)) };
    await ctx.close();
  }

  // ---- slow network: the signed-in library shows a loading state, then content
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await ctx.newPage();
    await page.goto(BASE + '/signin');
    await page.click('.persona:has-text("Priya Sharma")');
    await page.waitForSelector('.sidebar');
    const cdp = await ctx.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.emulateNetworkConditions', SLOW_3G);
    await page.click('nav >> text=Library');
    const sawSkeleton = await page.waitForSelector('.skeleton-list', { timeout: 5000 }).then(() => true, () => false);
    const t0 = Date.now();
    await page.waitForSelector('.doc-row', { timeout: 60000 });
    results['slow3g library (signed in)'] = { showedLoadingState: sawSkeleton, contentAfterMs: Date.now() - t0 };
    await ctx.close();
  }

  // ---- memory soak
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    const cdp = await ctx.newCDPSession(page);
    await cdp.send('Performance.enable');
    await page.goto(BASE + '/signin');
    await page.click('.persona:has-text("Rohan Iyer")');
    await page.waitForSelector('.sidebar');
    const measure = async () => {
      await cdp.send('HeapProfiler.collectGarbage');
      await page.waitForTimeout(200);
      const m = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map((x) => [x.name, x.value]));
      return { heapMB: +(m.JSHeapUsedSize / 1048576).toFixed(2), nodes: m.Nodes, listeners: m.JSEventListeners };
    };
    const round = async () => {
      for (const [link, sel] of [['Library', '.doc-row'], ['Access requests', 'h1'], ['Assistant', '.chat']]) {
        await page.click(`nav >> text=${link}`);
        await page.waitForSelector(sel);
      }
      await page.goto(BASE + '/library/6');
      await page.waitForSelector('.paper');
      await page.keyboard.press('Control+k');
      await page.fill('.palette-input input', 'policy');
      await page.waitForSelector('.palette-item >> nth=2');
      await page.keyboard.press('Escape');
      await page.click('nav >> text=Assistant');
      await page.waitForSelector('.chat');
    };
    for (let i = 0; i < 5; i++) await round();
    const warm = await measure();
    for (let i = 0; i < 40; i++) await round();
    const after = await measure();
    results['memory soak (45 rounds)'] = { afterWarmup: warm, after40More: after, heapGrowthMB: +(after.heapMB - warm.heapMB).toFixed(2) };
    await ctx.close();
  }

  await browser.close();
  console.log(JSON.stringify(results, null, 2));
})();
