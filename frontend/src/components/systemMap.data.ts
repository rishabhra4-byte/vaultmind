import type { SVGProps } from "react";
import {
  IconActivity,
  IconBell,
  IconBook,
  IconChart,
  IconChat,
  IconChecklist,
  IconCloud,
  IconColumns,
  IconCompass,
  IconFile,
  IconFilter,
  IconHistory,
  IconKey,
  IconLock,
  IconSearch,
  IconSend,
  IconShield,
  IconSiren,
  IconSparkle,
  IconUpload,
  IconUsers,
  IconAlert,
} from "./icons";

/** What the "How VaultMind works" map shows. Every statement here describes what the code
 *  does; `code` points at where, in the public repository. */

export type Icon = (p: SVGProps<SVGSVGElement>) => JSX.Element;

export type Lane = { id: string; title: string; sub: string };

export type MapNode = {
  id: string;
  lane: string;
  row: number;
  title: string;
  sub: string;
  icon: Icon;
  optional?: boolean;
  body: string;
  input: string;
  output: string;
  safe?: string;
  fails?: string;
  code: string;
};

export type MapEdge = {
  from: string;
  to: string;
  label?: string;
  optional?: boolean;
  /** For edges between lanes that aren't next to each other: which gap between rows to run
   *  along (0 = above the first row, 4 = below the last). */
  corridor?: number;
};

export type RouteStep = {
  node: string;
  title: string;
  note: string;
  /** The connection that lights up on the way to this step, when it isn't from the previous one. */
  via?: [string, string];
};
export type Route = { id: string; label: string; summary: string; steps: RouteStep[] };

export const REPO = "https://github.com/rishabhra4-byte/vaultmind/blob/main/";

export const LANES: Lane[] = [
  { id: "people", title: "People & sources", sub: "where it starts" },
  { id: "intake", title: "Intake", sub: "every file, first" },
  { id: "access", title: "Access", sub: "before any search" },
  { id: "search", title: "Search", sub: "what they can open" },
  { id: "answer", title: "Answer", sub: "AI only if allowed" },
  { id: "out", title: "Out", sub: "what people see" },
];

export const NODES: MapNode[] = [
  // ---------------------------------------------------------------- people & sources
  {
    id: "ask",
    lane: "people",
    row: 0,
    title: "Question",
    sub: "web app or Slack",
    icon: IconChat,
    body: "Someone asks in plain words, in the app or in Slack (/vaultmind, a mention or a direct message). Slack answers use that person's own permissions.",
    input: "A question, and optional filters: team, owner, date, source",
    output: "The question, and who is asking",
    safe: "Answers in a Slack channel are shown only to the person who asked.",
    fails: "Too many questions too quickly: a rate limit asks them to wait a moment.",
    code: "backend/app/routers/assistant.py",
  },
  {
    id: "upload",
    lane: "people",
    row: 1,
    title: "Upload",
    sub: "drag and drop",
    icon: IconUpload,
    body: "A file is added from the library, with an owning team and a classification: public, internal, confidential or restricted.",
    input: "PDF, Word, Excel, PowerPoint, text, Markdown, CSV or JSON, up to 20 MB",
    output: "The file, its title and its classification",
    safe: "File names are cleaned, and downloads are always served as attachments with a safe type.",
    fails: "Unsupported, empty, scanned or old Office files get a plain explanation.",
    code: "backend/app/routers/documents.py",
  },
  {
    id: "sync",
    lane: "people",
    row: 2,
    title: "Drive & SharePoint",
    sub: "synced on a schedule",
    icon: IconCloud,
    body: "Google Drive, Microsoft 365 and shared folders are synced on a schedule. Each file's sharing (people, groups, the whole domain) is copied, so VaultMind shows exactly what the source would.",
    input: "New and changed files, with their sharing",
    output: "Files to read, and who may open each one",
    safe: "Files deleted at the source disappear here too.",
    fails: "A source whose sync keeps failing is flagged on Admin → Sources and raises an alert.",
    code: "backend/app/sync.py",
  },
  {
    id: "approver",
    lane: "people",
    row: 3,
    title: "Approver",
    sub: "owner or manager",
    icon: IconUsers,
    body: "Access requests go to the people with authority over that document: its team's managers and above, or an administrator.",
    input: "A request, with the reason the person gave",
    output: "Approved with an expiry date, or denied",
    safe: "Two approvers can't both decide the same request; the second is told it's already decided.",
    fails: "No decision yet: the request stays pending, and the person who asked can cancel it.",
    code: "backend/app/access_flow.py",
  },

  // ---------------------------------------------------------------- intake
  {
    id: "scan",
    lane: "intake",
    row: 0,
    title: "Malware scan",
    sub: "ClamAV",
    icon: IconSiren,
    body: "Every upload and every synced file goes through ClamAV before anything reads or stores it.",
    input: "The file's bytes",
    output: "Clean, or refused",
    safe: "If the scanner is down, files wait rather than slip through.",
    fails: "Something infected is refused and logged, and administrators are told.",
    code: "backend/app/malware.py",
  },
  {
    id: "read",
    lane: "intake",
    row: 1,
    title: "Read the file",
    sub: "PDF · Office files",
    icon: IconFile,
    body: "Text is pulled out with size limits: every Excel sheet with its column labels, every slide with its tables and speaker notes.",
    input: "A clean file",
    output: "Plain text",
    safe: "Zip bombs and XML entity tricks are refused before they can use up memory.",
    fails: "Scans with no text, old .doc/.xls/.ppt files and damaged files are explained, not guessed at.",
    code: "backend/app/extract.py",
  },
  {
    id: "sensitivity",
    lane: "intake",
    row: 2,
    title: "Sensitivity scan",
    sub: "IDs · cards · keys",
    icon: IconAlert,
    body: "Looks for PAN and Aadhaar numbers, card numbers, API keys, passwords, salary and finance language, and suggests a classification.",
    input: "The text, and the classification chosen",
    output: "A suggested classification",
    safe: "An upload labelled less sensitive than it looks is stopped until the uploader overrides it, and the override is logged.",
    fails: "Synced files under folder rules are raised to the suggested level automatically.",
    code: "backend/app/sensitivity.py",
  },
  {
    id: "index",
    lane: "intake",
    row: 3,
    title: "Passages",
    sub: "split and indexed",
    icon: IconColumns,
    body: "The text is split into passages of about 140 words and added to the search index, so each answer can cite the exact paragraph.",
    input: "Plain text",
    output: "Searchable passages",
    safe: "A summary is written by the AI only if the AI data policy allows it for this document.",
    fails: "With search by meaning on, vectors are added in the background; until then a passage is found by its words.",
    code: "backend/app/services.py",
  },

  // ---------------------------------------------------------------- access
  {
    id: "who",
    lane: "access",
    row: 0,
    title: "Who's asking",
    sub: "team · level · grants",
    icon: IconKey,
    body: "The person's team, position (Intern to Executive), groups, and any access they've been granted decide which documents are in scope.",
    input: "The signed-in person",
    output: "The documents they may open",
    safe: "Every decision comes with a reason in words, such as “needs Manager level or above”.",
    fails: "A session ended elsewhere (signed out, password changed, account deactivated) is refused before this step.",
    code: "backend/app/policy.py",
  },
  {
    id: "filter",
    lane: "access",
    row: 1,
    title: "Permission filter",
    sub: "before any search",
    icon: IconFilter,
    body: "Search, filters, spelling corrections and acronyms only ever see the documents this person may open.",
    input: "Every document",
    output: "Only the ones this person may open",
    safe: "Filters can narrow this set, never widen it. Checked over the benchmark's 229 questions: 0 leaks.",
    code: "backend/app/services.py",
  },
  {
    id: "titles",
    lane: "access",
    row: 2,
    title: "Locked titles",
    sub: "titles only",
    icon: IconLock,
    body: "Documents the person can't open, but may know exist, are matched on their titles only, never their contents, so searching can't probe what a locked file says.",
    input: "The question",
    output: "Titles of matching locked documents",
    safe: "Restricted documents are never shown, not even as a title.",
    code: "backend/app/search.py",
  },
  {
    id: "grant",
    lane: "access",
    row: 3,
    title: "Time-boxed access",
    sub: "expires by itself",
    icon: IconHistory,
    body: "An approved request opens one document for one person until its expiry date. It can be revoked at any time.",
    input: "An approval",
    output: "Access that counts from the next question",
    safe: "People are reminded before access expires, and expired access simply stops working.",
    code: "backend/app/access_flow.py",
  },

  // ---------------------------------------------------------------- search
  {
    id: "words",
    lane: "search",
    row: 0,
    title: "Words",
    sub: "typos · acronyms",
    icon: IconSearch,
    body: "Finds passages by their words (BM25), forgiving misspellings and word forms, and understanding the company glossary and acronyms the documents define.",
    input: "The question",
    output: "Candidate passages",
    safe: "Corrections and acronyms only come from documents this person can open.",
    code: "backend/app/search.py",
  },
  {
    id: "meaning",
    lane: "search",
    row: 1,
    title: "Meaning",
    sub: "optional",
    icon: IconCompass,
    optional: true,
    body: "Finds passages that say the same thing in other words. Off unless an embedding service is set up; one run inside the company keeps all text in.",
    input: "The question, masked",
    output: "Passages close in meaning",
    safe: "Only passages the AI may read are ever embedded.",
    fails: "If the service is down, search uses words alone.",
    code: "backend/app/embeddings.py",
  },
  {
    id: "evidence",
    lane: "search",
    row: 2,
    title: "Evidence check",
    sub: "enough to answer?",
    icon: IconChecklist,
    body: "Passages are reranked by how much of the question they cover, and the sentence that answers it is picked. If it doesn't cover enough, the reply says so and names the words no document mentions.",
    input: "Candidate passages",
    output: "The best passages, and whether they're enough",
    safe: "Saying “not enough evidence” is better than guessing.",
    code: "backend/app/retrieval.py",
  },
  {
    id: "conflicts",
    lane: "search",
    row: 3,
    title: "Conflicts & age",
    sub: "documents disagree",
    icon: IconChart,
    body: "Figures of the kind asked for (amounts, durations, rates, times) are compared across documents about the same thing. Both sides are shown, newest first, and old documents are flagged.",
    input: "The best passages",
    output: "Disagreements, and out-of-date warnings",
    code: "backend/app/facts.py",
  },

  // ---------------------------------------------------------------- answer
  {
    id: "policy",
    lane: "answer",
    row: 0,
    title: "AI data policy",
    sub: "what may be sent",
    icon: IconShield,
    body: "Only passages this person may open, from documents the AI may read, at or below the classification ceiling (restricted stays local by default), with ID numbers and secrets masked.",
    input: "The best passages",
    output: "What Claude may see",
    safe: "Owners can keep a document, or a whole source, away from the AI.",
    code: "backend/app/ai_policy.py",
  },
  {
    id: "claude",
    lane: "answer",
    row: 1,
    title: "Claude",
    sub: "streams the answer",
    icon: IconSparkle,
    body: "Writes the answer from the allowed passages, citing each fact, and streams it as it's written. Document text is treated as data, never as instructions.",
    input: "The question, the allowed passages, recent messages",
    output: "A cited answer",
    safe: "Daily limits keep the bill predictable; past them, answers are built offline and say why.",
    fails: "Slow, down or out of credit: the person gets an offline answer, with the reason.",
    code: "backend/app/llm.py",
  },
  {
    id: "offline",
    lane: "answer",
    row: 2,
    title: "Offline answer",
    sub: "no AI needed",
    icon: IconBook,
    body: "Built from the best-matching sentences, with the same citations, conflicts and evidence check. Used when there's no API key, a document is kept from the AI, or a limit is reached.",
    input: "The best passages",
    output: "An answer, labelled as offline",
    code: "backend/app/assistant.py",
  },
  {
    id: "cite",
    lane: "answer",
    row: 3,
    title: "Citation check",
    sub: "only what it saw",
    icon: IconChecklist,
    body: "Every citation in the answer is checked against the passages that were actually sent; anything else is dropped.",
    input: "The model's answer",
    output: "An answer whose sources are real",
    safe: "The model can't cite or quote a document the person can't open: it never saw one.",
    code: "backend/app/assistant.py",
  },

  // ---------------------------------------------------------------- out
  {
    id: "reply",
    lane: "out",
    row: 0,
    title: "Answer + sources",
    sub: "numbered, clickable",
    icon: IconSend,
    body: "Each source opens at the cited paragraph and shows its owner, version, last update and why this person may open it. People mark answers helpful or not.",
    input: "A checked answer",
    output: "What the person reads",
    code: "frontend/src/components/Bubble.tsx",
  },
  {
    id: "locked",
    lane: "out",
    row: 1,
    title: "Locked hint",
    sub: "request in a click",
    icon: IconLock,
    body: "Matching documents the person can't open are listed by title, with the reason, and a button to ask for access.",
    input: "Titles of locked matches",
    output: "A request, if the person wants one",
    code: "backend/app/assistant.py",
  },
  {
    id: "notify",
    lane: "out",
    row: 2,
    title: "Notifications",
    sub: "app · email · Slack",
    icon: IconBell,
    body: "Approvers hear about requests; people hear decisions, access about to expire, and changes to documents they've read.",
    input: "Requests, decisions, new versions",
    output: "A message where the person works",
    code: "backend/app/notify.py",
  },
  {
    id: "audit",
    lane: "out",
    row: 3,
    title: "Activity log",
    sub: "who saw what",
    icon: IconActivity,
    body: "Every open, refusal, question, upload and approval is recorded, so administrators can answer “who saw this?”.",
    input: "Everything above",
    output: "A searchable record for admins",
    code: "backend/app/audit.py",
  },
];

export const EDGES: MapEdge[] = [
  // a question
  { from: "ask", to: "who", label: "question", corridor: 0 },
  { from: "who", to: "filter" },
  { from: "filter", to: "words", label: "openable only" },
  { from: "filter", to: "meaning", optional: true },
  { from: "words", to: "evidence" },
  { from: "meaning", to: "evidence", optional: true },
  { from: "evidence", to: "conflicts" },
  { from: "evidence", to: "policy", label: "enough evidence" },
  { from: "policy", to: "claude", label: "masked" },
  { from: "policy", to: "offline", label: "no key / kept local", corridor: 2 },
  { from: "claude", to: "cite" },
  { from: "cite", to: "reply", corridor: 1 },
  { from: "offline", to: "reply", corridor: 1 },
  { from: "conflicts", to: "reply", label: "flags", corridor: 1 },
  // locked documents and access requests
  { from: "filter", to: "titles", label: "can't open" },
  { from: "titles", to: "locked", corridor: 2 },
  { from: "locked", to: "approver", label: "request access", corridor: 4 },
  { from: "approver", to: "grant", corridor: 4 },
  { from: "grant", to: "who", label: "next question" },
  { from: "approver", to: "notify", corridor: 3 },
  // files
  { from: "upload", to: "scan", label: "file" },
  { from: "sync", to: "scan", label: "changed files" },
  { from: "scan", to: "read", label: "clean" },
  { from: "read", to: "sensitivity" },
  { from: "sensitivity", to: "index" },
  { from: "index", to: "words", label: "searchable", corridor: 1 },
  { from: "index", to: "meaning", optional: true, corridor: 2 },
  { from: "sync", to: "filter", label: "sharing copied", corridor: 2 },
  { from: "sensitivity", to: "audit", corridor: 4 },
];

export const ROUTES: Route[] = [
  {
    id: "ask",
    label: "Ask a question",
    summary: "From a question to an answer with sources, looking only at what this person may open.",
    steps: [
      { node: "ask", title: "Ask", note: "Someone types a question in the app or in Slack." },
      { node: "who", title: "Who's asking", note: "Their team, level, groups and grants are looked up." },
      { node: "filter", title: "Filter", note: "Everything they can't open is set aside before searching." },
      { node: "words", title: "Search", note: "Passages that match the question's words, typos forgiven." },
      { node: "evidence", title: "Evidence", note: "The sentence that answers it, and whether it's enough." },
      { node: "policy", title: "Policy", note: "Only passages the AI may read go further, masked." },
      { node: "claude", title: "Claude", note: "The answer is written and streamed, citing each fact." },
      { node: "cite", title: "Check", note: "Citations of anything Claude wasn't given are dropped." },
      { node: "reply", title: "Answer", note: "The person reads it, with sources they can open." },
    ],
  },
  {
    id: "locked",
    label: "Can't open it",
    summary: "What happens when the answer is in a document the person isn't cleared for.",
    steps: [
      { node: "ask", title: "Ask", note: "An intern asks for the SDE-2 salary band." },
      { node: "who", title: "Who's asking", note: "Intern, Engineering: below Manager level." },
      { node: "filter", title: "Filter", note: "Salary Bands 2026 is confidential, so it's set aside." },
      { node: "titles", title: "Titles", note: "Its title matches; its contents are never searched." },
      { node: "locked", title: "Hint", note: "They see the title, the reason and a request button." },
      { node: "approver", title: "Approve", note: "The owning team's manager approves, with an expiry." },
      { node: "grant", title: "Access", note: "One document, for one person, until the date set." },
      { node: "who", title: "Ask again", note: "The next question can use that document." },
    ],
  },
  {
    id: "upload",
    label: "Upload a file",
    summary: "From a dropped file to searchable passages, with three checks on the way.",
    steps: [
      { node: "upload", title: "Upload", note: "A file is dropped in the library with a classification." },
      { node: "scan", title: "Scan", note: "ClamAV checks it before anything reads it." },
      { node: "read", title: "Read", note: "The text is pulled out: sheets, slides, pages." },
      { node: "sensitivity", title: "Sensitivity", note: "ID numbers or keys in a “public” file stop the upload." },
      { node: "index", title: "Passages", note: "Split into passages and indexed, ready to cite." },
      { node: "words", title: "Searchable", note: "Found by people allowed to open it, seconds later." },
    ],
  },
  {
    id: "sync",
    label: "Sync from Drive",
    summary: "Files from Google Drive or SharePoint, with the same sharing they have there.",
    steps: [
      { node: "sync", title: "Sync", note: "Changed files and their sharing are fetched on a schedule." },
      { node: "scan", title: "Scan", note: "Each file is scanned for malware first." },
      { node: "read", title: "Read", note: "Google Sheets arrive with every tab, Slides with notes." },
      { node: "sensitivity", title: "Sensitivity", note: "Under folder rules, a file that looks more sensitive is raised to that level." },
      { node: "index", title: "Passages", note: "Indexed like an upload." },
      {
        node: "filter",
        title: "Sharing",
        note: "Only the people, groups and domain it's shared with can find it.",
        via: ["sync", "filter"],
      },
    ],
  },
  {
    id: "offline",
    label: "Without AI",
    summary: "No API key, a document kept local, or the daily limit reached: the same rules, no model.",
    steps: [
      { node: "ask", title: "Ask", note: "The same question as before." },
      { node: "who", title: "Who's asking", note: "The same person, the same level." },
      { node: "filter", title: "Filter", note: "The same permission check, first." },
      { node: "words", title: "Search", note: "The same search." },
      { node: "evidence", title: "Evidence", note: "The same check that there's enough to answer." },
      { node: "policy", title: "Policy", note: "Nothing may go to the AI here, so nothing is sent." },
      { node: "offline", title: "Offline", note: "The answer is built from the best sentences." },
      { node: "reply", title: "Answer", note: "Labelled as offline, with the same sources." },
    ],
  },
];
