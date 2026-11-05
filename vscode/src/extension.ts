import * as vscode from "vscode";
import { Client, Filters, MAX_MESSAGE, User, VaultMindError, type DocumentSummary } from "./client";
import { modeLabel, numberCitations, toHtml } from "./format";
import { parseRules, projectQuery, type Rules } from "./project";
import { parseQuest, report, runQuest, template } from "./quest";

const DEMO = "https://vaultmind-demo.onrender.com";
const LOCAL = "http://localhost:5173";

let client: Client;
let user: User | null = null;
let secrets: vscode.SecretStorage;
let status: vscode.StatusBarItem;
let chat: ChatView;
let docs: ProjectDocs;

const serverUrl = () => (vscode.workspace.getConfiguration("vaultmind").get<string>("serverUrl") || LOCAL).replace(/\/+$/, "");
const tokenKey = () => `vaultmind.token:${serverUrl()}`;

export async function activate(context: vscode.ExtensionContext) {
  secrets = context.secrets;
  const version = (context.extension.packageJSON as { version: string }).version;
  client = new Client(serverUrl(), null, `VaultMind-VSCode/${version} (${process.platform === "win32" ? "Windows" : process.platform === "darwin" ? "Mac OS X" : "Linux"})`);

  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 50);
  status.command = "vaultmind.signIn";
  status.show();

  chat = new ChatView(context.extensionUri);
  docs = new ProjectDocs();
  context.subscriptions.push(
    status,
    vscode.window.registerWebviewViewProvider("vaultmind.chat", chat, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.window.registerTreeDataProvider("vaultmind.projectDocs", docs),
    vscode.commands.registerCommand("vaultmind.signIn", signIn),
    vscode.commands.registerCommand("vaultmind.signOut", signOut),
    vscode.commands.registerCommand("vaultmind.newChat", () => chat.reset()),
    vscode.commands.registerCommand("vaultmind.askAboutSelection", askAboutSelection),
    vscode.commands.registerCommand("vaultmind.searchDocs", searchDocs),
    vscode.commands.registerCommand("vaultmind.refreshProjectDocs", () => docs.refresh()),
    vscode.commands.registerCommand("vaultmind.newQuest", newQuest),
    vscode.commands.registerCommand("vaultmind.runQuest", runQuestCommand),
    vscode.commands.registerCommand("vaultmind.openWebsite", () => vscode.env.openExternal(vscode.Uri.parse(client.link("/")))),
    vscode.commands.registerCommand("vaultmind.openDocument", (id: number, passage?: number) =>
      vscode.env.openExternal(vscode.Uri.parse(client.documentLink(id, passage))),
    ),
    vscode.workspace.onDidChangeConfiguration(async (e) => {
      if (e.affectsConfiguration("vaultmind.serverUrl")) await restore();
    }),
    vscode.workspace.onDidSaveTextDocument((d) => {
      if (d.fileName.endsWith(".vaultmind.json")) docs.refresh();
    }),
  );
  await restore();
}

export function deactivate() {}

// ---------------------------------------------------------------- signing in

async function restore() {
  client.base = serverUrl();
  client.token = (await secrets.get(tokenKey())) ?? null;
  user = null;
  if (client.token) {
    try {
      user = await client.me();
    } catch (err) {
      if (err instanceof VaultMindError && err.status === 401) await secrets.delete(tokenKey());
      client.token = null;
    }
  }
  signedInChanged();
}

function signedInChanged() {
  vscode.commands.executeCommand("setContext", "vaultmind.signedIn", !!user);
  status.text = user ? `$(book) VaultMind: ${user.full_name}` : "$(sign-in) VaultMind: sign in";
  status.tooltip = user ? `Signed in to ${client.base} as ${user.email}` : `Sign in to ${client.base}`;
  status.command = user ? "vaultmind.openWebsite" : "vaultmind.signIn";
  chat.post({ type: "state", signedIn: !!user, name: user?.full_name ?? "", server: client.base });
  docs.refresh();
}

async function signIn() {
  const current = serverUrl();
  const pick = await vscode.window.showQuickPick(
    [
      { label: current, description: "current address" },
      ...(current !== LOCAL ? [{ label: LOCAL, description: "run from VS Code (Ctrl+Shift+B)" }] : []),
      ...(current !== DEMO ? [{ label: DEMO, description: "public demo" }] : []),
      { label: "Another address…", description: "your company's VaultMind" },
    ],
    { title: "Sign in to which VaultMind?" },
  );
  if (!pick) return;
  let url = pick.label;
  if (url === "Another address…") {
    const typed = await vscode.window.showInputBox({ prompt: "VaultMind address", placeHolder: "https://vaultmind.example.com", value: current });
    if (!typed) return;
    url = typed.trim();
  }
  if (url.replace(/\/+$/, "") !== current) {
    await vscode.workspace.getConfiguration("vaultmind").update("serverUrl", url, vscode.ConfigurationTarget.Global);
    client.base = url.replace(/\/+$/, "");
  }
  let start;
  try {
    start = await client.startSignIn("VS Code");
  } catch (err) {
    vscode.window.showErrorMessage((err as Error).message);
    return;
  }
  const page = client.link(`/connect?code=${start.user_code}`);
  await vscode.env.clipboard.writeText(start.user_code);
  await vscode.env.openExternal(vscode.Uri.parse(page));
  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: `VaultMind: approve code ${start.user_code} in your browser (copied). Waiting…`,
      cancellable: true,
    },
    async (_p, cancel) => {
      try {
        const got = await client.waitForSignIn(start, () => cancel.isCancellationRequested);
        if (!got) return;
        client.token = got.token;
        user = got.user;
        await secrets.store(tokenKey(), got.token);
        signedInChanged();
        vscode.window.showInformationMessage(`Signed in to VaultMind as ${got.user.full_name}.`);
      } catch (err) {
        vscode.window.showErrorMessage((err as Error).message);
      }
    },
  );
}

async function signOut() {
  try {
    if (client.token) await client.signOut();
  } catch {
    /* already ended on the server */
  }
  await secrets.delete(tokenKey());
  client.token = null;
  user = null;
  chat.reset();
  signedInChanged();
}

/** Run something that needs a session; sign-in problems are explained, not thrown. */
async function signedIn(): Promise<boolean> {
  if (user) return true;
  const go = await vscode.window.showInformationMessage("Sign in to VaultMind first.", "Sign in");
  if (go) await signIn();
  return !!user;
}

function handleError(err: unknown) {
  if (err instanceof VaultMindError && err.status === 401) {
    secrets.delete(tokenKey());
    client.token = null;
    user = null;
    signedInChanged();
  }
  return err instanceof Error ? err.message : String(err);
}

// ---------------------------------------------------------------- project rules

async function rules(): Promise<Rules> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) return parseRules(undefined);
  try {
    const bytes = await vscode.workspace.fs.readFile(vscode.Uri.joinPath(folder.uri, ".vaultmind.json"));
    const r = parseRules(Buffer.from(bytes).toString("utf8"));
    if (r.problems.length) vscode.window.showWarningMessage(`.vaultmind.json: ${r.problems.join(" ")}`);
    return r;
  } catch {
    return parseRules(undefined);
  }
}

// ---------------------------------------------------------------- the chat panel

type ToWebview =
  | { type: "state"; signedIn: boolean; name: string; server: string }
  | { type: "user"; text: string }
  | { type: "delta"; text: string }
  | { type: "answer"; html: string; label: string; sources: { n: number; id: number; title: string; passages: number[] }[]; locked: { id: number; title: string }[]; notEnough: boolean; scope?: string }
  | { type: "error"; text: string }
  | { type: "clear" }
  | { type: "busy"; busy: boolean };

class ChatView implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private conversationId?: number;
  private abort?: AbortController;

  constructor(private extensionUri: vscode.Uri) {}

  resolveWebviewView(view: vscode.WebviewView) {
    this.view = view;
    const media = vscode.Uri.joinPath(this.extensionUri, "media");
    view.webview.options = { enableScripts: true, localResourceRoots: [media] };
    const nonce = [...Array(32)].map(() => Math.floor(Math.random() * 36).toString(36)).join("");
    const css = view.webview.asWebviewUri(vscode.Uri.joinPath(media, "chat.css"));
    const js = view.webview.asWebviewUri(vscode.Uri.joinPath(media, "chat.js"));
    view.webview.html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${view.webview.cspSource}; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="${css}"><title>VaultMind</title></head>
<body><main id="log" aria-live="polite"></main>
<form id="ask"><textarea id="q" rows="3" maxlength="${MAX_MESSAGE}" placeholder="Ask your company's documents…" aria-label="Question"></textarea>
<div class="bar"><span id="who"></span><button id="stop" type="button" hidden>Stop</button><button id="send" type="submit">Ask</button></div></form>
<script nonce="${nonce}" src="${js}"></script></body></html>`;
    view.webview.onDidReceiveMessage((m) => this.receive(m));
    this.post({ type: "state", signedIn: !!user, name: user?.full_name ?? "", server: client.base });
  }

  post(m: ToWebview) {
    this.view?.webview.postMessage(m);
  }

  reset() {
    this.abort?.abort();
    this.conversationId = undefined;
    this.post({ type: "clear" });
  }

  reveal() {
    this.view?.show?.(true);
    return vscode.commands.executeCommand("vaultmind.chat.focus");
  }

  private async receive(m: { type: string; text?: string; id?: number; p?: number }) {
    if (m.type === "ask" && m.text) await this.ask(m.text);
    else if (m.type === "stop") this.abort?.abort();
    else if (m.type === "open" && typeof m.id === "number") vscode.commands.executeCommand("vaultmind.openDocument", m.id, m.p);
    else if (m.type === "signIn") vscode.commands.executeCommand("vaultmind.signIn");
  }

  async ask(text: string, shown = text) {
    if (!(await signedIn())) return;
    this.post({ type: "user", text: shown });
    this.post({ type: "busy", busy: true });
    this.abort = new AbortController();
    try {
      const filters: Filters = (await rules()).filters;
      const out = await client.ask(text, {
        conversationId: this.conversationId,
        filters,
        signal: this.abort.signal,
        onText: (t) => this.post({ type: "delta", text: t }),
      });
      this.conversationId = out.conversation.id;
      const reply = out.reply;
      const numbered = numberCitations(reply.content, reply.meta.citations);
      this.post({
        type: "answer",
        html: toHtml(reply.content, numbered.order),
        label: modeLabel(reply),
        sources: numbered.sources,
        locked: reply.meta.locked ?? [],
        notEnough: reply.meta.evidence?.enough === false,
        scope: reply.meta.search?.scope,
      });
    } catch (err) {
      if ((err as Error).name === "AbortError") this.post({ type: "error", text: "Stopped." });
      else this.post({ type: "error", text: handleError(err) });
    } finally {
      this.post({ type: "busy", busy: false });
    }
  }
}

// ---------------------------------------------------------------- ask about a selection

async function askAboutSelection() {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.selection.isEmpty) return;
  if (!(await signedIn())) return;
  const question = await vscode.window.showInputBox({
    title: "Ask VaultMind about the selection",
    value: "Does this follow our company's guidelines?",
    prompt: "Answered from your company's documents, with the selection as context.",
  });
  if (!question) return;
  const selected = editor.document.getText(editor.selection);
  const lang = editor.document.languageId;
  const room = MAX_MESSAGE - question.length - 40;
  const excerpt = selected.length > room ? selected.slice(0, room) + "\n…" : selected;
  await chat.reveal();
  await chat.ask(`${question}\n\n\`\`\`${lang}\n${excerpt}\n\`\`\``, `${question}\n(${selected.split("\n").length} selected lines of ${lang})`);
}

// ---------------------------------------------------------------- documents for this project

class DocItem extends vscode.TreeItem {
  constructor(public doc: DocumentSummary) {
    super(doc.title, vscode.TreeItemCollapsibleState.None);
    this.description = `${doc.owner_team} · ${doc.classification}`;
    this.tooltip = new vscode.MarkdownString(`**${doc.title}**\n\n${doc.summary}\n\n_${doc.owner_team}, ${doc.classification}, updated ${doc.updated_at.slice(0, 10)}_`);
    this.iconPath = new vscode.ThemeIcon("book");
    this.command = { command: "vaultmind.openDocument", title: "Open", arguments: [doc.id] };
  }
}

class ProjectDocs implements vscode.TreeDataProvider<vscode.TreeItem> {
  private changed = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changed.event;
  private items: vscode.TreeItem[] | null = null;

  refresh() {
    this.items = null;
    this.changed.fire();
  }

  getTreeItem(item: vscode.TreeItem) {
    return item;
  }

  async getChildren(): Promise<vscode.TreeItem[]> {
    if (!user) return [];
    if (this.items) return this.items;
    const folder = vscode.workspace.workspaceFolders?.[0];
    const read = async (name: string) => {
      if (!folder) return undefined;
      try {
        return Buffer.from(await vscode.workspace.fs.readFile(vscode.Uri.joinPath(folder.uri, name))).toString("utf8");
      } catch {
        return undefined;
      }
    };
    const folders = folder
      ? (await vscode.workspace.fs.readDirectory(folder.uri)).filter(([, t]) => t === vscode.FileType.Directory).map(([n]) => n)
      : [];
    const r = await rules();
    const query = projectQuery({ readme: (await read("README.md")) ?? (await read("readme.md")), packageJson: await read("package.json"), folders }, r.topics);
    if (!query) {
      this.items = [new vscode.TreeItem("Open a project folder to see its documents.")];
      return this.items;
    }
    try {
      const found = await client.documents(query, 12);
      const head = new vscode.TreeItem(`Matched on: ${query}`);
      head.iconPath = new vscode.ThemeIcon("info");
      head.tooltip = "Set \"topics\" in .vaultmind.json at the project root to choose what this list searches for.";
      this.items = found.length ? [head, ...found.map((d) => new DocItem(d))] : [head, new vscode.TreeItem("No documents matched.")];
    } catch (err) {
      this.items = [new vscode.TreeItem(handleError(err))];
    }
    return this.items;
  }
}

async function searchDocs() {
  if (!(await signedIn())) return;
  const q = await vscode.window.showInputBox({ title: "Search company documents", placeHolder: "e.g. expense limits" });
  if (!q) return;
  try {
    const found = await client.documents(q, 20);
    const pick = await vscode.window.showQuickPick(
      found.map((d) => ({ label: d.title, description: `${d.owner_team} · ${d.classification}`, detail: d.summary, id: d.id })),
      { title: found.length ? `${found.length} documents` : "Nothing matched", matchOnDetail: true },
    );
    if (pick) vscode.commands.executeCommand("vaultmind.openDocument", pick.id);
  } catch (err) {
    vscode.window.showErrorMessage(handleError(err));
  }
}

// ---------------------------------------------------------------- research quests

async function newQuest() {
  const folder = vscode.workspace.workspaceFolders?.[0];
  const title = await vscode.window.showInputBox({ title: "New research quest", prompt: "What is it about?", value: "Backend on-call readiness" });
  if (!title) return;
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "research";
  if (folder) {
    const uri = vscode.Uri.joinPath(folder.uri, "vaultmind-quests", `${slug}.quest.md`);
    await vscode.workspace.fs.writeFile(uri, Buffer.from(template(title), "utf8"));
    await vscode.window.showTextDocument(uri);
  } else {
    const doc = await vscode.workspace.openTextDocument({ language: "markdown", content: template(title) });
    await vscode.window.showTextDocument(doc);
  }
}

async function runQuestCommand(uri?: vscode.Uri) {
  const doc = uri ? await vscode.workspace.openTextDocument(uri) : vscode.window.activeTextEditor?.document;
  if (!doc) return;
  if (!(await signedIn())) return;
  if (doc.isDirty) await doc.save();
  const quest = parseQuest(doc.getText());
  if (!quest.questions.length) {
    vscode.window.showWarningMessage("Write at least one question under “## Questions”, as a bulleted list.");
    return;
  }
  const r = await rules();
  const findings = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `Research quest: ${quest.title}`, cancellable: true },
    (progress, cancel) =>
      runQuest(client, quest, {
        filters: r.filters,
        cancelled: () => cancel.isCancellationRequested,
        onProgress: (done, total, q) => progress.report({ message: `${done + 1}/${total}: ${q}`, increment: done ? 100 / total : 0 }),
      }),
  );
  const text = report(quest, findings, client);
  const target = doc.isUntitled ? undefined : vscode.Uri.file(doc.fileName.replace(/\.quest\.md$/, ".report.md"));
  if (target) {
    await vscode.workspace.fs.writeFile(target, Buffer.from(text, "utf8"));
    await vscode.commands.executeCommand("markdown.showPreviewToSide", target);
  } else {
    await vscode.window.showTextDocument(await vscode.workspace.openTextDocument({ language: "markdown", content: text }), vscode.ViewColumn.Beside);
  }
  const answered = findings.filter((f) => !f.error && f.result?.reply.meta.evidence?.enough !== false).length;
  vscode.window.showInformationMessage(`Research quest done: ${answered} of ${findings.length} questions answered from the documents.`);
}
