import { Suspense, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useApp } from "../state";
import { ThemeToggle } from "../theme";
import { AccountDialog } from "./AccountDialog";
import { Avatar, Ladder } from "./bits";
import CommandPalette from "./CommandPalette";
import { ErrorBoundary } from "./ErrorBoundary";
import { NotificationBell } from "./NotificationBell";
import { IconChat, IconInbox, IconKey, IconLibrary, IconLogout, IconSearch, IconShield, IconSparkle } from "./icons";

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

export default function Shell() {
  const { user, signOut, inboxCount, meta } = useApp();
  const { pathname } = useLocation();
  const [palette, setPalette] = useState(false);
  const [account, setAccount] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!user) return null;
  return (
    <div className="shell">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <IconSparkle />
          </span>
          VaultMind
        </div>
        <button className="palette-trigger" onClick={() => setPalette(true)} aria-label="Search and jump">
          <IconSearch />
          <span>Search</span>
          <span className="kbd">{isMac ? "⌘" : "Ctrl"} K</span>
        </button>
        <nav aria-label="Main">
          <NavLink to="/" end>
            <IconChat />
            <span className="nav-label">Assistant</span>
          </NavLink>
          <NavLink to="/library">
            <IconLibrary />
            <span className="nav-label">Library</span>
          </NavLink>
          <NavLink to="/requests">
            <IconInbox />
            <span className="nav-label">Access requests</span>
            {inboxCount > 0 && (
              <span className="count" aria-label={`${inboxCount} waiting for you`}>
                {inboxCount}
              </span>
            )}
          </NavLink>
          <NotificationBell />
          {user.is_admin && (
            <NavLink to="/admin">
              <IconShield />
              <span className="nav-label">Admin</span>
            </NavLink>
          )}
        </nav>
        <div className="identity">
          <div className="identity-top">
            <Avatar name={user.full_name} team={user.team_name} size={38} />
            <span>
              <div className="identity-name">{user.full_name}</div>
              <div className="identity-role">
                {user.title}, {user.team_name}
              </div>
            </span>
          </div>
          <Ladder position={user.position} label={user.position_label} />
          <div className="identity-foot">
            <span className={`mode mode-${meta?.ai_mode}`} title={meta?.ai_model ?? "No API key set"}>
              {meta?.ai_mode === "ai" ? "AI answers on" : "Offline mode"}
            </span>
            <span className="row">
              <ThemeToggle />
              <button className="link-btn" onClick={() => setAccount(true)} aria-label="Your account and sessions" title="Your account and sessions">
                <IconKey />
              </button>
              <button className="link-btn" onClick={signOut} aria-label="Sign out" title="Sign out">
                <IconLogout />
              </button>
            </span>
          </div>
        </div>
      </aside>
      <main className="main" id="main" tabIndex={-1}>
        {/* A page's code loads the first time it's opened; the sidebar stays put meanwhile, and
            if a page fails, the others can still be opened from it. */}
        <ErrorBoundary resetKey={pathname}>
          <Suspense fallback={<div className="page" aria-busy="true" />}>
            <Outlet />
          </Suspense>
        </ErrorBoundary>
      </main>
      {palette && <CommandPalette onClose={() => setPalette(false)} />}
      {account && <AccountDialog onClose={() => setAccount(false)} />}
    </div>
  );
}
