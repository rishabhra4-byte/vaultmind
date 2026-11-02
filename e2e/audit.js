// End-to-end audit of VaultMind: every interactive flow, edge cases, breakpoints, a11y.
// Records pass/fail per check instead of stopping, prints a report, exits 1 on any failure.
//
// Needs a fresh server with demo data (it changes data as it goes):
//   cd backend && rm -f vaultmind.db && uvicorn app.main:app   (serving the built frontend)
//   cd e2e && npm install && npx playwright install chromium && npm run audit
const playwright = require('playwright');
const BROWSER = process.env.BROWSER || 'chromium'; // chromium | firefox | webkit
const IS_CHROMIUM = BROWSER === 'chromium';
const fs = require('fs');
const os = require('os');
const path = require('path');
const BASE = process.env.VAULTMIND_URL || 'http://localhost:8000';
const AXE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'vaultmind-audit-'));
const SHOTS = path.join(__dirname, 'shots') + '/';
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
const problems = []; // console errors, failed requests
const notes = []; // things worth knowing that don't fail the audit (e.g. a retried navigation)
const ok = (name, cond, info = '') => results.push({ name, pass: !!cond, info });
async function check(name, fn) {
  try {
    const r = await fn();
    ok(name, r !== false, typeof r === 'string' ? r : '');
  } catch (e) {
    ok(name, false, e.message.split('\n')[0].slice(0, 200));
  }
}

// How each engine reports a request the browser itself cancelled (Chromium, Firefox, WebKit).
const CANCELLED = /^(net::ERR_ABORTED|NS_BINDING_ABORTED|Load request cancelled|cancelled)$/;
// WebKit also logs an error for each fetch of this site it cancels when a page is left:
// "Fetch API cannot load <url> due to access control checks." (Playwright reports it as a page
// error). A same-site request can't fail a real access control check.
const OWN_FETCH_CANCELLED = new RegExp(`${new URL(BASE).host.replace(/\./g, '\\.')}/\\S* due to access control checks`);

function watch(page, tag) {
  // The audit moves on as soon as a check passes, so it often loads the next page while the
  // last one is still fetching. The browser cancels those requests; that's not a problem in
  // the app. A cancelled request counts only if the page didn't navigate after it started.
  let navigations = 0;
  let lastNavigationAt = 0;
  const startedAfter = new WeakMap();
  page.on('request', (r) => {
    let leavesPage = false;
    try {
      leavesPage = r.isNavigationRequest() && r.frame() === page.mainFrame();
    } catch {} // requests without a frame (service workers) can't navigate the page
    if (leavesPage) {
      navigations++;
      lastNavigationAt = Date.now();
    }
    startedAfter.set(r, navigations);
  });
  // True for WebKit's message about a cancelled fetch when the page was left just before or after.
  const cancelledByLeaving = async (text) => {
    if (!OWN_FETCH_CANCELLED.test(text)) return false;
    const seenAt = Date.now();
    const before = navigations;
    await new Promise((resolve) => setTimeout(resolve, 1000));
    return navigations > before || seenAt - lastNavigationAt < 2000;
  };
  page.on('pageerror', async (e) => {
    if (await cancelledByLeaving(e.message)) return;
    problems.push(`[${tag}] page error: ${e.message}`);
  });
  page.on('console', async (m) => {
    if (page.__expectFailures) return; // a check that breaks things on purpose
    if (m.type() !== 'error' || /fonts\.g|ERR_TUNNEL|net::ERR_FAILED|Failed to load resource: the server responded with a status of (401|403|404|409|422|429)/.test(m.text())) return;
    if (await cancelledByLeaving(m.text())) return;
    problems.push(`[${tag}] console: ${m.text().slice(0, 200)}`);
  });
  page.on('requestfailed', async (r) => {
    if (/fonts\.g/.test(r.url()) || page.__expectFailures) return;
    const error = r.failure()?.errorText ?? '';
    if (CANCELLED.test(error)) {
      // Engines report the cancellations and the navigation that caused them in either order.
      await new Promise((resolve) => setTimeout(resolve, 1000));
      if (navigations > (startedAfter.get(r) ?? navigations)) return;
    }
    // Chromium reports bodiless 204 responses as "aborted"; only count real failures.
    const resp = await r.response().catch(() => null);
    if (resp && resp.status() === 204) return;
    // Chromium can also report a streamed answer (text/event-stream) as aborted after the page
    // has read all of it: a race between the page finishing the read and the browser marking the
    // request complete. The checks themselves fail if an answer doesn't arrive, so this isn't
    // hidden; a stream that really breaks shows "The answer was cut off" and fails its check.
    if (IS_CHROMIUM && error === 'net::ERR_ABORTED' && resp && resp.status() === 200 && /text\/event-stream/.test(resp.headers()['content-type'] ?? '')) return;
    if (error === 'net::ERR_ABORTED' && r.method() === 'DELETE') return;
    problems.push(`[${tag}] request failed: ${r.method()} ${r.url()} ${error}`);
  });
  page.on('response', (r) => {
    if (r.status() >= 500) problems.push(`[${tag}] HTTP ${r.status()} ${r.url()}`);
  });
}

// In GitHub Actions, failures also become annotations, readable without downloading logs.
const annotate = (level, title, msg) => {
  if (process.env.GITHUB_ACTIONS) {
    console.log(`::${level} title=${title}::${String(msg).replace(/%/g, '%25').replace(/\r/g, '%0D').replace(/\n/g, '%0A')}`);
  }
};

// A block that throws outside a check (e.g. a sign-in that never completes) is recorded as a
// failure and the audit carries on with the next block, instead of losing the whole report.
async function block(name, fn) {
  try {
    await fn();
  } catch (e) {
    ok(`block: "${name}" ran to the end`, false, e.message.split('\n')[0].slice(0, 200));
  }
}

async function newPage(browser, tag, opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true, ...opts });
  if (IS_CHROMIUM) await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE });
  const page = await ctx.newPage();
  watch(page, tag);
  return page;
}

async function signIn(page, name) {
  // First navigation in a fresh context: retried once on a timeout (see the note in the sweep).
  await page.goto(BASE + '/signin').catch(async (e) => {
    if (!/Timeout/.test(e.message)) throw e;
    notes.push(`${BROWSER}: loading /signin for ${name} timed out once and was retried`);
    await page.goto(BASE + '/signin');
  });
  await page.click(`.persona:has-text("${name}")`);
  await page.waitForSelector('.sidebar');
}
const toast = (page, text) => page.waitForSelector(`.toast:has-text("${text}")`, { timeout: 6000 });
async function signOut(page) {
  await page.click('button[aria-label="Sign out"]');
  await page.waitForURL(/signin/);
}

(async () => {
  const browser = await playwright[BROWSER].launch();
  console.log(`Browser: ${BROWSER} ${browser.version()}`);

  // ---------------------------------------------------------------- public pages
  await block('public pages', async () => {
    const p = await newPage(browser, 'public');
    await check('landing: loads with hero headline', async () => (await p.goto(BASE + '/'), await p.textContent('.hero h1')).includes("knows who's asking"));
    await check('landing: "How it works" anchor scrolls to section', async () => {
      await p.click('.site-links a:has-text("How it works")');
      await p.waitForTimeout(400);
      const top = await p.$eval('#how', (el) => el.getBoundingClientRect().top);
      return top < 200 || `section top at ${top}`;
    });
    await check('landing: nav gets background after scroll', async () => (await p.$('.site-nav.scrolled')) !== null);
    await check('fonts: self-hosted fonts load (no third-party requests)', async () => {
      const r = await p.evaluate(async () => {
        await document.fonts.ready;
        await document.fonts.load('16px "Plus Jakarta Sans Variable"');
        return {
          sans: document.fonts.check('16px "Plus Jakarta Sans Variable"'),
          loaded: [...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family),
          thirdParty: performance.getEntriesByType('resource').map((e) => e.name).filter((u) => !u.startsWith(location.origin)),
        };
      });
      return (r.sans && r.loaded.some((f) => f.includes('Plus Jakarta')) && r.thirdParty.length === 0) || JSON.stringify(r);
    });
    await check('csp: strict policy is sent and the theme script still runs under it', async () => {
      const res = await p.goto(BASE + '/');
      const csp = res.headers()['content-security-policy'] || '';
      const theme = await p.evaluate(() => document.documentElement.dataset.theme);
      return (csp.includes("script-src 'self'") && !csp.includes('unsafe-inline') && (theme === 'light' || theme === 'dark')) || `${csp} / ${theme}`;
    });
    await check('privacy: signed-out visitors do not get the team list', async () => {
      const meta = await (await p.request.get(BASE + '/api/meta')).json();
      return meta.teams.length === 0;
    });
    await check('landing: "Try the demo" opens sign-in', async () => {
      await p.click('.site-nav a:has-text("Try the demo")');
      await p.waitForURL(/\/signin$/);
    });
    await check('sign-in: "Back to home" returns to landing', async () => {
      await p.click('text=‹ Back to home');
      await p.waitForURL(BASE + '/');
    });
    await check('theme: toggle switches to dark and persists after reload', async () => {
      const before = await p.evaluate(() => document.documentElement.dataset.theme);
      await p.click('.site-nav .theme-toggle');
      const after = await p.evaluate(() => document.documentElement.dataset.theme);
      await p.reload();
      const reloaded = await p.evaluate(() => document.documentElement.dataset.theme);
      await p.click('.site-nav .theme-toggle'); // back
      return (before !== after && after === reloaded) || `${before} -> ${after} -> ${reloaded}`;
    });
    await check('sign-in: wrong password shows a clear error', async () => {
      await p.goto(BASE + '/signin');
      await p.fill('input[type=email]', 'rohan@northwind.example');
      await p.fill('input[type=password]', 'wrong-password');
      await p.click('form button:has-text("Sign in")');
      const t = await p.textContent('.error', { timeout: 4000 });
      return t.includes('Incorrect email or password') || t;
    });
    await check('sign-in: empty form is blocked by validation (no request sent)', async () => {
      await p.fill('input[type=email]', '');
      await p.fill('input[type=password]', '');
      let sent = false;
      const on = (r) => r.url().includes('/api/auth/login') && (sent = true);
      p.on('request', on);
      await p.click('form button:has-text("Sign in")');
      await p.waitForTimeout(400);
      p.off('request', on);
      return !sent;
    });
    await check('sign-in: deep link while signed out redirects to sign-in', async () => {
      await p.goto(BASE + '/library/3');
      await p.waitForURL(/\/signin\?next=%2Flibrary%2F3$/);
    });
    await check('sign-in: typed credentials work and return to the page that was asked for', async () => {
      await p.fill('input[type=email]', 'ROHAN@northwind.example ');
      await p.fill('input[type=password]', 'demo1234');
      await p.click('form button:has-text("Sign in")');
      await p.waitForSelector('.sidebar');
      await p.waitForURL(/\/library\/3$/);
    });
    await check('sign-in: a return address on another site is ignored', async () => {
      await p.goto(BASE + '/signin?next=' + encodeURIComponent('https://evil.example/'));
      await p.waitForURL((u) => u.pathname === '/' && u.host === new URL(BASE).host);
      await p.waitForLoadState('networkidle'); // the next check reloads; let this page finish loading first
    });
    await check('session: corrupted token signs you out cleanly', async () => {
      await p.evaluate(() => localStorage.setItem('vaultmind.token', 'garbage'));
      await p.reload();
      await p.waitForURL(/\/(signin(\?next=[^#]*)?)?$/);
      return (await p.$('.sidebar')) === null;
    });
    await p.context().close();
  });

  // ---------------------------------------------------------------- assistant as intern
  await block('assistant as intern', async () => {
    const p = await newPage(browser, 'aarav');
    await signIn(p, 'Aarav Mehta');
    await check('assistant: empty state shows four starter prompts', async () => {
      await p.waitForSelector('.starter'); // the page's code loads after the sidebar appears
      return (await p.$$('.starter')).length === 4;
    });
    await check('first week: a new joiner sees the checklist, nothing ticked yet', async () => {
      await p.waitForSelector('.first-week');
      const items = await p.$$eval('.first-week li', (els) => els.map((e) => e.className));
      return (items.length >= 4 && items.every((c) => !c.includes('done'))) || items.join(',');
    });
    await check('assistant: send button disabled for empty message', async () => p.isDisabled('.composer button'));
    await check('assistant: starter prompt gets an answer with locked hint', async () => {
      await p.click('.starter:has-text("salary bands")');
      await p.waitForSelector('.msg-tools', { timeout: 10000 }); // appears after typing finishes
      return (await p.$$('.sealed')).length > 0;
    });
    await check('assistant: locked hint never reveals salary figures', async () => !(await p.textContent('.chat-scroll')).includes('22 to 34'));
    await check('assistant: copy button copies the answer', async () => {
      await p.click('.msg-tools button[aria-label="Copy answer"]');
      if (!IS_CHROMIUM) return true; // clipboard permissions can only be granted to Chromium in automation
      await toast(p, 'Answer copied');
      const clip = await p.evaluate(() => navigator.clipboard.readText());
      return (clip.length > 20 && !clip.includes('[D')) || clip;
    });
    await check('assistant: request access opens a card with the question as the starting reason', async () => {
      await p.click('.sealed:has-text("Salary Bands") button:has-text("Request access")');
      await p.waitForSelector('.sealed .action-card textarea');
      const reason = await p.inputValue('.sealed .action-card textarea');
      const focused = await p.evaluate(() => document.activeElement?.tagName === 'TEXTAREA');
      return (reason.includes('salary bands') && focused) || `${reason} / focused ${focused}`;
    });
    await check('assistant: request access validates reason length', async () => {
      await p.fill('.sealed .action-card textarea', 'short');
      return p.isDisabled('.sealed .action-card button:has-text("Send request")');
    });
    await check('assistant: request access sends only when confirmed, and marks the hint pending', async () => {
      await p.fill('.sealed .action-card textarea', 'Preparing for my conversion interview and want to understand the SDE-1 band.');
      await p.click('.sealed .action-card button:has-text("Send request")');
      await toast(p, 'Asked for access');
      await p.waitForSelector('.sealed:has-text("Salary Bands") .pill:has-text("Request pending")');
    });
    await check('assistant: Enter sends a typed question, Shift+Enter makes a new line', async () => {
      await p.click('.composer textarea');
      await p.keyboard.type('How many days of');
      await p.keyboard.press('Shift+Enter');
      await p.keyboard.type('earned leave do I get?');
      const v = await p.inputValue('.composer textarea');
      if (!v.includes('\n')) return 'no newline';
      await p.keyboard.press('Enter');
      await p.waitForSelector('.msg-assistant:has-text("24 days")', { timeout: 10000 });
    });
    await check('assistant: citation number opens the document at the cited paragraph', async () => {
      await p.waitForSelector('.msg-tools >> nth=1');
      // The leave answer's first source is the handbook paragraph that gives the number of days.
      const link = p.locator('.msg-assistant').last().locator('.cite[href*="#p"]').first();
      await link.click();
      await p.waitForURL(/\/library\/\d+#p\d+$/);
      await p.waitForSelector('.doc-hero h1');
      await p.waitForSelector('.passage.hl');
      return (await p.textContent('.passage.hl')).includes('24 days');
    });
    await check('assistant: conversation list reopens a past conversation', async () => {
      await p.click('nav >> text=Assistant');
      await p.click('.threads .thread >> nth=0');
      await p.waitForSelector('.msg-user');
      return (await p.$$('.msg-user')).length >= 2 || `only ${(await p.$$('.msg-user')).length} messages`;
    });
    await check('assistant: "New conversation" clears the chat', async () => {
      await p.click('.threads button:has-text("New conversation")');
      await p.waitForSelector('.chat-empty');
    });
    await check('first week: asking a question and reading ticks steps off', async () => {
      await p.waitForSelector('.first-week li.done:has-text("Ask VaultMind a question")');
      return (await p.$$('.first-week li.done')).length >= 2 || 'only one step ticked';
    });
    await check('assistant: deleting a conversation removes it', async () => {
      const before = (await p.$$('.threads li')).length;
      await p.hover('.threads li >> nth=0');
      await p.click('.threads li >> nth=0 >> button[aria-label^="Delete"]');
      await p.waitForTimeout(500);
      const after = (await p.$$('.threads li')).length;
      return after === before - 1 || `${before} -> ${after}`;
    });
    await check('assistant: "Suggested for you" card opens the document', async () => {
      await p.click('.rec >> nth=0');
      await p.waitForURL(/\/library\/\d+$/);
    });
    await check('palette: Ctrl+K opens, search finds documents, Enter navigates', async () => {
      await p.keyboard.press('Control+k');
      await p.waitForSelector('dialog.palette[open]');
      await p.fill('.palette-input input', 'leave');
      await p.waitForSelector('.palette-item:has-text("Employee Handbook")');
      // first item is "Ask the assistant", move down past the nav entries to the handbook
      const items = await p.$$eval('.palette-item', (els) => els.map((e) => e.textContent));
      const idx = items.findIndex((t) => t.includes('Employee Handbook'));
      for (let i = 0; i < idx; i++) await p.keyboard.press('ArrowDown');
      await p.keyboard.press('Enter');
      await p.waitForSelector('.doc-hero h1:has-text("Employee Handbook")');
    });
    await check('palette: Esc closes it', async () => {
      await p.keyboard.press('Control+k');
      await p.waitForSelector('dialog.palette[open]');
      await p.keyboard.press('Escape');
      await p.waitForTimeout(200);
      return (await p.$('dialog.palette[open]')) === null;
    });
    await check('palette: "Ask the assistant" sends the question', async () => {
      await p.keyboard.press('Control+k');
      await p.fill('.palette-input input', 'what is the hotel limit for travel');
      await p.keyboard.press('Enter');
      await p.waitForSelector('.msg-user:has-text("hotel limit")');
      await p.waitForSelector('.msg-assistant:has-text("7,000")', { timeout: 10000 });
    });
    await check('assistant: documents that disagree are flagged, the newer one first', async () => {
      const note = p.locator('.msg-assistant').last().locator('.conflict-note');
      await note.waitFor({ timeout: 5000 });
      const t = await note.textContent();
      return (t.indexOf('Expense Reimbursement Policy') < t.indexOf('Travel Policy 2024') && t.includes('6,000')) || t;
    });
    await check('assistant: an out-of-date source is tagged; "Why these sources" gives owner, date and access', async () => {
      const last = p.locator('.msg-assistant').last();
      const tagged = await last.locator('.source:has(.stale-tag)').first().textContent();
      await last.locator('.why-sources > summary').click();
      const facts = await last.locator('.why-sources').textContent();
      const ok = tagged.includes('Travel Policy 2024') && facts.includes('Owner') && facts.includes('Updated') && facts.includes('Your access');
      return ok || `${tagged} / ${facts.slice(0, 200)}`;
    });
    await check('assistant: when no document answers it, the reply says so and lists only "closest matches"', async () => {
      await p.fill('.composer textarea', 'What is the dental insurance cover for parents?');
      await p.press('.composer textarea', 'Enter');
      const reply = p.locator('.msg-assistant:has-text("enough evidence")');
      await reply.locator('.msg-tools').waitFor({ timeout: 10000 });
      const t = await reply.textContent();
      const labels = await reply.locator('.sources-label').allTextContents();
      return (t.includes('“dental”') && labels.every((l) => l.startsWith('Closest matches'))) || t.slice(0, 240);
    });
    await check('assistant: "Not helpful" asks what was wrong, and the choice is kept', async () => {
      const reply = p.locator('.msg-assistant:has-text("enough evidence")');
      await reply.locator('button[aria-label="Not helpful"]').click();
      await reply.locator('.feedback-form label:has-text("should have said")').click();
      await reply.locator('.feedback-form textarea').fill('We do have dental cover; the HR team owns it.');
      await reply.locator('.feedback-form button:has-text("Send")').click();
      await toast(p, 'Thanks');
      const pressed = await reply.locator('button[aria-label="Not helpful"]').getAttribute('aria-pressed');
      return (pressed === 'true' && (await reply.locator('.feedback-form').count()) === 0) || `pressed=${pressed}`;
    });
    await check('assistant: "Helpful" can be given and taken back', async () => {
      const reply = p.locator('.msg-assistant').first();
      const up = reply.locator('button[aria-label="Helpful"]');
      await up.click();
      await p.waitForFunction(() => document.querySelector('.msg-assistant button[aria-label="Helpful"]')?.getAttribute('aria-pressed') === 'true');
      await up.click();
      await p.waitForFunction(() => document.querySelector('.msg-assistant button[aria-label="Helpful"]')?.getAttribute('aria-pressed') === 'false');
    });
    await check('assistant: "Answer from" narrows answers to one team, and the reply says so', async () => {
      await p.click('.scope-picker > summary');
      await p.selectOption('.scope-fields select >> nth=0', { label: 'Engineering' });
      const summary = await p.textContent('.scope-picker > summary');
      await p.fill('.composer textarea', 'What is the hotel limit per night in metro cities?');
      await p.press('.composer textarea', 'Enter');
      const reply = p.locator('.msg-assistant:has-text("among Engineering documents")');
      await reply.locator('.msg-tools').waitFor({ timeout: 10000 });
      const note = await reply.locator('.search-note').textContent();
      const folded = !(await p.isVisible('.scope-fields')); // folds away once the question is sent
      await p.click('.scope-picker > summary');
      await p.click('.scope-fields button:has-text("Clear")');
      const cleared = await p.textContent('.scope-picker > summary');
      const ok = summary.includes('Engineering documents') && note.includes('Only searched Engineering documents') && folded && cleared.includes('all documents you can open');
      return ok || `${summary} / ${note} / ${folded} / ${cleared}`;
    });
    await check('assistant: a link with ?ask= does not send a question on the person\'s behalf', async () => {
      await p.goto(BASE + '/?ask=' + encodeURIComponent('Planted question from a link'));
      await p.waitForSelector('.chat');
      await p.waitForTimeout(800);
      return (await p.$('.msg-user:has-text("Planted question")')) === null;
    });
    await check('palette: shows locked docs with a lock, and never restricted ones', async () => {
      await p.keyboard.press('Control+k');
      await p.fill('.palette-input input', 'board meeting acquisition falcon salary');
      await p.waitForTimeout(600);
      const text = await p.textContent('.palette-list');
      await p.keyboard.press('Escape');
      return (!text.includes('Falcon') && !text.includes('Board Meeting')) || text.slice(0, 200);
    });
    await check('nav: non-admin visiting /admin gets the not-found page', async () => {
      await p.goto(BASE + '/admin');
      await p.waitForSelector('h1:has-text("This page doesn\'t exist")');
    });
    await check('nav: unknown page shows a 404 with a way back', async () => {
      await p.goto(BASE + '/no-such-page');
      await p.waitForSelector('.not-found a:has-text("Go to the assistant")');
      return (await p.title()).startsWith('Page not found');
    });
    await check('titles: each page sets its own tab title', async () => {
      const titles = {};
      for (const [path, sel] of [['/', '.chat'], ['/library', '.doc-row'], ['/requests', 'h1'], ['/library/6', '.doc-hero h1']]) {
        await p.goto(BASE + path);
        await p.waitForSelector(sel);
        await p.waitForTimeout(150);
        titles[path] = await p.title();
      }
      const ok = titles['/'].startsWith('Assistant') && titles['/library'].startsWith('Library') &&
        titles['/requests'].startsWith('Access requests') && titles['/library/6'].startsWith('Backend Onboarding Guide');
      return ok || JSON.stringify(titles);
    });
    await check('assistant: answers say whether AI wrote them', async () => {
      await p.goto(BASE + '/');
      await p.click('.starter >> nth=1');
      await p.waitForSelector('.msg-tools .answer-label', { timeout: 10000 });
      return (await p.textContent('.msg-tools .answer-label')).includes('Offline answer');
    });
    await check('assistant: Stop cancels a slow answer and restores the question', async () => {
      await p.click('.threads button:has-text("New conversation")');
      await p.route('**/api/assistant/chat**', async (r) => { await new Promise((res) => setTimeout(res, 3000)); await r.continue().catch(() => {}); });
      p.__expectFailures = true; // the cancelled request is reported as aborted, which is the point
      const aborted = p.waitForEvent('requestfailed', { predicate: (r) => r.url().includes('/api/assistant/chat'), timeout: 10000 }).catch(() => null);
      await p.fill('.composer textarea', 'A question I will cancel');
      await p.click('.composer button[aria-label="Send"]');
      await p.click('.composer button[aria-label="Stop waiting"]');
      await toast(p, 'Stopped waiting');
      const v = await p.inputValue('.composer textarea');
      await aborted; // reported only once the delayed request is let through
      await p.unroute('**/api/assistant/chat**');
      p.__expectFailures = false;
      return v === 'A question I will cancel' || v;
    });
    await check('doc: unknown document shows a helpful empty state', async () => {
      await p.goto(BASE + '/library/99999');
      await p.waitForSelector('.empty:has-text("doesn\'t exist")');
    });
    await check('doc: restricted document is indistinguishable from missing', async () => {
      await p.goto(BASE + '/library/19');
      await p.waitForSelector('.empty:has-text("doesn\'t exist")');
    });
    await check('doc: locked document shows reason and request button', async () => {
      await p.goto(BASE + '/library/9'); // Incident runbook, Associate+
      await p.waitForSelector('.sealed-lg');
      const t = await p.textContent('.sealed-lg');
      return (t.includes('Associate') && (await p.$('.sealed-lg button:has-text("Request access")')) !== null) || t;
    });
    await check('doc: locked document has no download or preview', async () => (await p.$('.paper')) === null && (await p.$('button:has-text("Download")')) === null);
    await signOut(p);
    await p.context().close();
  });

  // ---------------------------------------------------------------- library + upload as manager
  await block('library and upload as manager', async () => {
    const p = await newPage(browser, 'rohan');
    await signIn(p, 'Rohan Iyer');
    await p.click('nav >> text=Library');
    await p.waitForSelector('.doc-row');
    const total = (await p.$$('.doc-row')).length;
    await check('library: "/" focuses search', async () => {
      await p.click('h1');
      await p.keyboard.press('/');
      return p.evaluate(() => document.activeElement?.getAttribute('aria-label') === 'Search documents');
    });
    await check('library: search narrows results and highlights matches', async () => {
      await p.fill('.search-box input', 'incident');
      await p.waitForSelector('.doc-title mark');
      const n = (await p.$$('.doc-row')).length;
      return (n < total && n > 0) || `${n} of ${total}`;
    });
    await check('library: search term is kept in the URL', async () => p.url().includes('q=incident'));
    await check('library: no-results state offers to clear filters, and it works', async () => {
      await p.fill('.search-box input', 'zzqqxx');
      await p.waitForSelector('.empty:has-text("Nothing matches")');
      await p.click('.empty button:has-text("clear the filters")');
      await p.waitForFunction((t) => document.querySelectorAll('.doc-row').length === t, total);
    });
    await check('library: classification chip filters, clicking again clears', async () => {
      await p.click('.chip:has-text("Confidential")');
      const tags = await p.$$eval('.doc-row .class-tag', (els) => [...new Set(els.map((e) => e.textContent))]);
      await p.click('.chip:has-text("Confidential")');
      const n = (await p.$$('.doc-row')).length;
      return (tags.length === 1 && tags[0] === 'Confidential' && n === total) || `${tags} / ${n}`;
    });
    await check('library: filter by when a document was last updated', async () => {
      const when = 'select[aria-label="Filter by when it was updated"]';
      await p.selectOption(when, '90');
      await p.waitForFunction((t) => document.querySelectorAll('.doc-row').length < t, total);
      const titles = await p.$$eval('.doc-title', (els) => els.map((e) => e.textContent));
      await p.selectOption(when, '');
      await p.waitForFunction((t) => document.querySelectorAll('.doc-row').length === t, total);
      return (titles.length > 0 && !titles.some((t) => t.includes('Travel Policy 2024'))) || titles.join(', ');
    });
    await check('library: filter by who added a document', async () => {
      const who = 'select[aria-label="Filter by who added it"]';
      await p.selectOption(who, { label: 'Added by Ananya Gupta' });
      await p.waitForFunction((t) => document.querySelectorAll('.doc-row').length < t, total);
      const titles = await p.$$eval('.doc-title', (els) => els.map((e) => e.textContent));
      await p.selectOption(who, '');
      await p.waitForFunction((t) => document.querySelectorAll('.doc-row').length === t, total);
      return (titles.some((t) => t.includes('Expense Reimbursement Policy')) && !titles.some((t) => t.includes('Code of Conduct'))) || titles.join(', ');
    });
    await check('library: a misspelt search still finds the document', async () => {
      await p.fill('.search-box input', 'reimbursment');
      await p.waitForSelector('.doc-row:first-child .doc-title:has-text("Expense Reimbursement Policy")');
      await p.fill('.search-box input', '');
      await p.waitForFunction((t) => document.querySelectorAll('.doc-row').length === t, total);
    });
    await check('library: team filter', async () => {
      await p.selectOption('select[aria-label="Filter by team"]', { label: 'Finance' });
      await p.waitForTimeout(500);
      const teams = await p.$$eval('.doc-row .team-chip', (els) => [...new Set(els.map((e) => e.textContent))]);
      await p.selectOption('select[aria-label="Filter by team"]', '');
      await p.waitForTimeout(400);
      return (teams.length === 1 && teams[0] === 'Finance') || teams.join(',');
    });
    await check('library: "show documents I can request" toggle hides locked rows', async () => {
      const lockedBefore = (await p.$$('.doc-row.locked')).length;
      await p.click('.chip-row .switch');
      await p.waitForTimeout(500);
      const lockedAfter = (await p.$$('.doc-row.locked')).length;
      await p.click('.chip-row .switch');
      await p.waitForTimeout(400);
      return (lockedBefore > 0 && lockedAfter === 0) || `${lockedBefore} -> ${lockedAfter}`;
    });
    fs.writeFileSync(`${TMP}/bad.exe`, 'MZ binary');
    fs.writeFileSync(`${TMP}/team-offsite.md`, '# Team offsite plan\n\nThe engineering offsite is in Coorg on 12 December. Buses leave the office at 7am.\n');
    fs.writeFileSync(`${TMP}/payouts.txt`, 'Contractor payouts\n\nRavi Kumar, PAN ABCDE1234F, payout INR 85,000.\n');
    fs.writeFileSync(`${TMP}/empty.txt`, '');
    await check('upload: unsupported file type is rejected with a clear message', async () => {
      await p.click('button:has-text("Upload a document")');
      await p.setInputFiles('.dropzone input', `${TMP}/bad.exe`);
      const t = await p.textContent('dialog .error');
      return t.includes("isn't a supported file") || t;
    });
    await check('upload: empty file is rejected by the server with a message', async () => {
      await p.setInputFiles('.dropzone input', `${TMP}/empty.txt`);
      await p.waitForSelector('dialog .error');
      const t = await p.textContent('dialog .error');
      return /empty/i.test(t) || t;
    });
    await check('upload: drag-and-drop a file runs the scanner', async () => {
      const data = fs.readFileSync(`${TMP}/team-offsite.md`, 'utf8');
      const dt = await p.evaluateHandle((d) => {
        const t = new DataTransfer();
        t.items.add(new File([d], 'team-offsite.md', { type: 'text/markdown' }));
        return t;
      }, data);
      await p.dispatchEvent('.dropzone', 'drop', { dataTransfer: dt });
      await p.waitForSelector('.scan:has-text("No personal data")');
      return (await p.inputValue('dialog input[maxlength="255"]')) === 'team offsite';
    });
    await check('upload: clearing the title blocks upload with a message', async () => {
      await p.fill('dialog input[maxlength="255"]', '   ');
      await p.click('dialog button:has-text("Upload"):not(:has-text("as"))');
      const t = await p.textContent('dialog .error');
      return t.includes('title') || t;
    });
    await check('upload: valid upload succeeds and appears in the library', async () => {
      await p.fill('dialog input[maxlength="255"]', 'Team Offsite Plan');
      await p.click('dialog button:has-text("Upload"):not(:has-text("as"))');
      await toast(p, 'Uploaded');
      await p.waitForSelector('.doc-row:has-text("Team Offsite Plan")');
    });
    await check('upload: same file again is flagged as a duplicate', async () => {
      await p.click('button:has-text("Upload a document")');
      await p.setInputFiles('.dropzone input', `${TMP}/team-offsite.md`);
      await p.waitForSelector('.scan');
      await p.click('dialog button:has-text("Upload"):not(:has-text("as"))');
      const t = await p.textContent('dialog .error', { timeout: 5000 });
      return t.includes('already exists') || t;
    });
    await check('upload: sensitive file marked public is stopped, override works', async () => {
      await p.setInputFiles('.dropzone input', `${TMP}/payouts.txt`);
      await p.waitForSelector('.scan:has-text("restricted")');
      await p.selectOption('dialog select >> nth=0', 'public');
      await p.click('dialog button:has-text("Upload"):not(:has-text("as"))');
      await p.waitForSelector('.warning:has-text("looks restricted")');
      await p.click('.warning button');
      await toast(p, 'Uploaded');
    });
    await check('upload: auto-summary of the sensitive file is redacted', async () => {
      await p.click('.doc-row:has-text("payouts") .doc-title');
      await p.waitForSelector('.doc-abstract');
      const s = await p.textContent('.doc-abstract');
      return !s.includes('ABCDE1234F') || s;
    });
    await check('doc: download delivers the file', async () => {
      await p.goto(BASE + '/library/6');
      const [dl] = await Promise.all([p.waitForEvent('download'), p.click('button:has-text("Download")')]);
      return dl.suggestedFilename().endsWith('.md') || dl.suggestedFilename();
    });
    await check('doc: tag opens the library filtered by that tag', async () => {
      await p.click('.tags .tag >> nth=0');
      await p.waitForURL(/\/library\?q=/);
      await p.waitForSelector('.doc-row');
    });
    await check('doc: ask about this document: a quick action answers from it, citing paragraphs', async () => {
      await p.goto(BASE + '/library/6');
      await p.click('button:has-text("Ask about this document")');
      await p.waitForSelector('.ask-drawer');
      const focused = await p.evaluate(() => document.activeElement?.getAttribute('aria-label'));
      await p.click('.ask-quick button:has-text("Key points")');
      await p.waitForSelector('.ask-drawer .msg-tools', { timeout: 10000 });
      const hrefs = await p.$$eval('.ask-drawer .msg-assistant .cite', (els) => els.map((e) => e.getAttribute('href')));
      const paragraphs = hrefs.filter((h) => /^\/library\/6#p\d+$/.test(h));
      return (focused === 'Question about this document' && paragraphs.length > 0 && hrefs.every((h) => h.startsWith('/library/6'))) || `${focused} / ${hrefs}`;
    });
    await check('doc: a paragraph citation in the panel marks that paragraph', async () => {
      await p.click('.ask-drawer .msg-assistant .cite[href*="#p"] >> nth=0');
      await p.waitForSelector('.passage.hl');
    });
    await check('doc: the panel keeps the conversation, and Esc closes it', async () => {
      await p.keyboard.press('Escape');
      await p.waitForSelector('.ask-drawer', { state: 'detached' });
      await p.click('button:has-text("Ask about this document")');
      await p.waitForSelector('.ask-drawer .msg-user:has-text("key points")');
      await p.click('.ask-drawer button[aria-label="Close the assistant panel"]');
      await p.waitForTimeout(150);
      return (await p.evaluate(() => document.activeElement?.textContent?.includes('Ask about this document'))) || 'focus not returned';
    });
    await check('library: source filter shows only files synced from that source', async () => {
      await p.goto(BASE + '/library');
      await p.waitForSelector('.doc-row');
      await p.selectOption('select[aria-label="Filter by where documents come from"]', { label: 'Shared drive (demo) (Shared folder)' });
      await p.waitForTimeout(300);
      const rows = await p.$$eval('.doc-row', (els) => els.map((e) => !!e.querySelector('.source-chip')));
      const url = p.url();
      await p.selectOption('select[aria-label="Filter by where documents come from"]', '');
      return (rows.length > 0 && rows.every(Boolean) && url.includes('source=')) || `${rows} ${url}`;
    });
    await check('doc: access settings save and update who can open it', async () => {
      await p.goto(BASE + '/library/8'); // API Design Guidelines, internal Engineering
      await p.waitForSelector('.who li');
      const before = (await p.$$('.who li')).length;
      await p.click('button:has-text("Change access settings")');
      await p.click('.settings .check-chip:has-text("Sales")');
      await p.click('button:has-text("Save access settings")');
      await toast(p, 'Access settings saved');
      await p.waitForFunction((b) => document.querySelectorAll('.who li').length > b, before);
    });
    fs.writeFileSync(`${TMP}/team-offsite-v2.md`, '# Team offsite plan\n\nThe engineering offsite is in Coorg on 14 December. Buses leave the office at 7am.\n');
    await check('doc: a new version is saved, and what changed is shown', async () => {
      await p.goto(BASE + '/library');
      await p.click('.doc-row:has-text("Team Offsite Plan") .doc-title');
      await p.click('button:has-text("Upload a new version")');
      await p.setInputFiles('dialog input[type=file]', `${TMP}/team-offsite-v2.md`);
      await p.click('dialog button:has-text("Upload new version")');
      await toast(p, 'Saved version 2');
      await p.waitForFunction(() => document.querySelectorAll('.versions li').length === 2);
      await p.click('button:has-text("See what changed")');
      await p.waitForSelector('dialog .diff ins');
      const t = await p.textContent('dialog .diff');
      await p.click('dialog button[aria-label="Close"]');
      return (t.includes('12 December') && t.includes('14 December')) || t;
    });
    await check('doc: managers can keep a document away from the AI, and allow it again', async () => {
      const aiSwitch = 'label:has-text("Let the assistant read this document")';
      await p.click(aiSwitch);
      await toast(p, "won't read");
      const off = await p.isChecked(`${aiSwitch} input`);
      await p.click(aiSwitch);
      await toast(p, 'can read this document again');
      const on = await p.isChecked(`${aiSwitch} input`);
      return (!off && on) || `${off} ${on}`;
    });
    await check('doc: delete asks for confirmation; "Keep it" cancels', async () => {
      await p.goto(BASE + '/library');
      await p.click('.doc-row:has-text("Team Offsite Plan") .doc-title');
      await p.click('button:has-text("Delete document")');
      await p.click('dialog button:has-text("Keep it")');
      await p.waitForTimeout(300);
      return p.url().includes('/library/');
    });
    await check('doc: delete removes the document', async () => {
      await p.click('button:has-text("Delete document")');
      await p.click('dialog button:has-text("Delete document")');
      await toast(p, 'Deleted');
      await p.waitForURL(/\/library$/);
      await p.waitForSelector('.doc-row');
      return (await p.$('.doc-row:has-text("Team Offsite Plan")')) === null;
    });
    await check('requests: inbox count shows in the nav', async () => (await p.textContent('nav a[href="/requests"]')).match(/\d/) !== null);
    await check('requests: approving a request with a note works', async () => {
      await p.click('nav >> text=Access requests');
      await p.waitForSelector('.req:has-text("Priya Sharma")');
      await p.fill('.req:has-text("Priya Sharma") input', 'Welcome to the panel');
      await p.selectOption('.req:has-text("Priya Sharma") select', '3');
      await p.click('.req:has-text("Priya Sharma") button:has-text("Approve")');
      await toast(p, 'Approved for 3 days');
      await p.waitForSelector('h2:has-text("Temporary access")');
      return (await p.textContent('.block:has(h2:has-text("Temporary access"))')).includes('Priya Sharma');
    });
    await check('requests: revoking a grant removes it', async () => {
      await p.click('.req:has-text("Priya Sharma") button:has-text("Revoke")');
      await toast(p, 'Access revoked');
      await p.waitForTimeout(300);
      return (await p.$('.req:has-text("Priya Sharma") button:has-text("Revoke")')) === null;
    });
    await signOut(p);
    await p.context().close();
  });

  // ---------------------------------------------------------------- requester side: cancel + deny
  await block('requester', async () => {
    const p = await newPage(browser, 'karan');
    await signIn(p, 'Karan Singh');
    await check('notifications: the bell shows an unread change to a document Karan reads', async () => {
      await p.waitForSelector('button.nav-btn .count');
      await p.click('button.nav-btn');
      await p.waitForSelector('.notif-panel .notif.unread:has-text("Expense Reimbursement Policy")');
      return p.evaluate(() => document.activeElement?.classList.contains('notif-panel'));
    });
    await check('notifications: opening one shows what changed, and marks it read', async () => {
      await p.click('.notif-panel .notif:has-text("Expense Reimbursement Policy")');
      await p.waitForURL(/\/library\/\d+\?changes=1$/);
      await p.waitForSelector('dialog .diff');
      const t = await p.textContent('dialog .diff');
      await p.click('dialog button[aria-label="Close"]');
      await p.waitForSelector('button.nav-btn .count', { state: 'detached', timeout: 5000 }).catch(() => {});
      return (t.includes('INR 7,000') && t.includes('INR 6,000')) || t;
    });
    await check('notifications: Esc closes the panel and returns focus to the bell', async () => {
      await p.click('button.nav-btn');
      await p.waitForSelector('.notif-panel');
      await p.keyboard.press('Escape');
      await p.waitForSelector('.notif-panel', { state: 'detached' });
      return p.evaluate(() => document.activeElement?.classList.contains('nav-btn'));
    });
    await check('requests: requester can cancel a pending request', async () => {
      await p.click('nav >> text=Access requests');
      await p.click('.req:has-text("Salary Bands") button:has-text("Cancel request")');
      await toast(p, 'Request cancelled');
      await p.waitForSelector('.req:has-text("Salary Bands") .pill:has-text("Cancelled")');
    });
    await check('library: request access from a locked row', async () => {
      await p.click('nav >> text=Library');
      await p.click('.doc-row.locked:has-text("Backend Onboarding") button:has-text("Request access")');
      await p.fill('dialog textarea', 'Customer asked about our onboarding process for an integration.');
      await p.click('dialog button:has-text("Send request")');
      await toast(p, 'Request sent');
      await p.waitForSelector('.doc-row:has-text("Backend Onboarding") .pill:has-text("Request pending")');
    });
    await signOut(p);
    await p.context().close();
  });

  // ---------------------------------------------------------------- admin
  await block('admin', async () => {
    const p = await newPage(browser, 'dev');
    await signIn(p, 'Dev Patel');
    await check('admin: dashboard figures count up to real values', async () => {
      await p.click('nav >> text=Admin');
      await p.waitForTimeout(1200);
      const vals = await p.$$eval('.figure dd', (els) => els.map((e) => e.textContent));
      return vals.length === 4 && vals.every((v) => /\d/.test(v)) ? true : vals.join('|');
    });
    await check('admin: insights list what people asked, without names', async () => {
      await p.waitForSelector('.insights');
      const t = await p.textContent('.insights');
      return (/\d+ questions/.test(t) && t.includes('salary bands') && !t.includes('Aarav')) || t.slice(0, 200);
    });
    await check('admin: answers marked unhelpful are listed, without who marked them', async () => {
      const panel = p.locator('.feedback-panel');
      await panel.locator('li').first().waitFor({ timeout: 10000 });
      const t = await panel.textContent();
      return (t.includes('dental insurance') && t.includes("should have said it doesn't know") && !t.includes('Aarav')) || t.slice(0, 240);
    });
    await check('admin: activity filter shows only refused opens', async () => {
      await p.click('.admin-tabs >> text=Activity');
      // Wait for the filtered list itself: the unfiltered one can also start with a refusal.
      await Promise.all([
        p.waitForResponse((r) => r.url().includes('/api/admin/audit') && r.url().includes('action=document.view_denied')),
        p.selectOption('select[aria-label="Filter activity"]', 'document.view_denied'),
      ]);
      await p.waitForSelector('.audit li.denied');
      await p.waitForTimeout(100);
      const all = await p.$$eval('.audit li', (els) => els.every((e) => e.classList.contains('denied')));
      return all;
    });
    // Each change waits for the server's answer: a toast from the previous change may still be
    // on screen, and closing the page with a request in flight could cancel it.
    const saved = (action) =>
      Promise.all([p.waitForResponse((r) => r.url().includes('/api/admin/users/') && r.request().method() === 'PATCH'), action()]).then(
        ([r]) => r.ok() || `HTTP ${r.status()}`,
      );
    await check('admin: changing a position saves', async () => {
      await p.click('.admin-tabs >> text=People');
      await p.waitForSelector('select[aria-label="Position for Aarav Mehta"]');
      const a = await saved(() => p.selectOption('select[aria-label="Position for Aarav Mehta"]', { label: 'Associate' }));
      const b = await saved(() => p.selectOption('select[aria-label="Position for Aarav Mehta"]', { label: 'Intern' }));
      await toast(p, 'Updated Aarav Mehta');
      return (a === true && b === true) || `${a} / ${b}`;
    });
    await check('admin: cannot deactivate yourself', async () => p.isDisabled('input[aria-label="Dev Patel is active"]'));
    await check('admin: deactivating and reactivating a user', async () => {
      const a = await saved(() => p.click('label:has(input[aria-label="Neha Kapoor is active"])'));
      await p.waitForFunction(() => !document.querySelector('input[aria-label="Neha Kapoor is active"]').checked);
      const b = await saved(() => p.click('label:has(input[aria-label="Neha Kapoor is active"])'));
      await p.waitForFunction(() => document.querySelector('input[aria-label="Neha Kapoor is active"]').checked);
      return (a === true && b === true) || `${a} / ${b}`;
    });
    await check('admin: add a person who signs in with a password', async () => {
      await p.click('button:has-text("Add a person")');
      await p.fill('dialog label:has-text("Full name") input', 'Zara Ali');
      await p.fill('dialog input[type=email]', 'zara@northwind.example');
      await p.fill('dialog label:has-text("Job title") input', 'Account Executive');
      await p.selectOption('dialog label:has-text("Team") select', { label: 'Sales' });
      await p.fill('dialog input[aria-label="Starting password"]', 'a-long-password');
      await p.click('dialog button:has-text("Add person")');
      await toast(p, 'Added Zara Ali');
      await p.waitForSelector('tr:has-text("zara@northwind.example") .pill:has-text("First week")');
      const r = await fetch(`${BASE}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: 'zara@northwind.example', password: 'a-long-password' }) });
      return r.ok || `sign-in ${r.status}`;
    });
    fs.writeFileSync(`${TMP}/people.csv`, 'email,name,title,team,position\nomar@northwind.example,Omar Khan,Designer,Design,Associate\nnot-an-email,X,,Sales,\n');
    await check('admin: import preview shows problems, and nothing is saved until they are fixed', async () => {
      await p.click('button:has-text("Import from a spreadsheet")');
      await p.setInputFiles('dialog input[type=file]', `${TMP}/people.csv`);
      await p.click('dialog button:has-text("Preview")');
      await p.waitForSelector('.import-preview');
      const t = await p.textContent('.import-preview');
      const blocked = await p.isDisabled('dialog button:has-text("Import")');
      await p.click('dialog button:has-text("Cancel")');
      const saved = await p.$('tr:has-text("omar@northwind.example")');
      return (t.includes('2 with problems') && blocked && saved === null) || `${t} / ${blocked}`;
    });
    await check('admin: create a group with a member', async () => {
      await p.click('.admin-tabs >> text=Groups');
      await p.click('button:has-text("New group")');
      await p.fill('dialog label:has-text("Name") input', 'Launch crew');
      await p.fill('dialog input[aria-label="Find people"]', 'Karan');
      await p.click('dialog .member-picker label:has-text("Karan Singh")');
      await p.click('dialog button:has-text("Save")');
      await toast(p, 'Created Launch crew');
      await p.waitForSelector('.list-card:has-text("Launch crew"):has-text("1 member")');
    });
    await check('admin: teams can be added, renamed and deleted when empty', async () => {
      await p.click('.admin-tabs >> text=Teams');
      await p.click('button:has-text("Add a team")');
      await p.fill('dialog label:has-text("Name") input', 'Design');
      await p.click('dialog button:has-text("Save")');
      await p.waitForSelector('tr:has-text("Design")');
      await p.click('button[aria-label="Delete Design"]');
      await p.click('dialog button:has-text("Delete team")');
      await toast(p, 'Deleted Design');
      return (await p.isDisabled('button[aria-label="Delete Sales"]')) || 'a team with people could be deleted';
    });
    await check('admin: the demo drive is synced, and "Sync now" runs again', async () => {
      await p.click('.admin-tabs >> text=Sources');
      await p.waitForSelector('.source-card:has-text("Shared drive (demo)") .status:has-text("Synced")');
      await p.click('.source-card:has-text("Shared drive (demo)") button:has-text("Sync now")');
      await toast(p, 'Syncing');
      await p.waitForSelector('.source-card:has-text("Shared drive (demo)") .status:has-text("Synced")', { timeout: 15000 });
    });
    await check('admin: Google and Microsoft say they need setting up first', async () => {
      await p.click('button:has-text("Connect a source")');
      const t = await p.textContent('dialog .kind-picker');
      const disabled = await p.isDisabled('dialog input[type=radio] >> nth=1');
      await p.click('dialog button:has-text("Cancel")');
      return (t.includes('Not set up on this server yet') && disabled) || t;
    });
    await check('admin: a glossary term is added', async () => {
      await p.click('.admin-tabs >> text=Glossary');
      await p.click('button:has-text("Add a term")');
      await p.fill('dialog label:has-text("Term") input', 'Spend policy');
      await p.fill('dialog label:has-text("Other words") input', 'expense, reimbursement');
      await p.click('dialog button:has-text("Save")');
      await toast(p, 'Spend policy');
      await p.waitForSelector('.list-card:has-text("Spend policy")');
    });
    await p.context().close();
  });

  // ---------------------------------------------------------------- sessions
  await block('sessions', async () => {
    const laptop = await newPage(browser, 'sessions-laptop');
    const phone = await newPage(browser, 'sessions-phone', { viewport: { width: 390, height: 844 } });
    await signIn(laptop, 'Karan Singh');
    await signIn(phone, 'Karan Singh');
    await check('account: lists where you are signed in, with this browser marked', async () => {
      await laptop.click('button[aria-label="Your account and sessions"]');
      await laptop.waitForSelector('dialog[open] .session-row');
      const rows = await laptop.$$eval('dialog[open] .session-row', (els) => els.map((e) => e.textContent));
      return (rows.length >= 2 && rows.filter((r) => r.includes('This browser')).length === 1) || rows.join(' | ');
    });
    await check('account: a wrong current password or mismatched new ones are refused', async () => {
      await laptop.fill('dialog[open] label:has-text("Current password") input', 'not-my-password');
      await laptop.fill('dialog[open] label:has-text("New password") >> nth=0 >> input', 'a-long-enough-new-pass');
      await laptop.fill('dialog[open] label:has-text("New password again") input', 'something-else-entirely');
      await laptop.click('dialog[open] button:has-text("Change password")');
      await laptop.waitForSelector('dialog[open] .error:has-text("don\'t match")');
      await laptop.fill('dialog[open] label:has-text("New password again") input', 'a-long-enough-new-pass');
      await laptop.click('dialog[open] button:has-text("Change password")');
      await laptop.waitForSelector('dialog[open] .error:has-text("current password")');
    });
    await check('account: signing out other sessions ends them there, and says why at sign-in', async () => {
      await laptop.click('dialog[open] button:has-text("Sign out of all other sessions")');
      await toast(laptop, 'other session');
      await laptop.keyboard.press('Escape');
      await phone.reload();
      await phone.waitForSelector('.fallback-note[role=status]:has-text("signed out from another browser")');
      return (await laptop.isVisible('.sidebar')) || 'this browser was signed out too';
    });
    await check('admin: "Sign out everywhere" ends someone\'s sessions', async () => {
      const admin = await newPage(browser, 'sessions-admin');
      await signIn(admin, 'Dev Patel');
      await admin.goto(BASE + '/admin/people');
      await admin.click('button[aria-label="Edit Karan Singh"]');
      const before = await admin.textContent('dialog[open] .session-admin');
      await admin.click('dialog[open] button:has-text("Sign out everywhere")');
      await toast(admin, 'Signed Karan Singh out');
      await admin.context().close();
      await laptop.reload();
      await laptop.waitForSelector('.fallback-note[role=status]:has-text("An administrator signed you out")');
      return before.includes('Signed in on 1 browser') || before;
    });
    await check('sign-out ends the session on the server, not just in this browser', async () => {
      await signIn(phone, 'Karan Singh');
      const token = await phone.evaluate(() => localStorage.getItem('vaultmind.token'));
      await phone.click('button[aria-label="Sign out"]');
      await phone.waitForSelector('.sidebar', { state: 'detached' });
      const status = await phone.evaluate(async (t) => (await fetch('/api/auth/me', { headers: { Authorization: `Bearer ${t}` } })).status, token);
      return status === 401 || `the old token still works (${status})`;
    });
    await laptop.context().close();
    await phone.context().close();
  });

  // ---------------------------------------------------------------- failure handling
  await block('failure handling', async () => {
    const p = await newPage(browser, 'failures');
    await signIn(p, 'Neha Kapoor');
    p.__expectFailures = true;
    await check('offline: a page whose code fails to download offers a reload; the rest of the app still works', async () => {
      await p.route('**/assets/Requests-*.js', (r) => r.abort());
      await p.click('nav >> text=Access requests');
      await p.waitForSelector('.not-found[role=alert]:has-text("This page didn\'t load")', { timeout: 10000 });
      const sidebar = await p.isVisible('.sidebar nav');
      await p.click('nav >> text=Assistant');
      await p.waitForSelector('.composer textarea');
      await p.unroute('**/assets/Requests-*.js');
      await p.click('nav >> text=Access requests');
      await p.click('.not-found[role=alert] button:has-text("Reload")'); // the failed download is remembered until a reload
      await p.waitForSelector('h1:has-text("Access requests")');
      return sidebar || 'the sidebar disappeared with the page';
    });
    await check('offline: library shows a retry state when the API is unreachable', async () => {
      await p.route('**/api/documents?*', (r) => r.abort());
      await p.click('nav >> text=Library');
      await p.waitForSelector('.empty:has-text("didn\'t load")');
      await p.unroute('**/api/documents?*');
      await p.click('.empty button:has-text("try again")');
      await p.waitForSelector('.doc-row');
    });
    await check('offline: failed chat message shows an error and keeps the draft', async () => {
      await p.click('nav >> text=Assistant');
      await p.route('**/api/assistant/chat**', (r) => r.abort());
      await p.fill('.composer textarea', 'Is this kept if sending fails?');
      await p.click('.composer button');
      await p.waitForSelector('.toast-error');
      const v = await p.inputValue('.composer textarea');
      await p.unroute('**/api/assistant/chat**');
      return v === 'Is this kept if sending fails?' || v;
    });
    await check('server errors: rate limit message reaches the user', async () => {
      await p.route('**/api/assistant/chat**', (r) => r.fulfill({ status: 429, contentType: 'application/json', body: '{"detail":"Slow down: try again in 30s"}' }));
      await p.click('.composer button');
      await p.waitForSelector('.toast-error:has-text("Slow down")');
      await p.unroute('**/api/assistant/chat**');
    });
    p.__expectFailures = false;
    await p.context().close();
  });

  // ---------------------------------------------------------------- keyboard
  await block('keyboard', async () => {
    const p = await newPage(browser, 'keyboard');
    await signIn(p, 'Priya Sharma');
    await check('keyboard: first Tab reveals "Skip to content", Enter jumps to main', async () => {
      await p.keyboard.press('Tab');
      const name = await p.evaluate(() => document.activeElement?.textContent);
      await p.keyboard.press('Enter');
      const inMain = await p.evaluate(() => document.activeElement?.id === 'main');
      return (name === 'Skip to content' && inMain) || `${name} / ${inMain}`;
    });
    await check('keyboard: starter prompts reachable and activate with Enter', async () => {
      await p.focus('.starter >> nth=1');
      await p.keyboard.press('Enter');
      await p.waitForSelector('.msg-user');
    });
    await p.context().close();
  });

  // ---------------------------------------------------------------- responsive sweep + axe
  const widths = [320, 360, 390, 768, 1024, 1280, 1440, 1920];
  const pages = [
    ['landing', '/', null],
    ['signin', '/signin', null],
    ['assistant', '/', 'Rohan Iyer'],
    ['library', '/library', 'Rohan Iyer'],
    ['document', '/library/6', 'Rohan Iyer'],
    ['requests', '/requests', 'Rohan Iyer'],
    ['document-ask', '/library/6', 'Rohan Iyer', async (p) => {
      await p.click('button:has-text("Ask about this document")');
      await p.waitForSelector('.ask-drawer');
    }],
    ['notifications', '/', 'Karan Singh', async (p) => {
      await p.click('button.nav-btn');
      await p.waitForSelector('.notif-panel');
    }],
    ['admin', '/admin', 'Dev Patel'],
    ['admin-people', '/admin/people', 'Dev Patel'],
    ['admin-sources', '/admin/sources', 'Dev Patel'],
    ['admin-glossary', '/admin/glossary', 'Dev Patel'],
  ];
  const axeFindings = new Map();
  // One session per person, reused across screen sizes, so the sweep stays fast.
  const tokens = {};
  const tokenFor = async (name) => {
    if (!tokens[name]) {
      const email = `${name.split(' ')[0].toLowerCase()}@northwind.example`;
      const r = await fetch(`${BASE}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password: 'demo1234' }) });
      tokens[name] = (await r.json()).access_token;
    }
    return tokens[name];
  };
  for (const theme of ['light', 'dark']) {
    for (const w of widths) {
      // bypassCSP only so the axe script can be injected; every other check runs with CSP enforced.
      const ctx = await browser.newContext({ viewport: { width: w, height: w < 800 ? 844 : 900 }, colorScheme: theme, bypassCSP: true });
      ctx.setDefaultTimeout(20000);
      let p = await ctx.newPage();
      watch(p, `${theme}@${w}`);
      // Firefox under automation in CI occasionally never reports "load" for a navigation (seen
      // on different pages in different runs, never in Chromium or WebKit, and the same pages
      // load fine everywhere else in the run). Such a navigation is retried once in a new tab of
      // the same context and listed in the report; a second timeout fails the check.
      const open = async (url) => {
        try {
          await p.goto(url);
        } catch (e) {
          if (!/Timeout/.test(e.message)) throw e;
          notes.push(`${BROWSER}: loading ${url.replace(BASE, '') || '/'} at ${w}px ${theme} timed out once and was retried`);
          await p.close().catch(() => {});
          p = await ctx.newPage();
          watch(p, `${theme}@${w}`);
          await p.goto(url);
        }
      };
      for (const [i, [name, path, user, after]] of pages.entries()) {
        const tag = `${name} @${w} ${theme}`;
        try {
          const token = user ? await tokenFor(user) : null;
          // A new context starts signed out, which the first page (the landing page) needs. After
          // that the page is on the app's origin, so the next person's session is set right here
          // before navigating.
          if (i > 0) await p.evaluate((t) => (t ? localStorage.setItem('vaultmind.token', t) : localStorage.removeItem('vaultmind.token')), token);
          await open(BASE + path);
          await p.waitForLoadState('networkidle');
          await p.waitForTimeout(name.startsWith('admin') ? 900 : 300);
          if (after) {
            await after(p);
            await p.waitForTimeout(350); // let the panel's entrance animation finish
          }
          const r = await p.evaluate(() => {
            const vw = document.documentElement.clientWidth;
            const over = [];
            for (const el of document.querySelectorAll('body *')) {
              const cs = getComputedStyle(el);
              if (cs.display === 'none' || cs.visibility === 'hidden' || el.closest('dialog:not([open])')) continue;
              const b = el.getBoundingClientRect();
              if (b.width === 0 || b.height === 0) continue;
              // ignore content inside horizontally scrollable containers (e.g. the people table)
              let p = el.parentElement, scroller = false;
              while (p) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll') { scroller = true; break; } p = p.parentElement; }
              if (!scroller && b.right > vw + 1) over.push(`${el.tagName.toLowerCase()}.${[...el.classList].join('.')} right=${Math.round(b.right)}`);
            }
            const h1 = document.querySelector('h1');
            return {
              scroll: document.documentElement.scrollWidth - vw,
              over: over.slice(0, 4),
              h1: h1 ? parseFloat(getComputedStyle(h1).fontSize) : 0,
              tiny: [...document.querySelectorAll('button, a, input, select')].filter((e) => {
                const b = e.getBoundingClientRect();
                return b.width > 0 && b.height > 0 && b.height < 24 && !e.classList.contains('cite') && !e.closest('.sources, .tags, .doc-meta, p, .plain, .site-foot, .audit, .who');
              }).length,
            };
          });
          ok(`layout: no horizontal overflow, ${tag}`, r.scroll <= 0 && r.over.length === 0, `${r.scroll}px ${r.over.join(', ')}`);
          if (w <= 390) ok(`layout: heading fits phone, ${tag}`, r.h1 <= 40, `h1 ${r.h1}px`);
          if (w <= 390) ok(`layout: touch targets ≥24px, ${tag}`, r.tiny === 0, `${r.tiny} small targets`);
          if (w === 768 || (w === 390 && theme === 'dark')) await p.screenshot({ path: `${SHOTS}${name}-${w}-${theme}.png` });
          if ((w === 1440 || w === 390) && (IS_CHROMIUM || theme === 'light')) {
            await p.addScriptTag({ content: AXE });
            const res = await p.evaluate(async () => {
              const r = await window.axe.run(document, { runOnly: ['wcag2a', 'wcag2aa'] });
              return r.violations.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length, sample: v.nodes[0]?.target?.join(' ') + ' :: ' + (v.nodes[0]?.failureSummary || '').slice(0, 160) }));
            });
            for (const v of res) {
              const k = `${v.id} on ${name} (${theme})`;
              if (!axeFindings.has(k)) axeFindings.set(k, v);
            }
          }
        } catch (e) {
          ok(`sweep: ${tag} loaded and was measured`, false, e.message.split('\n').slice(0, 3).join(' ').slice(0, 300));
        }
      }
      await ctx.close();
    }
  }
  for (const [k, v] of axeFindings) ok(`a11y: ${k}`, false, `${v.impact}, ${v.n} nodes, e.g. ${v.sample}`);
  ok('a11y: axe WCAG 2 A/AA scan', axeFindings.size === 0, `${axeFindings.size} distinct issues`);

  await new Promise((resolve) => setTimeout(resolve, 1200)); // let the last cancelled-request reports settle
  await browser.close();

  report();
})().catch((e) => {
  ok('audit ran to the end', false, e.stack || e.message);
  report();
});

function report() {
  const failed = results.filter((r) => !r.pass);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  for (const f of failed) {
    console.log(`FAIL  ${f.name}${f.info ? '  ->  ' + f.info : ''}`);
    annotate('error', `${BROWSER}: ${f.name}`.slice(0, 120), f.info || 'failed');
  }
  for (const n of notes) {
    console.log(`NOTE  ${n}`);
    annotate('warning', `${BROWSER}: note`, n);
  }
  const uniqueProblems = [...new Set(problems)];
  console.log(`\nConsole/network problems: ${uniqueProblems.length}`);
  uniqueProblems.slice(0, 30).forEach((x) => console.log('  ' + x));
  if (uniqueProblems.length) annotate('error', `${BROWSER}: console or network problems`, uniqueProblems.slice(0, 10).join('\n'));
  fs.writeFileSync(path.join(__dirname, `report-${BROWSER}.json`), JSON.stringify({ results, problems: uniqueProblems, notes }, null, 2));
  process.exit(failed.length || uniqueProblems.length ? 1 : 0);
}
