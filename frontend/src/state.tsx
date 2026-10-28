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
  signOut: () => void;
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

  const signOut = useCallback(() => {
    setToken(null);
    setUser(null);
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
    setUnauthorizedHandler(signOut);
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
  }, [signOut]);

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
    setUser(res.user);
  }, []);

  const value = useMemo(
    () => ({ user, meta, metaSettled, ready, signIn, signOut, notify, inboxCount, refreshInbox, refreshMeta }),
    [user, meta, metaSettled, ready, signIn, signOut, notify, inboxCount, refreshInbox, refreshMeta],
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
