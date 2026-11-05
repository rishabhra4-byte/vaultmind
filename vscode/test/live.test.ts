/** Against a real VaultMind (VAULTMIND_URL, with demo data): sign in by code, ask, stream,
 *  find documents, run a quest. Skipped when VAULTMIND_URL isn't set. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { Client, VaultMindError } from "../src/client";
import { parseQuest, report, runQuest } from "../src/quest";

const BASE = process.env.VAULTMIND_URL;

async function approveAs(email: string, code: string) {
  const r = await fetch(`${BASE}/api/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password: "demo1234" }) });
  const { access_token } = (await r.json()) as { access_token: string };
  const d = await fetch(`${BASE}/api/auth/device/${code}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${access_token}` },
    body: JSON.stringify({ allow: true }),
  });
  assert.equal(d.status, 200);
}

test("the extension's client against a running server", { skip: !BASE }, async (t) => {
  const client = new Client(BASE!);
  let signedIn: Awaited<ReturnType<Client["waitForSignIn"]>> = null;

  await t.test("signs in by code, approved in the browser", async () => {
    const start = await client.startSignIn("VS Code");
    assert.match(start.user_code, /^[A-Z]{4}-[A-Z]{4}$/);
    setTimeout(() => approveAs("priya@northwind.example", start.user_code), 1500);
    signedIn = await client.waitForSignIn({ ...start, interval: 1 }, () => false);
    assert.ok(signedIn);
    client.token = signedIn.token;
    assert.equal((await client.me()).full_name, "Priya Sharma");
  });

  await t.test("asks, with citations from documents Priya can open", async () => {
    let streamed = "";
    const out = await client.ask("How do we handle a production incident?", { onText: (s) => (streamed += s) });
    assert.ok(out.reply.content.length > 20);
    assert.ok((out.reply.meta.citations ?? []).some((c) => c.title === "Incident Response Runbook"), JSON.stringify(out.reply.meta.citations));
    assert.ok(["ai", "offline", "verified"].includes(out.reply.meta.mode ?? ""));
    if (out.reply.meta.mode === "ai") assert.ok(streamed.length > 0);
  });

  await t.test("finds the project's documents, without ones she can't open", async () => {
    const docs = await client.documents("incident on-call runbook");
    assert.ok(docs.some((d) => d.title === "Incident Response Runbook"));
    assert.ok(!docs.some((d) => d.title === "Salary Bands 2026"));
  });

  await t.test("runs a quest into a cited report", async () => {
    const quest = parseQuest("# On-call\n\n## Questions\n\n- How do we handle a production incident?\n- What dental insurance do we have?\n");
    const findings = await runQuest(client, quest);
    const text = report(quest, findings, client);
    assert.match(text, /## 1\. How do we handle a production incident\?/);
    assert.match(text, /\/library\/\d+/);
    assert.match(text, /\| 2 \| What dental insurance do we have\? \| Not enough in the documents \|/);
  });

  await t.test("signing out ends the session", async () => {
    await client.signOut();
    await assert.rejects(client.me(), (e: unknown) => e instanceof VaultMindError && e.status === 401);
  });
});
