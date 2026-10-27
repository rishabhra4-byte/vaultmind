import { Link } from "react-router-dom";
import { usePageTitle } from "../components/bits";
import { IconCompass } from "../components/icons";
import { useApp } from "../state";

export default function NotFound() {
  const { user } = useApp();
  usePageTitle("Page not found");
  return (
    <div className="page not-found">
      <span className="empty-icon">
        <IconCompass />
      </span>
      <h1>This page doesn't exist</h1>
      <p className="muted">The link may be old or mistyped.</p>
      <div className="row wrap">
        <Link to="/" className="btn primary">
          {user ? "Go to the assistant" : "Go to the homepage"}
        </Link>
        {user && (
          <Link to="/library" className="btn">
            Browse the library
          </Link>
        )}
      </div>
    </div>
  );
}
