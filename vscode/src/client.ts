/** Talking to a VaultMind server: signing in by code, asking (streamed), and finding documents.
 *  No VS Code imports here, so it can be tested on its own against a real server. */

export interface User {
  id: number;
  email: string;
  full_name: string;
  team_name: string;
  is_admin: boolean;
}

export interface DocCard {
  id: number;
  title: string;
  team?: string;
  classification?: string;
  passages?: number[];
  snippet?: string;
}

export interface Reply {
  id: number;
  content: string;
  meta: {
    mode?: "ai" | "offline" | "direct" | "verified";
    citations?: DocCard[];
    locked?: { id: number; title: string }[];
    evidence?: { enough?: boolean | null; note?: string };
    verified?: { by: string; team: string; verified_at: string };
    fallback?: { reason: string; message: string };
    search?: { scope?: string };
  };
}

export interface ChatResult {
  conversation: { id: number; title: string };
  reply: Reply;
}

export interface Filters {
  team_id?: number;
  classification?: "public" | "internal" | "confidential" | "restricted";
  source?: string;
  owner_id?: number;
  updated_within_days?: number;
}

export interface DocumentSummary {
  id: number;
  title: string;
  owner_team: string;
  classification: string;
  summary: string;
  updated_at: string;
  access: { allowed: boolean };
}

export interface DeviceStart {
  device_code: string;
  user_code: string;
  expires_in: number;
  interval: number;
}

export class VaultMindError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export const MAX_MESSAGE = 2000; // the server's limit on a question

export class Client {
  constructor(
    public base: string,
    public token: string | null = null,
    private agent = "VaultMind-VSCode/0.1.0",
    private fetcher: typeof fetch = fetch,
  ) {
    this.base = base.replace(/\/+$/, "");
  }

  /** A page of the website, for opening in the browser. */
  link(path: string): string {
    return this.base + path;
  }

  documentLink(id: number, passage?: number): string {
    return this.link(`/library/${id}${passage !== undefined ? `#p${passage}` : ""}`);
  }

  private headers(json = true): Record<string, string> {
    const h: Record<string, string> = { "User-Agent": this.agent, Accept: "application/json" };
    if (json) h["Content-Type"] = "application/json";
    if (this.token) h.Authorization = `Bearer ${this.token}`;
    return h;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<{ status: number; data: T }> {
    let res: Response;
    try {
      res = await this.fetcher(this.base + path, {
        method,
        headers: this.headers(body !== undefined),
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (err) {
      throw new VaultMindError(0, `Can't reach VaultMind at ${this.base}. Is it running, and is the address right? (${(err as Error).message})`);
    }
    const text = await res.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = text;
    }
    if (res.status >= 400) throw new VaultMindError(res.status, errorText(data, res.status));
    return { status: res.status, data: data as T };
  }

  // ---------------------------------------------------------------- signing in

  async startSignIn(client = "VS Code"): Promise<DeviceStart> {
    return (await this.request<DeviceStart>("POST", "/api/auth/device", { client })).data;
  }

  /** Ask once whether the code was approved: the token and person when it was, null while waiting. */
  async pollSignIn(deviceCode: string): Promise<{ token: string; user: User } | null> {
    const { status, data } = await this.request<{ access_token: string; user: User }>("POST", "/api/auth/device/token", {
      device_code: deviceCode,
    });
    if (status === 202) return null;
    return { token: data.access_token, user: data.user };
  }

  /** Keep asking until approved, refused, expired or cancelled. */
  async waitForSignIn(start: DeviceStart, cancelled: () => boolean, sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))) {
    const until = Date.now() + start.expires_in * 1000;
    while (Date.now() < until) {
      if (cancelled()) return null;
      const got = await this.pollSignIn(start.device_code);
      if (got) return got;
      await sleep(Math.max(1, start.interval) * 1000);
    }
    throw new VaultMindError(410, "The code expired before it was approved. Sign in again.");
  }

  async me(): Promise<User> {
    return (await this.request<User>("GET", "/api/auth/me")).data;
  }

  async signOut(): Promise<void> {
    await this.request("POST", "/api/auth/logout");
  }

  // ---------------------------------------------------------------- documents

  async documents(query: string, limit = 10): Promise<DocumentSummary[]> {
    const q = encodeURIComponent(query);
    const docs = (await this.request<DocumentSummary[]>("GET", `/api/documents?q=${q}&include_locked=false`)).data;
    return docs.filter((d) => d.access?.allowed !== false).slice(0, limit);
  }

  // ---------------------------------------------------------------- asking

  /** Ask a question; `onText` gets an AI answer as it's written. Resolves with the answer to keep. */
  async ask(
    message: string,
    opts: { conversationId?: number; filters?: Filters; onText?: (text: string) => void; signal?: AbortSignal } = {},
  ): Promise<ChatResult> {
    const body = {
      message: message.slice(0, MAX_MESSAGE),
      conversation_id: opts.conversationId,
      filters: opts.filters && Object.keys(opts.filters).length ? opts.filters : undefined,
    };
    let res: Response;
    try {
      res = await this.fetcher(this.base + "/api/assistant/chat/stream", {
        method: "POST",
        headers: { ...this.headers(), Accept: "text/event-stream" },
        body: JSON.stringify(body),
        signal: opts.signal,
      });
    } catch (err) {
      if ((err as Error).name === "AbortError") throw err;
      throw new VaultMindError(0, `Can't reach VaultMind at ${this.base}. (${(err as Error).message})`);
    }
    if (res.status >= 400) {
      const text = await res.text();
      let data: unknown = text;
      try {
        data = JSON.parse(text);
      } catch {
        /* plain text */
      }
      throw new VaultMindError(res.status, errorText(data, res.status));
    }
    let result: ChatResult | null = null;
    let failure: VaultMindError | null = null;
    for await (const ev of readEvents(res)) {
      if (ev.event === "delta") opts.onText?.((ev.data as { text: string }).text);
      else if (ev.event === "done") result = ev.data as ChatResult;
      else if (ev.event === "error") {
        const e = ev.data as { status: number; detail: unknown };
        failure = new VaultMindError(e.status, errorText({ detail: e.detail }, e.status));
      }
    }
    if (failure) throw failure;
    if (!result) throw new VaultMindError(502, "The answer stopped before it finished. Try again.");
    return result;
  }
}

export function errorText(data: unknown, status: number): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string")
    return (detail as { message: string }).message;
  if (Array.isArray(detail)) return "The request wasn't valid.";
  if (status === 401) return "You're signed out. Sign in again.";
  return `VaultMind answered with an error (${status}).`;
}

/** Server-sent events from a response body: `event:` and `data:` lines, blank line between. */
export async function* readEvents(res: Response): AsyncGenerator<{ event: string; data: unknown }> {
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.search(/\r?\n\r?\n/)) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut).replace(/^\r?\n\r?\n/, "");
      const parsed = parseEvent(block);
      if (parsed) yield parsed;
    }
    if (done) break;
  }
  const last = parseEvent(buffer);
  if (last) yield last;
}

export function parseEvent(block: string): { event: string; data: unknown } | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!data.length) return null;
  const raw = data.join("\n");
  try {
    return { event, data: JSON.parse(raw) };
  } catch {
    return { event, data: raw };
  }
}
