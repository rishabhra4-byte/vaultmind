// Accessibility checks an automated scanner can't make, plus accessibility-tree snapshots
// for a human to read (what a screen reader is given). Not a substitute for testing with
// VoiceOver / NVDA / TalkBack: see docs/production-readiness.md.
//   VAULTMIND_URL=http://localhost:8000 node a11y-review.js
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const BASE = process.env.VAULTMIND_URL || 'http://localhost:8000';
const OUT = path.join(__dirname, 'shots', 'a11y-tree');
fs.mkdirSync(OUT, { recursive: true });

const results = [];
const ok = (name, pass, info = '') => results.push({ name, pass: !!pass, info });
async function check(name, fn) {
  try {
    const r = await fn();
    ok(name, r !== false && typeof r !== 'string', typeof r === 'string' ? r : '');
  } catch (e) {
    ok(name, false, e.message.split('\n')[0].slice(0, 200));
  }
}

const focusInfo = (page) =>
  page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return { tag: 'body', text: '', visible: true };
    const cs = getComputedStyle(el);
    const visible = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== 'none');
    return { tag: el.tagName, text: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 40), visible };
  });

(async () => {
  const browser = await chromium.launch();

  // ---- keyboard-only sign-in with a visible focus indicator at every stop
  {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.goto(BASE + '/signin');
    const stops = [];
    let reached = false;
    for (let i = 0; i < 25 && !reached; i++) {
      await page.keyboard.press('Tab');
      const f = await focusInfo(page);
      stops.push(f);
      reached = f.text.includes('Aarav Mehta');
    }
    await check('keyboard: every stop on the sign-in page shows a focus indicator', async () => {
      const hidden = stops.filter((s) => s.tag !== 'body' && !s.visible);
      return hidden.length === 0 || `no visible focus on: ${hidden.map((h) => h.tag + ' ' + h.text).join(' | ')}`;
    });
    await check('keyboard: a persona can be reached and chosen with Tab and Enter', async () => {
      if (!reached) return `not reached after ${stops.length} tabs`;
      await page.keyboard.press('Enter');
      await page.waitForSelector('.sidebar');
    });

    await check('keyboard: Ctrl+K palette takes focus and Esc returns it', async () => {
      await page.focus('nav a[href="/library"]');
      await page.keyboard.press('Control+k');
      await page.waitForSelector('dialog.palette[open]');
      const inInput = await page.evaluate(() => document.activeElement?.closest('dialog.palette') !== null);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(200);
      const back = await page.evaluate(() => document.activeElement?.getAttribute('href'));
      return (inInput && back === '/library') || `focus in palette: ${inInput}, focus after close: ${back}`;
    });

    await check('keyboard: upload dialog traps focus and returns it on Esc', async () => {
      await page.goto(BASE + '/library');
      await page.waitForSelector('.doc-row');
      await page.focus('button:has-text("Upload a document")');
      await page.keyboard.press('Enter');
      await page.waitForSelector('dialog.modal[open]');
      let escaped = false;
      for (let i = 0; i < 30; i++) {
        await page.keyboard.press('Tab');
        const inside = await page.evaluate(() => !!document.activeElement?.closest('dialog[open]') || document.activeElement === document.body);
        if (!inside) escaped = true;
      }
      await page.keyboard.press('Escape');
      await page.waitForTimeout(200);
      const back = await page.evaluate(() => document.activeElement?.textContent?.trim());
      return (!escaped && back?.includes('Upload a document')) || `focus escaped: ${escaped}, focus after close: ${back}`;
    });

    await check('names: every button, link and form field has an accessible name', async () => {
      const unnamed = [];
      for (const p of ['/', '/library', '/library/6', '/requests']) {
        await page.goto(BASE + p);
        await page.waitForLoadState('networkidle');
        const found = await page.evaluate(() =>
          [...document.querySelectorAll('button, a[href], input, select, textarea, [role="button"]')]
            .filter((el) => el.offsetParent !== null || el.getClientRects().length)
            .filter((el) => {
              const label = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.getAttribute('title');
              const text = (el.textContent || '').trim();
              const labelled = el.id && document.querySelector(`label[for="${el.id}"]`);
              const wrapped = el.closest('label');
              return !label && !text && !labelled && !wrapped && !(el.tagName === 'INPUT' && el.placeholder);
            })
            .map((el) => el.outerHTML.slice(0, 80)),
        );
        unnamed.push(...found.map((f) => `${p}: ${f}`));
      }
      return unnamed.length === 0 || unnamed.join('\n');
    });

    // Snapshots of what assistive technology is given, for manual review.
    const pages = [
      ['assistant-answer', '/', async () => {
        await page.click('.starter >> nth=2');
        await page.waitForSelector('.msg-tools');
      }],
      ['library', '/library', () => page.waitForSelector('.doc-row')],
      ['document', '/library/6', () => page.waitForSelector('.paper')],
      ['locked-document', '/library/14', () => page.waitForSelector('.sealed-lg')],
      ['requests', '/requests', () => page.waitForSelector('h1')],
    ];
    for (const [name, url, ready] of pages) {
      await page.goto(BASE + url);
      await ready();
      fs.writeFileSync(path.join(OUT, `${name}.yml`), await page.locator('body').ariaSnapshot());
    }
    await page.close();
  }

  // ---- signed-out pages
  {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    for (const [name, url] of [['landing', '/'], ['signin', '/signin']]) {
      await page.goto(BASE + url);
      await page.waitForLoadState('networkidle');
      fs.writeFileSync(path.join(OUT, `${name}.yml`), await page.locator('body').ariaSnapshot());
    }
    await page.close();
  }

  // ---- WCAG 2.4.11 focus not obscured: sticky headers and the sidebar never cover the
  // focused element completely, on a phone and on a desktop
  for (const width of [390, 1280]) {
    const page = await browser.newPage({ viewport: { width, height: width < 800 ? 844 : 800 } });
    await page.goto(BASE + '/signin');
    await page.click('.persona:has-text("Rohan Iyer")');
    await page.waitForSelector('.sidebar, .topbar');
    for (const url of ['/library', '/library/6', '/requests']) {
      await page.goto(BASE + url);
      await page.waitForLoadState('networkidle');
      await check(`focus not obscured: every Tab stop on ${url} at ${width}px is at least partly visible`, async () => {
        const hidden = [];
        for (let i = 0; i < 45; i++) {
          await page.keyboard.press('Tab');
          // let focus-triggered transitions (the skip link slides in) and scrolling finish
          await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => {}))));
          await page.waitForTimeout(50);
          const r = await page.evaluate(() => {
            const focused = document.activeElement;
            if (!focused || focused === document.body) return null;
            // A visually hidden native input (custom switch) shows its focus ring on the sibling
            // that draws it, so that is what must stay visible.
            const fb = focused.getBoundingClientRect();
            const el = (fb.width === 0 || fb.height === 0) && focused.nextElementSibling ? focused.nextElementSibling : focused;
            const b = el.getBoundingClientRect();
            const pts = [[0.5, 0.5], [0.15, 0.15], [0.85, 0.15], [0.15, 0.85], [0.85, 0.85]];
            const seen = pts.some(([x, y]) => {
              const px = b.left + b.width * x;
              const py = b.top + b.height * y;
              if (px < 0 || py < 0 || px > innerWidth || py > innerHeight) return false;
              const hit = document.elementFromPoint(px, py);
              return hit && (hit === el || el.contains(hit) || hit.contains(el));
            });
            return seen ? null : `${focused.tagName.toLowerCase()} "${(focused.textContent || focused.getAttribute('aria-label') || '').trim().slice(0, 30)}"`;
          });
          if (r) hidden.push(r);
        }
        return hidden.length === 0 || `covered: ${[...new Set(hidden)].join(' | ')}`;
      });
    }
    await page.close();
  }

  // ---- reduced motion: answers appear at once, nothing animates
  {
    const ctx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(BASE + '/signin');
    await page.click('.persona:has-text("Neha Kapoor")');
    await page.waitForSelector('.starter');
    await check('motion: with "reduce motion", the answer appears in full with no typing effect', async () => {
      await page.click('.starter >> nth=1');
      await page.waitForSelector('.msg-assistant .rich-text');
      const caret = await page.$('.caret');
      const anims = await page.evaluate(() => document.getAnimations().filter((a) => a.playState === 'running').length);
      return (caret === null && anims === 0) || `caret: ${caret !== null}, running animations: ${anims}`;
    });
    await ctx.close();
  }

  // ---- WCAG 1.4.12 text spacing: content still fits with user-adjusted spacing
  {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.goto(BASE + '/signin');
    await page.click('.persona:has-text("Rohan Iyer")');
    await page.waitForSelector('.sidebar');
    for (const url of ['/', '/library', '/library/6', '/requests']) {
      await page.goto(BASE + url);
      await page.waitForLoadState('networkidle');
      await page.addStyleTag({
        content: '* { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }',
      }).catch(() => {});
      await page.evaluate(() => {
        // fall back to inline styles if the CSP blocked the style tag
        document.querySelectorAll('*').forEach((el) => {
          el.style.setProperty('line-height', '1.5', 'important');
          el.style.setProperty('letter-spacing', '0.12em', 'important');
          el.style.setProperty('word-spacing', '0.16em', 'important');
        });
      });
      await check(`text spacing: no horizontal overflow on ${url} at 390px`, async () => {
        const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        return over <= 0 || `${over}px overflow`;
      });
    }
    await page.close();
  }

  await browser.close();
  const failed = results.filter((r) => !r.pass);
  console.log(`${results.length - failed.length}/${results.length} accessibility review checks passed`);
  for (const f of failed) console.log(`FAIL  ${f.name}\n      ${f.info}`);
  console.log(`Accessibility-tree snapshots written to ${OUT}`);
  process.exit(failed.length ? 1 : 0);
})();
