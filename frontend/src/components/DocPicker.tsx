import { useEffect, useId, useState } from "react";
import { api } from "../api";
import type { DocRef } from "../types";

/** Choose documents by title: search, then add; chosen ones show as removable chips. */
export function DocPicker({
  value,
  onChange,
  max = 5,
  label = "Documents",
}: {
  value: DocRef[];
  onChange: (docs: DocRef[]) => void;
  max?: number;
  label?: string;
}) {
  const id = useId();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<DocRef[]>([]);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) {
      setResults([]);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      api<DocRef[]>(`/api/documents?q=${encodeURIComponent(term)}`)
        .then((docs) => live && setResults(docs.slice(0, 6).map((d) => ({ id: d.id, title: d.title }))))
        .catch(() => live && setResults([]));
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [q]);

  const chosen = new Set(value.map((d) => d.id));
  return (
    <div className="doc-picker">
      <label className="field" htmlFor={id}>
        <span>{label}</span>
      </label>
      {value.length > 0 && (
        <ul className="doc-picker-chosen" aria-label="Chosen documents">
          {value.map((d) => (
            <li key={d.id} className="tag">
              <span className="mono small">[D{d.id}]</span> {d.title}
              <button type="button" className="link-btn" aria-label={`Remove ${d.title}`} onClick={() => onChange(value.filter((x) => x.id !== d.id))}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {value.length < max && (
        <>
          <input id={id} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by title or contents" autoComplete="off" />
          {results.length > 0 && (
            <ul className="doc-picker-results" aria-label="Matching documents">
              {results
                .filter((d) => !chosen.has(d.id))
                .map((d) => (
                  <li key={d.id}>
                    <button
                      type="button"
                      onClick={() => {
                        onChange([...value, d]);
                        setQ("");
                        setResults([]);
                      }}
                    >
                      + {d.title}
                    </button>
                  </li>
                ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
