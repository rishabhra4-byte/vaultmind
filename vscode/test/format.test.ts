import assert from "node:assert/strict";
import { test } from "node:test";
import { parseEvent, errorText, Client } from "../src/client";
import { numberCitations, toHtml, toMarkdown, modeLabel, escapeHtml } from "../src/format";
import { parseQuest, report, template } from "../src/quest";
import { parseRules, projectQuery } from "../src/project";
import type { Reply } from "../src/client";

const reply: Reply = {
  id: 1,
  content: "You get **24 days** of earned leave [D7.2]. Expenses follow the policy [D9] and [D7.4].",
  meta: { mode: "ai", citations: [{ id: 7, title: "Employee Handbook 2026" }, { id: 9, title: "Expense Policy" }, { id: 11, title: "Holiday Calendar" }] },
};

test("citations are numbered by first use, with every paragraph cited, and unmarked sources after", () => {
  const n = numberCitations(reply.content, reply.meta.citations);
  assert.deepEqual([...n.order.entries()], [[7, 1], [9, 2], [11, 3]]);
  assert.deepEqual(n.sources[0], { n: 1, id: 7, title: "Employee Handbook 2026", passages: [2, 4] });
});

test("answers become Markdown with links to the paragraph, and a source list", () => {
  const md = toMarkdown(reply, (id, p) => `https://vm/library/${id}${p !== undefined ? `#p${p}` : ""}`);
  assert.match(md, /earned leave \[\[1\]\]\(https:\/\/vm\/library\/7#p2\)\./);
  assert.match(md, /Sources:\n\n1\. \[Employee Handbook 2026\]\(https:\/\/vm\/library\/7\)\n2\. \[Expense Policy\]/);
});

test("the chat HTML escapes everything and only adds its own markup", () => {
  const n = numberCitations(reply.content, reply.meta.citations);
  const html = toHtml('<img src=x onerror=alert(1)> **bold** [D9]\n\n- one `<b>`\n- two\n\n> quote', n.order);
  assert.ok(!html.includes("<img"), html);
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.ok(html.includes("<strong>bold</strong>"));
  assert.ok(html.includes('<button class="cite" data-doc="9" title="Open source 2">2</button>'));
  assert.ok(html.includes("<ul><li>one <code>&lt;b&gt;</code></li><li>two</li></ul>"));
  assert.ok(html.includes("<blockquote>quote</blockquote>"));
  assert.equal(escapeHtml(`"'&`), "&quot;&#39;&amp;");
});

test("answer labels say where the answer came from", () => {
  assert.equal(modeLabel(reply), "AI answer from your documents");
  assert.equal(modeLabel({ ...reply, meta: { mode: "verified", verified: { by: "Neha Kapoor", team: "Human Resources", verified_at: "" } } }), "Verified by Neha Kapoor for Human Resources");
  assert.equal(modeLabel({ ...reply, meta: { mode: "offline" } }), "Matching passages (no AI)");
});

test("server-sent events and errors are read", () => {
  assert.deepEqual(parseEvent('event: delta\ndata: {"text":"Hi"}'), { event: "delta", data: { text: "Hi" } });
  assert.equal(parseEvent(": keep-alive"), null);
  assert.equal(errorText({ detail: "Nope" }, 403), "Nope");
  assert.equal(errorText({ detail: { message: "This exact file already exists" } }, 409), "This exact file already exists");
  assert.equal(errorText(null, 401), "You're signed out. Sign in again.");
});

test("a quest's questions come from its bullet list, and the template is a runnable quest", () => {
  const q = parseQuest(template("On-call readiness"));
  assert.equal(q.title, "On-call readiness");
  assert.equal(q.questions.length, 2);
  const brief = parseQuest("# X\n\n## Brief\n\nWhat is the notice period?\n");
  assert.deepEqual(brief.questions, ["What is the notice period?"]);
  const numbered = parseQuest("# Y\n## Questions\n1. First question\n2) Second question\n");
  assert.deepEqual(numbered.questions, ["First question", "Second question"]);
});

test("a quest report counts what was answered and flags what wasn't", () => {
  const client = new Client("https://vm/");
  const text = report(
    { title: "Leave", brief: "For new joiners", questions: ["a", "b", "c"] },
    [
      { question: "How much leave?", result: { conversation: { id: 1, title: "" }, reply } },
      { question: "Dental cover?", result: { conversation: { id: 2, title: "" }, reply: { id: 2, content: "Nothing covers it.", meta: { mode: "offline", citations: [], evidence: { enough: false } } } } },
      { question: "Broken?", error: "Can't reach VaultMind" },
    ],
    client,
    new Date("2026-11-05T10:00:00Z"),
  );
  assert.match(text, /\*\*1 of 3\*\* questions answered/);
  assert.match(text, /\| 2 \| Dental cover\? \| Not enough in the documents \|/);
  assert.match(text, /\| 3 \| Broken\? \| Failed \|/);
  assert.match(text, /\[\[1\]\]\(https:\/\/vm\/library\/7#p2\)/);
  assert.match(text, /_Couldn't ask this: Can't reach VaultMind_/);
});

test("the project's documents are searched by its topics, or by what its README and package say", () => {
  assert.equal(projectQuery({}, ["payments", "refunds"]), "payments refunds");
  const q = projectQuery({
    readme: "# Orders API\n\nThe orders service handles payments and refunds for the storefront.\n\n```bash\nnpm i\n```",
    packageJson: JSON.stringify({ name: "@acme/orders-api", description: "Orders and payments", keywords: ["payments"] }),
    folders: ["src", "node_modules", "migrations", ".git"],
  });
  const words = q.split(" ");
  assert.ok(words.includes("orders") && words.includes("payments") && words.includes("migrations"), q);
  assert.ok(!words.includes("src") && !words.includes("node_modules") && !words.includes("npm"), q);
});

test("rules are checked, and wrong settings are reported rather than sent", () => {
  const ok = parseRules('{"filters": {"team_id": 2, "classification": "internal", "updated_within_days": 365}, "topics": ["incidents"]}');
  assert.deepEqual(ok.filters, { team_id: 2, classification: "internal", updated_within_days: 365 });
  assert.deepEqual(ok.topics, ["incidents"]);
  assert.equal(ok.problems.length, 0);
  const bad = parseRules('{"filters": {"team_id": "eng", "classification": "secret", "colour": 1}, "topics": "x"}');
  assert.deepEqual(bad.filters, {});
  assert.equal(bad.problems.length, 4);
  assert.deepEqual(parseRules("{nope").problems, [".vaultmind.json isn't valid JSON, so it's ignored."]);
});
