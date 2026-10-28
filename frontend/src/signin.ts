import { setToken } from "./api";

/** Only paths on this site: "/library/3" yes; "https://evil.example" or "//evil.example" no. */
export function safeNext(path: string | null | undefined): string {
  if (!path || !path.startsWith("/") || path.startsWith("//") || path.includes("\\") || path.length > 300) return "/";
  return path;
}

/**
 * After "Continue with Google/Microsoft" the server sends the browser to
 * /signin#token=…&next=…. The token is in the fragment so it never reaches a server log or a
 * Referer header. This runs once, before the router starts: it keeps the token, and swaps the
 * address for /signin?next=… so the token doesn't stay in the address bar or the history.
 */
export function takeSignInFromUrl(): void {
  if (window.location.pathname !== "/signin" || !window.location.hash.includes("token=")) return;
  const params = new URLSearchParams(window.location.hash.slice(1));
  const token = params.get("token");
  const next = safeNext(params.get("next"));
  if (token) setToken(token);
  window.history.replaceState(null, "", `/signin?next=${encodeURIComponent(next)}`);
}

/** What the sign-in page says when a company sign-in didn't work (codes come from the server). */
export function ssoErrorMessage(code: string | null, company: string): string | null {
  if (!code) return null;
  const messages: Record<string, string> = {
    domain: `That account isn't part of ${company}. Use your work account.`,
    unverified: "That account's email address isn't verified yet, so it can't be used to sign in.",
    expired: "That sign-in attempt expired or was already used. Please try again.",
    denied: "Sign-in was cancelled.",
    disabled: "Your VaultMind account has been switched off. Ask an administrator if you still need it.",
    no_account: "There's no VaultMind account for that email yet. Ask an administrator to add you.",
    unavailable: "The sign-in service couldn't be reached just now. Try again in a minute.",
  };
  return messages[code] ?? "Signing in didn't work. Please try again.";
}
