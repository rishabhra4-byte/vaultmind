const TOKEN_KEY = "vaultmind.token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session lasts until reload */
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail: unknown,
  ) {
    super(message);
  }
}

/** Called when the server refuses this browser's session, with its reason ("You were signed out (password changed)."). */
let onUnauthorized: (reason: string) => void = () => {};
export function setUnauthorizedHandler(fn: (reason: string) => void) {
  onUnauthorized = fn;
}

function messageFrom(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  return fallback;
}

export async function api<T>(path: string, options: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = options.body;
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.json);
  }
  const res = await fetch(path, { ...options, headers, body });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* non-JSON error */
    }
    const message = messageFrom(detail, `Request failed (${res.status})`);
    if (res.status === 401 && token) onUnauthorized(message);
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/**
 * POST to a server-sent-events endpoint: calls `onDelta` with each piece of text as it arrives,
 * and resolves with the final `done` event's data. Refusals before the stream starts, and
 * `error` events, reject with an ApiError, like `api()`.
 */
export async function streamEvents<T>(
  path: string,
  json: unknown,
  onDelta: (text: string) => void,
  signal?: AbortSignal,
): Promise<T> {
  const token = getToken();
  const headers = new Headers({ "Content-Type": "application/json", Accept: "text/event-stream" });
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(path, { method: "POST", headers, body: JSON.stringify(json), signal });
  if (!res.ok || !res.body) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* non-JSON error */
    }
    const message = messageFrom(detail, `Request failed (${res.status})`);
    if (res.status === 401 && token) onUnauthorized(message);
    throw new ApiError(res.status, message, detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // After "done" the server closes the stream; it's read to the end rather than dropped, so the
  // browser doesn't report the request as cancelled.
  let result: { value: T } | null = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      const payload = data ? JSON.parse(data) : null;
      if (event === "delta") onDelta(payload.text);
      else if (event === "done") result = { value: payload as T };
      else if (event === "error") throw new ApiError(payload.status ?? 500, messageFrom(payload.detail, "The assistant failed"), payload.detail);
    }
  }
  if (result) return result.value;
  throw new ApiError(0, "The answer was cut off. Check your connection and try again.", null);
}

export async function download(docId: number, filename: string) {
  const res = await fetch(`/api/documents/${docId}/download`, {
    headers: { Authorization: `Bearer ${getToken() ?? ""}` },
  });
  if (!res.ok) throw new ApiError(res.status, "Download failed", null);
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  URL.revokeObjectURL(url);
}
