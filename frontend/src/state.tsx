import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, getToken, setToken, setUnauthorizedHandler } from "./api";
import type { Meta, User } from "./types";

interface Toast {
  id: number;
  text: string;
  tone: "ok" | "error";
}

interface AppState {
  user: User | null;
  meta: Meta | null;
  /** True once the first metadata request has finished, whether or not it succeeded. */
  metaSettled: boolean;
  ready: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  /** Ends this session on the server too, so the token stops working even if it was copied. */
  signOut: () => Promise<void>;
  /** Why the server ended the last session, for the sign-in page ("You were signed out (password changed)."). */
  endedReason: string | null;
  notify: (text: string, tone?: Toast["tone"]) => void;
  inboxCount: number;
  refreshInbox: () => void;
  /** Reload teams and other metadata, e.g. after an admin adds a team. */
  refreshMeta: () => void;
}

const Ctx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useApp outside provider");
  return ctx;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaSettled, setMetaSettled] = useState(false);
  const [ready, setReady] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [inboxCount, setInboxCount] = useState(0);
  const nextId = useRef(1);

  const [endedReason, setEndedReason] = useState<string | null>(null);

  // The server refused the session (signed out elsewhere, password changed, idle too long).
  const expire = useCallback((reason: string) => {
    setToken(null);
    setUser(null);
    setEndedReason(reason);
  }, []);

  const signOut = useCallback(async () => {
    const token = getToken();
    if (token) {
      // End the session on the server first, so the token stops working even if it was copied.
      // Offline or slow: give up after a few seconds; this browser forgets the session regardless.
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3000);
      try {
        const res = await fetch("/api/auth/logout", {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
        });
        await res.text();
      } catch {
        /* it ends on the server when it expires */
      } finally {
        clearTimeout(timer);
      }
    }
    setToken(null);
    setUser(null);
    setEndedReason(null);
  }, []);

  const notify = useCallback((text: string, tone: Toast["tone"] = "ok") => {
    const id = nextId.current++;
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  const refreshInbox = useCallback(() => {
    api<unknown[]>("/api/access-requests/inbox")
      .then((r) => setInboxCount(r.length))
      .catch(() => setInboxCount(0));
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(expire);
    api<Meta>("/api/meta")
      .then(setMeta)
      .catch(() => setMeta(null))
      .finally(() => setMetaSettled(true));
    if (!getToken()) {
      setReady(true);
      return;
    }
    api<User>("/api/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setReady(true));
  }, [expire]);

  useEffect(() => {
    if (user) refreshInbox();
  }, [user, refreshInbox]);

  const refreshMeta = useCallback(() => {
    api<Meta>("/api/meta")
      .then(setMeta)
      .catch(() => {});
  }, []);

  // Teams are only included for signed-in people, so reload metadata when that changes.
  const userId = user?.id ?? null;
  useEffect(() => {
    if (userId !== null) refreshMeta();
  }, [userId, refreshMeta]);

  const signIn = useCallback(async (email: string, password: string) => {
    const res = await api<{ access_token: string; user: User }>("/api/auth/login", { method: "POST", json: { email, password } });
    setToken(res.access_token);
    setEndedReason(null);
    setUser(res.user);
  }, []);

  const value = useMemo(
    () => ({ user, meta, metaSettled, ready, signIn, signOut, endedReason, notify, inboxCount, refreshInbox, refreshMeta }),
    [user, meta, metaSettled, ready, signIn, signOut, endedReason, notify, inboxCount, refreshInbox, refreshMeta],
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone}`}>
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
