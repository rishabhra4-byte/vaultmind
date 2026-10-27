import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api";
import { Avatar, ClassTag, Ladder, teamColor } from "../components/bits";
import { IconLock, IconSparkle } from "../components/icons";
import { useApp } from "../state";
import { ThemeToggle } from "../theme";

const POSITIONS = ["Intern", "Associate", "Senior", "Manager", "Director", "Executive"];

export default function Login() {
  const { signIn, meta } = useApp();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function go(e: string, p: string) {
    setBusy(e);
    setError("");
    try {
      await signIn(e, p);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach the server");
      setBusy(null);
    }
  }

  return (
    <div className="login">
      <ThemeToggle className="login-theme on-light" />
      <div className="login-mobile-brand">
        <Link to="/" className="brand" aria-label="VaultMind home">
          <span className="brand-mark">
            <IconSparkle />
          </span>
          VaultMind
        </Link>
      </div>
      <section className="login-intro">
        <div className="row between">
          <div className="brand brand-lg">
            <span className="brand-mark">
              <IconSparkle />
            </span>
            VaultMind
          </div>
          <Link to="/" className="back-home">
            ‹ Back to home
          </Link>
        </div>

        <div className="hero-art" aria-hidden>
          <div className="float-card fc-1">
            <div className="fc-title">
              Employee Handbook <ClassTag value="public" />
            </div>
            <div className="fc-lines">
              <span />
              <span />
              <span />
            </div>
          </div>
          <div className="float-card fc-2">
            <div className="fc-title">
              Onboarding Guide <ClassTag value="internal" />
            </div>
            <div className="fc-lines">
              <span />
              <span />
              <span />
            </div>
          </div>
          <div className="float-card fc-3 locked">
            <div className="fc-title">
              <IconLock /> Board Notes
            </div>
            <div className="fc-lines">
              <span />
              <span />
              <span />
            </div>
          </div>
        </div>

        <h1>Ask for any company document. Only get what you're cleared to read.</h1>
        <p className="lede">
          VaultMind is the document assistant for {meta?.company ?? "your company"}. It finds the right files for your role,
          answers from them with sources, and checks your team and seniority before it reads a single page on your behalf.
        </p>
        <ul className="legend">
          {meta?.classifications.map((c) => (
            <li key={c.value}>
              <ClassTag value={c.value} />
              <span>{c.help}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="login-panel">
        <form
          className="card stack"
          onSubmit={(e) => {
            e.preventDefault();
            go(email, password);
          }}
        >
          <h2>Sign in</h2>
          <label className="field">
            <span>Work email</span>
            <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          {error && <p className="error">{error}</p>}
          <button className="btn primary" disabled={!!busy}>
            {busy === email ? "Signing in…" : "Sign in"}
          </button>
        </form>

        {meta && meta.demo_users.length > 0 && (
          <div className="personas">
            <h2>Try it as someone else</h2>
            <p className="muted">
              Ask each person the same question, for example “What are the salary bands for SDE-2?”, and compare what they get.
            </p>
            <ul>
              {meta.demo_users.map((u) => (
                <li key={u.email}>
                  <button
                    className="persona"
                    onClick={() => go(u.email, u.password)}
                    disabled={!!busy}
                    style={{ "--av": teamColor(u.team) } as CSSProperties}
                  >
                    <Avatar name={u.full_name} team={u.team} size={40} />
                    <span className="persona-text">
                      <span className="persona-name">
                        {u.full_name}
                        {u.is_admin && <span className="pill">Admin</span>}
                      </span>
                      <span className="persona-role">
                        {u.title}, {u.team}
                      </span>
                    </span>
                    <Ladder position={POSITIONS.indexOf(u.position) + 1} label={u.position} />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  );
}
