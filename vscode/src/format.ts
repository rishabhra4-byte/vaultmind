/** Turning answers into something to show: numbered citations, Markdown for reports, and a
 *  small, safe HTML renderer for the chat panel. No VS Code imports. */

import type { DocCard, Reply } from "./client";

export const CITE = /\s?\[D(\d+)(?:\.(\d+))?\]/g;

export interface Numbered {
  /** Document id → its number in this answer (1, 2, …), in order of first citation. */
  order: Map<number, number>;
  sources: { n: number; id: number; title: string; passages: number[] }[];
}

export function numberCitations(text: string, citations: DocCard[] = []): Numbered {
  const titles = new Map(citations.map((c) => [c.id, c.title]));
  const order = new Map<number, number>();
  const passages = new Map<number, Set<number>>();
  for (const m of text.matchAll(CITE)) {
    const id = Number(m[1]);
    if (!order.has(id)) order.set(id, order.size + 1);
    if (m[2] !== undefined) (passages.get(id) ?? passages.set(id, new Set()).get(id)!).add(Number(m[2]));
  }
  // Sources the answer used without an inline marker still get listed, after the cited ones.
  for (const c of citations) if (!order.has(c.id)) order.set(c.id, order.size + 1);
  for (const c of citations) for (const p of c.passages ?? []) (passages.get(c.id) ?? passages.set(c.id, new Set()).get(c.id)!).add(p);
  const sources = [...order.entries()].map(([id, n]) => ({
    n,
    id,
    title: titles.get(id) ?? `Document ${id}`,
    passages: [...(passages.get(id) ?? [])].sort((a, b) => a - b),
  }));
  return { order, sources };
}

export function modeLabel(reply: Reply): string {
  const m = reply.meta;
  if (m.mode === "verified" && m.verified) return `Verified by ${m.verified.by} for ${m.verified.team}`;
  if (m.mode === "ai") return "AI answer from your documents";
  if (m.mode === "offline") return "Matching passages (no AI)";
  return "From VaultMind";
}

/** An answer as Markdown, for a report: citations become numbered links to the paragraph. */
export function toMarkdown(reply: Reply, link: (id: number, passage?: number) => string): string {
  const { order, sources } = numberCitations(reply.content, reply.meta.citations);
  const body = reply.content.replace(CITE, (_m, id: string, p?: string) => {
    const n = order.get(Number(id));
    return n ? ` [[${n}]](${link(Number(id), p !== undefined ? Number(p) : undefined)})` : "";
  });
  const list = sources.map((s) => `${s.n}. [${s.title}](${link(s.id)})`).join("\n");
  return list ? `${body.trim()}\n\nSources:\n\n${list}` : body.trim();
}

// ---------------------------------------------------------------- HTML for the chat panel

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

function inline(s: string, cite: (id: number, p?: number) => string): string {
  return escapeHtml(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\s?\[D(\d+)(?:\.(\d+))?\]/g, (_m, id: string, p?: string) => cite(Number(id), p !== undefined ? Number(p) : undefined));
}

/** A tiny Markdown subset (paragraphs, lists, quotes, code blocks, bold, code) to HTML. All text
 *  is escaped; the only markup comes from here. Citations become numbered buttons. */
export function toHtml(text: string, order: Map<number, number>): string {
  const cite = (id: number, p?: number) => {
    const n = order.get(id);
    return n ? `<button class="cite" data-doc="${id}"${p !== undefined ? ` data-p="${p}"` : ""} title="Open source ${n}">${n}</button>` : "";
  };
  const out: string[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++;
      out.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
    } else if (/^\s*[-*] /.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*] /.test(lines[i])) items.push(`<li>${inline(lines[i++].replace(/^\s*[-*] /, ""), cite)}</li>`);
      out.push(`<ul>${items.join("")}</ul>`);
    } else if (/^\s*\d+[.)] /.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)] /.test(lines[i])) items.push(`<li>${inline(lines[i++].replace(/^\s*\d+[.)] /, ""), cite)}</li>`);
      out.push(`<ol>${items.join("")}</ol>`);
    } else if (line.startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) quote.push(lines[i++].replace(/^>\s?/, ""));
      out.push(`<blockquote>${inline(quote.join(" "), cite)}</blockquote>`);
    } else if (/^#{1,6} /.test(line)) {
      out.push(`<p><strong>${inline(line.replace(/^#{1,6} /, ""), cite)}</strong></p>`);
      i++;
    } else if (!line.trim()) {
      i++;
    } else {
      const para: string[] = [];
      while (i < lines.length && lines[i].trim() && !/^(\s*[-*] |\s*\d+[.)] |>|```|#{1,6} )/.test(lines[i])) para.push(lines[i++]);
      out.push(`<p>${inline(para.join(" "), cite)}</p>`);
    }
  }
  return out.join("");
}
