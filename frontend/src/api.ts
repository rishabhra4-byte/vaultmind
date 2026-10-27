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

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
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
  if (res.status === 401 && token) onUnauthorized();
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, messageFrom(detail, `Request failed (${res.status})`), detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
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
