import { usePageTitle } from "../components/bits";
import { SystemMap } from "../components/SystemMap";

/** The system map, for people who are signed in (everyone else sees it on the landing page). */
export default function HowItWorks() {
  usePageTitle("How VaultMind works");
  return (
    <div className="page how-page">
      <header className="page-head">
        <div>
          <h1>How VaultMind works</h1>
          <p className="muted">What happens to a question, a locked document or a new file, and what keeps each step safe.</p>
        </div>
      </header>
      <SystemMap headingLevel={2} />
    </div>
  );
}
