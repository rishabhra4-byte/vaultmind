import { useState } from "react";
import { ApiError } from "../api";
import { Ladder } from "../components/bits";
import { useApp } from "../state";

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
      <section className="login-intro">
        <div className="brand brand-lg">
          <span className="brand-mark" aria-hidden />
          VaultMind
        </div>
        <h1>Ask for any company document. Only get what you're cleared to read.</h1>
        <p className="lede">
          VaultMind is the document assistant for {meta?.company ?? "your company"}. It finds the right files for your role,
          answers from them with sources, and checks your team and seniority before it reads a single page on your behalf.
        </p>
        <ul className="legend">
          {meta?.classifications.map((c) => (
            <li key={c.value}>
              <span className={`class-tag class-${c.value}`}>{c.label}</span>
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
                  <button className="persona" onClick={() => go(u.email, u.password)} disabled={!!busy}>
                    <Ladder position={POSITIONS.indexOf(u.position) + 1} label={u.position} />
                    <span className="persona-name">
                      {u.full_name}
                      {u.is_admin && <span className="pill">Admin</span>}
                    </span>
                    <span className="persona-role">
                      {u.title}, {u.team}
                    </span>
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
