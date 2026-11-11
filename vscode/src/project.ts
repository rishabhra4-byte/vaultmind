/** What the open project is about (to find its company documents), and the project's VaultMind
 *  rules file. No VS Code imports. */

import type { Filters } from "./client";

const STOP = new Set(
  (
    "a an and are as at be by for from has have in is it its of on or that the this to was were will with your you our we " +
    "src lib dist build out test tests docs node_modules vendor bin tmp public assets static scripts app apps packages " +
    "readme license changelog main index config setup install usage project repository repo code file files run using use"
  ).split(" "),
);
const IGNORED_FOLDERS = new Set([".git", ".github", ".vscode", ".idea", "node_modules", "dist", "build", "out", "coverage", "__pycache__", ".venv", "venv"]);

export interface ProjectInfo {
  readme?: string;
  packageJson?: string;
  folders?: string[];
}

function words(text: string): string[] {
  return (text.toLowerCase().match(/[a-z][a-z0-9-]{2,}/g) ?? []).filter((w) => !STOP.has(w));
}

/** A search for the company documents that matter to this project: its rules' topics if set,
 *  otherwise words from its README title and first paragraph, its package description and its
 *  top-level folders, most frequent first. */
export function projectQuery(info: ProjectInfo, topics: string[] = []): string {
  if (topics.length) return topics.join(" ").slice(0, 300);
  const counts = new Map<string, number>();
  const add = (ws: string[], weight: number) => ws.forEach((w) => counts.set(w, (counts.get(w) ?? 0) + weight));
  if (info.readme) {
    const text = info.readme.replace(/```[\s\S]*?```/g, " ").replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1");
    const title = text.match(/^#\s+(.+)$/m)?.[1] ?? "";
    const firstPara = text.split(/\n\s*\n/).find((p) => p.trim() && !p.trim().startsWith("#") && !p.trim().startsWith("<")) ?? "";
    add(words(title), 3);
    add(words(firstPara), 1);
  }
  if (info.packageJson) {
    try {
      const pkg = JSON.parse(info.packageJson) as { name?: string; description?: string; keywords?: string[] };
      add(words((pkg.name ?? "").replace(/^@[^/]+\//, "")), 2);
      add(words(pkg.description ?? ""), 1);
      add(words((pkg.keywords ?? []).join(" ")), 2);
    } catch {
      /* not JSON */
    }
  }
  add(words((info.folders ?? []).filter((f) => !IGNORED_FOLDERS.has(f) && !f.startsWith(".")).join(" ")), 1);
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 10)
    .map(([w]) => w)
    .join(" ");
}

export interface Rules {
  filters: Filters;
  topics: string[];
  problems: string[];
}

const CLASSES = ["public", "internal", "confidential", "restricted"];

/** `.vaultmind.json` at the project root: which documents to answer from, and what the project
 *  is about. Unknown or wrong settings are reported, never sent. */
export function parseRules(text: string | undefined): Rules {
  const rules: Rules = { filters: {}, topics: [], problems: [] };
  if (!text?.trim()) return rules;
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    rules.problems.push(".vaultmind.json isn't valid JSON, so it's ignored.");
    return rules;
  }
  const r = raw as { filters?: Record<string, unknown>; topics?: unknown };
  const f = r.filters ?? {};
  const int = (v: unknown) => (typeof v === "number" && Number.isInteger(v) && v > 0 ? v : undefined);
  if (f.team_id !== undefined) int(f.team_id) ? (rules.filters.team_id = f.team_id as number) : rules.problems.push("filters.team_id must be a team's number.");
  if (f.owner_id !== undefined) int(f.owner_id) ? (rules.filters.owner_id = f.owner_id as number) : rules.problems.push("filters.owner_id must be a person's number.");
  if (f.updated_within_days !== undefined)
    int(f.updated_within_days) && (f.updated_within_days as number) <= 3650
      ? (rules.filters.updated_within_days = f.updated_within_days as number)
      : rules.problems.push("filters.updated_within_days must be 1 to 3650.");
  if (f.classification !== undefined)
    CLASSES.includes(f.classification as string)
      ? (rules.filters.classification = f.classification as Filters["classification"])
      : rules.problems.push(`filters.classification must be one of ${CLASSES.join(", ")}.`);
  if (f.source !== undefined)
    typeof f.source === "string" && /^(uploaded|\d+)$/.test(f.source)
      ? (rules.filters.source = f.source)
      : rules.problems.push('filters.source must be "uploaded" or a source\'s number.');
  for (const k of Object.keys(f)) if (!["team_id", "owner_id", "updated_within_days", "classification", "source"].includes(k)) rules.problems.push(`Unknown filter "${k}".`);
  if (r.topics !== undefined) {
    if (Array.isArray(r.topics) && r.topics.every((t) => typeof t === "string")) rules.topics = (r.topics as string[]).map((t) => t.trim()).filter(Boolean).slice(0, 20);
    else rules.problems.push("topics must be a list of words or phrases.");
  }
  return rules;
}
