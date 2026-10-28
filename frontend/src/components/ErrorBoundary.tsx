import { Component, type ReactNode } from "react";
import { IconRefresh } from "./icons";

/** What browsers say when a page's code can't be downloaded (Chromium, Firefox, Safari, Vite). */
const LOAD_FAILED = /dynamically imported module|Importing a module script failed|Unable to preload/i;

interface Props {
  children: ReactNode;
  /** When this changes (e.g. the person opens another page), the error is cleared. */
  resetKey?: unknown;
}

interface State {
  error: Error | null;
}

/**
 * Shows a way out instead of a blank screen when a page can't load or crashes while drawing.
 * The usual cause is a new version deployed since the tab was opened: its old files are gone,
 * and reloading picks up the new ones.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const loading = LOAD_FAILED.test(String(error.message));
    return (
      <div className="page not-found" role="alert">
        <span className="empty-icon">
          <IconRefresh />
        </span>
        <h1>{loading ? "This page didn't load" : "Something went wrong on this page"}</h1>
        <p className="muted">
          {loading
            ? "VaultMind may have been updated since you opened it, or the connection dropped. Reloading usually fixes it."
            : "Reloading usually fixes it. If it keeps happening, tell your administrator what you were doing."}
        </p>
        <div className="row wrap">
          <button className="btn primary" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }
}
