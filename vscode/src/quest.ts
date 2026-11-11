/** Research quests: a brief and a list of questions in a `*.quest.md` file. VaultMind answers
 *  each from the company's documents and writes a report with every claim cited, saying
 *  plainly where the documents didn't hold enough. No VS Code imports. */

import type { ChatResult, Client, Filters } from "./client";
import { modeLabel, toMarkdown } from "./format";

export interface Quest {
  title: string;
  brief: string;
  questions: string[];
}

export function template(title: string): string {
  return `# ${title}

<!-- A research quest. Write the brief and the questions, then press ▶ "Run this research quest"
     in the editor's title bar. VaultMind answers each question from the company documents you can
     open and writes a cited report next to this file. -->

## Brief

What this research is for, in a sentence or two.

## Questions

- How do we handle a production incident?
- Who approves expenses above the manager's limit?
`;
}

export function parseQuest(text: string, fallbackTitle = "Research quest"): Quest {
  const clean = text.replace(/<!--[\s\S]*?-->/g, "");
  const title = clean.match(/^#\s+(.+)$/m)?.[1].trim() ?? fallbackTitle;
  const section = (name: string) => {
    const m = clean.match(new RegExp(`^##\\s+${name}\\s*$([\\s\\S]*?)(?=^##\\s|$(?![\\s\\S]))`, "mi"));
    return m ? m[1].trim() : "";
  };
  const brief = section("Brief");
  const listed = section("Questions") || clean;
  const questions = listed
    .split(/\r?\n/)
    .map((l) => l.match(/^\s*(?:[-*]|\d+[.)])\s+(.+)$/)?.[1].trim())
    .filter((q): q is string => !!q && q.length > 2);
  return { title, brief, questions: questions.length ? questions : brief ? [brief] : [] };
}

export interface Finding {
  question: string;
  result?: ChatResult;
  error?: string;
}

export async function runQuest(
  client: Client,
  quest: Quest,
  opts: { filters?: Filters; onProgress?: (done: number, total: number, question: string) => void; cancelled?: () => boolean } = {},
): Promise<Finding[]> {
  const findings: Finding[] = [];
  for (const [i, question] of quest.questions.entries()) {
    if (opts.cancelled?.()) break;
    opts.onProgress?.(i, quest.questions.length, question);
    try {
      findings.push({ question, result: await client.ask(question, { filters: opts.filters }) });
    } catch (err) {
      findings.push({ question, error: (err as Error).message });
    }
  }
  return findings;
}

const enough = (f: Finding) => !f.error && f.result?.reply.meta.evidence?.enough !== false && !!f.result?.reply.meta.citations?.length;

export function report(quest: Quest, findings: Finding[], client: Client, when = new Date()): string {
  const link = (id: number, p?: number) => client.documentLink(id, p);
  const answered = findings.filter(enough).length;
  const lines = [
    `# ${quest.title}: report`,
    "",
    `Researched by VaultMind on ${when.toISOString().slice(0, 16).replace("T", " ")} UTC, from the documents you can open at ${client.base}.`,
    quest.brief ? `\n> ${quest.brief.replace(/\n+/g, " ")}` : "",
    "",
    `**${answered} of ${findings.length}** questions answered from the documents.`,
    "",
    "| # | Question | Result |",
    "|---|---|---|",
    ...findings.map((f, i) => `| ${i + 1} | ${f.question.replace(/\|/g, "\\|")} | ${f.error ? "Failed" : enough(f) ? "Answered" : "Not enough in the documents"} |`),
    "",
  ];
  findings.forEach((f, i) => {
    lines.push(`## ${i + 1}. ${f.question}`, "");
    if (f.error) {
      lines.push(`_Couldn't ask this: ${f.error}_`, "");
      return;
    }
    const reply = f.result!.reply;
    lines.push(`_${modeLabel(reply)}${enough(f) ? "" : ". The documents didn't hold enough to answer this fully"}._`, "");
    lines.push(toMarkdown(reply, link), "");
    const locked = reply.meta.locked ?? [];
    if (locked.length) lines.push(`Documents you can't open that may help (request access): ${locked.map((l) => `[${l.title}](${link(l.id)})`).join(", ")}`, "");
  });
  return lines.filter((l, i, a) => !(l === "" && a[i - 1] === "")).join("\n").trim() + "\n";
}
