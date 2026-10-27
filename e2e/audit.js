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

function watch(page, tag) {
  page.on('pageerror', (e) => problems.push(`[${tag}] page error: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/fonts\.g|ERR_TUNNEL|net::ERR_FAILED|Failed to load resource: the server responded with a status of (401|403|404|409|422|429)/.test(m.text()))
      problems.push(`[${tag}] console: ${m.text().slice(0, 200)}`);
  });
  page.on('requestfailed', async (r) => {
    if (/fonts\.g/.test(r.url()) || page.__expectFailures) return;
    // Chromium reports bodiless 204 responses as "aborted"; only count real failures.
    const resp = await r.response().catch(() => null);
    if (resp && resp.status() === 204) return;
    if (r.failure()?.errorText === 'net::ERR_ABORTED' && r.method() === 'DELETE') return;
    problems.push(`[${tag}] request failed: ${r.method()} ${r.url()} ${r.failure()?.errorText}`);
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
      await p.waitForURL(/\/signin$/);
    });
    await check('sign-in: typed credentials work', async () => {
      await p.fill('input[type=email]', 'ROHAN@northwind.example ');
      await p.fill('input[type=password]', 'demo1234');
      await p.click('form button:has-text("Sign in")');
      await p.waitForSelector('.sidebar');
    });
    await check('session: corrupted token signs you out cleanly', async () => {
      await p.evaluate(() => localStorage.setItem('vaultmind.token', 'garbage'));
      await p.reload();
      await p.waitForURL(/\/(signin)?$/);
      return (await p.$('.sidebar')) === null;
    });
    await p.context().close();
  });

  // ---------------------------------------------------------------- assistant as intern
  await block('assistant as intern', async () => {
    const p = await newPage(browser, 'aarav');
    await signIn(p, 'Aarav Mehta');
    await check('assistant: empty state shows four starter prompts', async () => (await p.$$('.starter')).length === 4);
    await check('assistant: send button disabled for empty message', async () => p.isDisabled('.composer button'));
    await check('assistant: starter prompt gets an answer with locked hint', async () => {
      await p.click('.starter:has-text("salary bands")');
      await p.waitForSelector('.msg-tools', { timeout: 10000 }); // appears after typing finishes
      return (await p.$$('.sealed')).length > 0;
    });
    await check('assistant: locked hint never reveals salary figures', async () => !(await p.textContent('.chat-scroll')).includes('22 to 34'));
    await check('assistant: copy button copies the answer', async () => {
      await p.click('.msg-tools button');
      if (!IS_CHROMIUM) return true; // clipboard permissions can only be granted to Chromium in automation
      await toast(p, 'Answer copied');
      const clip = await p.evaluate(() => navigator.clipboard.readText());
      return (clip.length > 20 && !clip.includes('[D')) || clip;
    });
    await check('assistant: request access validates reason length', async () => {
      await p.click('.sealed:has-text("Salary Bands") button:has-text("Request access")');
      await p.fill('dialog textarea', 'short');
      const disabled = await p.isDisabled('dialog button:has-text("Send request")');
      return disabled;
    });
    await check('assistant: request access sends and marks the hint pending', async () => {
      await p.fill('dialog textarea', 'Preparing for my conversion interview and want to understand the SDE-1 band.');
      await p.click('dialog button:has-text("Send request")');
      await toast(p, 'Request sent');
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
    await check('assistant: citation number links to the document', async () => {
      await p.waitForSelector('.msg-tools >> nth=1');
      const link = p.locator('.msg-assistant .cite').last();
      await link.click();
      await p.waitForURL(/\/library\/\d+$/);
      await p.waitForSelector('.doc-hero h1');
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
      await p.route('**/api/assistant/chat', async (r) => { await new Promise((res) => setTimeout(res, 3000)); await r.continue().catch(() => {}); });
      p.__expectFailures = true; // the cancelled request is reported as aborted, which is the point
      await p.fill('.composer textarea', 'A question I will cancel');
      await p.click('.composer button[aria-label="Send"]');
      await p.click('.composer button[aria-label="Stop waiting"]');
      await toast(p, 'Stopped waiting');
      const v = await p.inputValue('.composer textarea');
      await p.unroute('**/api/assistant/chat');
      await p.waitForTimeout(300);
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
    await check('doc: "Ask the assistant about this" asks about the document', async () => {
      await p.goto(BASE + '/library/6');
      await p.click('button:has-text("Ask the assistant about this")');
      await p.waitForSelector('.msg-user:has-text("Backend Onboarding Guide")');
      await p.waitForSelector('.msg-tools', { timeout: 10000 });
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
    await check('admin: activity filter shows only refused opens', async () => {
      await p.selectOption('select[aria-label="Filter activity"]', 'document.view_denied');
      await p.waitForSelector('.audit li.denied');
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
    await p.context().close();
  });

  // ---------------------------------------------------------------- failure handling
  await block('failure handling', async () => {
    const p = await newPage(browser, 'failures');
    await signIn(p, 'Neha Kapoor');
    p.__expectFailures = true;
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
      await p.route('**/api/assistant/chat', (r) => r.abort());
      await p.fill('.composer textarea', 'Is this kept if sending fails?');
      await p.click('.composer button');
      await p.waitForSelector('.toast-error');
      const v = await p.inputValue('.composer textarea');
      await p.unroute('**/api/assistant/chat');
      return v === 'Is this kept if sending fails?' || v;
    });
    await check('server errors: rate limit message reaches the user', async () => {
      await p.route('**/api/assistant/chat', (r) => r.fulfill({ status: 429, contentType: 'application/json', body: '{"detail":"Slow down: try again in 30s"}' }));
      await p.click('.composer button');
      await p.waitForSelector('.toast-error:has-text("Slow down")');
      await p.unroute('**/api/assistant/chat');
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
    ['admin', '/admin', 'Dev Patel'],
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
      for (const [i, [name, path, user]] of pages.entries()) {
        const tag = `${name} @${w} ${theme}`;
        try {
          const token = user ? await tokenFor(user) : null;
          // A new context starts signed out, which the first page (the landing page) needs. After
          // that the page is on the app's origin, so the next person's session is set right here
          // before navigating.
          if (i > 0) await p.evaluate((t) => (t ? localStorage.setItem('vaultmind.token', t) : localStorage.removeItem('vaultmind.token')), token);
          await open(BASE + path);
          await p.waitForLoadState('networkidle');
          await p.waitForTimeout(name === 'admin' ? 900 : 300);
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
