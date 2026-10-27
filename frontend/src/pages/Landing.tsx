import { useEffect, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { Avatar, ClassTag, teamColor } from "../components/bits";
import {
  IconActivity,
  IconArrowRight,
  IconChat,
  IconCompass,
  IconInbox,
  IconLock,
  IconShield,
  IconSparkle,
} from "../components/icons";
import { useApp } from "../state";
import { ThemeToggle } from "../theme";

const FEATURES = [
  {
    icon: <IconChat />,
    color: "#6366f1",
    title: "Answers that know who's asking",
    body: "Ask in plain English. Answers come only from files you're cleared for, with numbered sources you can open.",
  },
  {
    icon: <IconInbox />,
    color: "#ec4899",
    title: "Access requests that expire",
    body: "Found something you can't open? Request it. The right manager approves, and access ends on its own.",
  },
  {
    icon: <IconShield />,
    color: "#f59e0b",
    title: "A scanner that catches mistakes",
    body: "Uploads are checked for PAN, Aadhaar, card numbers and keys. A payroll sheet marked public gets stopped.",
  },
  {
    icon: <IconCompass />,
    color: "#10b981",
    title: "Suggestions with reasons",
    body: "New joiners see what their team reads, with the reason for every suggestion. No mystery algorithm.",
  },
  {
    icon: <IconActivity />,
    color: "#0ea5e9",
    title: "A complete audit trail",
    body: "Every open, refusal, question, upload and approval is recorded, so admins can answer “who saw this?”.",
  },
  {
    icon: <IconSparkle />,
    color: "#8b5cf6",
    title: "Works with or without AI",
    body: "Add an Anthropic key for written answers by Claude. Without one, the same search and rules still work.",
  },
];

export default function Landing() {
  const { meta } = useApp();
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="landing">
      <header className={`site-nav ${scrolled ? "scrolled" : ""}`}>
        <div className="site-nav-inner">
          <Link to="/" className="brand" aria-label="VaultMind home">
            <span className="brand-mark">
              <IconSparkle />
            </span>
            VaultMind
          </Link>
          <nav aria-label="Sections" className="site-links">
            <a href="#how">How it works</a>
            <a href="#features">Features</a>
            <a href="#security">Security</a>
          </nav>
          <div className="row">
            <ThemeToggle className="on-light" />
            <Link to="/signin" className="btn ghost nav-signin">
              Sign in
            </Link>
            <Link to="/signin" className="btn primary">
              Try the demo
            </Link>
          </div>
        </div>
      </header>

      <main>
        <section className="hero">
          <div className="hero-copy">
            <span className="eyebrow-pill">
              <IconLock width={14} height={14} /> Permission-aware AI for company documents
            </span>
            <h1>The document assistant that knows who's asking.</h1>
            <p className="hero-lede">
              VaultMind answers questions from your company's files, but only from the files each person is cleared to read.
              An intern and a director can ask the same question and get different, correct answers.
            </p>
            <div className="hero-ctas">
              <Link to="/signin" className="btn primary lg">
                Try the live demo <IconArrowRight />
              </Link>
              <a href="#how" className="btn lg">
                See how it works
              </a>
            </div>
            <p className="hero-note muted small">No sign-up. Pick one of nine people at a demo company and start asking.</p>
          </div>

          <div className="hero-visual" aria-label="The same question asked by an intern and a manager">
            <div className="compare-card">
              <div className="compare-head">
                <Avatar name="Aarav Mehta" team="Engineering" size={30} />
                <span>
                  <strong>Aarav</strong> <span className="muted small">Intern, Engineering</span>
                </span>
              </div>
              <div className="msg-user mini">What's the SDE-2 salary band?</div>
              <div className="sealed class-edge-confidential mini">
                <div className="sealed-main">
                  <span className="doc-icon">
                    <IconLock />
                  </span>
                  <div>
                    <div className="sealed-title">
                      Salary Bands 2026 <ClassTag value="confidential" />
                    </div>
                    <div className="sealed-reason">Needs Manager level or above.</div>
                  </div>
                </div>
                <span className="btn small primary" aria-hidden>
                  Request access
                </span>
              </div>
            </div>
            <div className="compare-card offset">
              <div className="compare-head">
                <Avatar name="Rohan Iyer" team="Engineering" size={30} />
                <span>
                  <strong>Rohan</strong> <span className="muted small">Manager, Engineering</span>
                </span>
              </div>
              <div className="msg-user mini">What's the SDE-2 salary band?</div>
              <div className="answer mini">
                <span className="bot-avatar sm">
                  <IconSparkle width={14} height={14} />
                </span>
                <p>
                  SDE-2 is <strong>₹22–34 lakh</strong> per year. Offers above the midpoint need Director approval.
                  <span className="cite">1</span>
                </p>
              </div>
              <span className="source class-edge-confidential mini-source">
                <span className="source-n">1</span> Salary Bands 2026
              </span>
            </div>
          </div>
        </section>

        <section className="section" id="how">
          <div className="section-head">
            <h2>Permissions are checked before the AI reads anything</h2>
            <p className="muted">
              Most document chatbots put everything in the prompt and ask the model to keep secrets. VaultMind never gives the
              model a page the person can't open, so there's nothing to leak.
            </p>
          </div>
          <ol className="steps">
            <li style={{ "--sc": "#6366f1" } as CSSProperties}>
              <span className="step-n">1</span>
              <h3>Check who's asking</h3>
              <p>Their team, their position, and any temporary access they've been granted decide which files are in scope.</p>
            </li>
            <li style={{ "--sc": "#d946ef" } as CSSProperties}>
              <span className="step-n">2</span>
              <h3>Search only what they can open</h3>
              <p>Matching passages come from those files alone. Locked files that match are listed by title, never by content.</p>
            </li>
            <li style={{ "--sc": "#10b981" } as CSSProperties}>
              <span className="step-n">3</span>
              <h3>Answer with sources</h3>
              <p>The answer cites each file it used. Anything locked comes with a one-click request to the people who own it.</p>
            </li>
          </ol>
        </section>

        <section className="section" id="features">
          <div className="section-head">
            <h2>Everything a team needs to share knowledge safely</h2>
          </div>
          <div className="feature-grid">
            {FEATURES.map((f) => (
              <article key={f.title} className="feature" style={{ "--sc": f.color } as CSSProperties}>
                <span className="feature-icon">{f.icon}</span>
                <h3>{f.title}</h3>
                <p>{f.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="section security-band" id="security">
          <div className="security-copy">
            <h2>Four levels, six positions, one set of rules</h2>
            <p>
              Every document has a classification and an owning team. Every person has a team and a position. The rules live in
              one small, tested module, and every decision comes with a reason people can read.
            </p>
            <Link to="/signin" className="btn primary">
              Try it as different people <IconArrowRight />
            </Link>
          </div>
          <ul className="level-list">
            {(meta?.classifications ?? []).map((c) => (
              <li key={c.value}>
                <ClassTag value={c.value} />
                <span>{c.help}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="section cta-final">
          <h2>See it from nine points of view</h2>
          <p className="muted">From the intern to the CEO. Same question, different answers, all correct.</p>
          <div className="avatar-row" aria-hidden>
            {(meta?.demo_users ?? []).map((u) => (
              <span key={u.email} style={{ "--av": teamColor(u.team) } as CSSProperties} title={u.full_name}>
                <Avatar name={u.full_name} team={u.team} size={44} />
              </span>
            ))}
          </div>
          <Link to="/signin" className="btn primary lg">
            Open the demo <IconArrowRight />
          </Link>
        </section>
      </main>

      <footer className="site-foot">
        <span className="brand">
          <span className="brand-mark">
            <IconSparkle />
          </span>
          VaultMind
        </span>
        <span className="muted small">
          Built by Rishabh Rakshit. Open source under the MIT license.{" "}
          <a href="https://github.com/rishabhra4-byte/vaultmind" target="_blank" rel="noreferrer">
            View the code on GitHub
          </a>
        </span>
      </footer>
    </div>
  );
}
