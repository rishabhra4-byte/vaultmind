import { useEffect, useState } from "react";
import { api, ApiError } from "../../api";
import { Modal, Skeleton } from "../../components/bits";
import { IconBook, IconPlus } from "../../components/icons";
import { useApp } from "../../state";
import type { GlossaryTerm } from "../../types";

const split = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

export default function Glossary() {
  const { notify } = useApp();
  const [terms, setTerms] = useState<GlossaryTerm[] | null>(null);
  const [editing, setEditing] = useState<GlossaryTerm | "new" | null>(null);

  const load = () =>
    api<GlossaryTerm[]>("/api/glossary")
      .then(setTerms)
      .catch(() => setTerms([]));
  useEffect(() => {
    load();
  }, []);

  async function remove(t: GlossaryTerm) {
    try {
      await api(`/api/admin/glossary/${t.id}`, { method: "DELETE" });
      notify(`Removed “${t.term}”`);
      load();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't remove it", "error");
    }
  }

  const company = terms?.filter((t) => !t.builtin) ?? [];
  const builtin = terms?.filter((t) => t.builtin) ?? [];
  return (
    <section>
      <div className="row between wrap admin-head">
        <h2>
          <IconBook /> Glossary
        </h2>
        <button className="btn primary" onClick={() => setEditing("new")}>
          <IconPlus /> Add a term
        </button>
      </div>
      <p className="muted small section-note">
        Words people use for the same thing. When someone asks about one, the assistant also searches for the others, so “PTO”
        finds the Leave Policy. Add your team nicknames, product code names and acronyms.
      </p>
      {terms === null ? (
        <Skeleton rows={3} />
      ) : (
        <>
          <h3 className="list-heading">Your company's terms</h3>
          {company.length === 0 ? (
            <p className="muted small">None yet.</p>
          ) : (
            <ul className="card-list">
              {company.map((t) => (
                <li key={t.id} className="list-card">
                  <div className="list-card-main">
                    <strong>{t.term}</strong>
                    <div className="tags">
                      {t.synonyms.map((s) => (
                        <span key={s} className="tag">
                          {s}
                        </span>
                      ))}
                    </div>
                    {t.note && <p className="muted small">{t.note}</p>}
                  </div>
                  <div className="row">
                    <button className="btn small" onClick={() => setEditing(t)} aria-label={`Edit ${t.term}`}>
                      Edit
                    </button>
                    <button className="btn small ghost danger-text" onClick={() => remove(t)} aria-label={`Remove ${t.term}`}>
                      Remove
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <h3 className="list-heading">Built in</h3>
          <ul className="builtin-terms">
            {builtin.map((t) => (
              <li key={t.term}>
                <strong>{t.term}</strong> <span className="muted">{t.synonyms.join(", ")}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {editing && (
        <TermDialog
          term={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onDone={() => {
            setEditing(null);
            load();
          }}
        />
      )}
    </section>
  );
}

function TermDialog({ term, onClose, onDone }: { term: GlossaryTerm | null; onClose: () => void; onDone: () => void }) {
  const { notify } = useApp();
  const [name, setName] = useState(term?.term ?? "");
  const [synonyms, setSynonyms] = useState(term?.synonyms.join(", ") ?? "");
  const [note, setNote] = useState(term?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(term ? `/api/admin/glossary/${term.id}` : "/api/admin/glossary", {
        method: term ? "PATCH" : "POST",
        json: { term: name.trim(), synonyms: split(synonyms), note: note.trim() },
      });
      notify(term ? "Saved" : `Added “${name.trim()}”`);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save it");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={term ? `Edit “${term.term}”` : "Add a term"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">
          <span>Term</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} placeholder="e.g. Spend policy" />
        </label>
        <label className="field">
          <span>Other words people use for it</span>
          <input
            value={synonyms}
            onChange={(e) => setSynonyms(e.target.value)}
            required
            placeholder="e.g. expense policy, reimbursement"
          />
          <small className="muted">Separate them with commas.</small>
        </label>
        <label className="field">
          <span>Note (optional)</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={255} />
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy || !name.trim() || split(synonyms).length === 0}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
