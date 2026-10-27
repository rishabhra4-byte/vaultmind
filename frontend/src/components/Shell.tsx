import { NavLink, Outlet } from "react-router-dom";
import { useApp } from "../state";
import { Ladder } from "./bits";

export default function Shell() {
  const { user, signOut, inboxCount, meta } = useApp();
  if (!user) return null;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          VaultMind
        </div>
        <nav aria-label="Main">
          <NavLink to="/" end>
            Assistant
          </NavLink>
          <NavLink to="/library">Library</NavLink>
          <NavLink to="/requests">
            Access requests
            {inboxCount > 0 && (
              <span className="count" aria-label={`${inboxCount} waiting for you`}>
                {inboxCount}
              </span>
            )}
          </NavLink>
          {user.is_admin && <NavLink to="/admin">Admin</NavLink>}
        </nav>
        <div className="identity">
          <div className="identity-name">{user.full_name}</div>
          <div className="identity-role">
            {user.title}
            <br />
            {user.team_name}
          </div>
          <Ladder position={user.position} label={user.position_label} />
          <div className="identity-foot">
            <span className={`mode mode-${meta?.ai_mode}`} title={meta?.ai_model ?? "No API key set"}>
              {meta?.ai_mode === "ai" ? "AI answers on" : "Offline mode"}
            </span>
            <button className="link-btn" onClick={signOut}>
              Sign out
            </button>
          </div>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
