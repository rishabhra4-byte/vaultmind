import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../state";
import type { Doc, Recommendation } from "../types";
import { ClassTag } from "./bits";
import { IconChat, IconFile, IconInbox, IconLibrary, IconLock, IconSearch, IconShield, IconSparkle } from "./icons";

interface Item {
  key: string;
  group: string;
  title: ReactNode;
  hint?: ReactNode;
  icon: ReactNode;
  edge?: string;
  run: () => void;
}

/** Ctrl/⌘ + K: search every document you can see and jump anywhere. */
export default function CommandPalette({ onClose }: { onClose: () => void }) {
  const { user } = useApp();
  const navigate = useNavigate();
  const ref = useRef<HTMLDialogElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const [q, setQ] = useState("");
  const [docs, setDocs] = useState<Doc[]>([]);
  const [suggested, setSuggested] = useState(true);
  const [active, setActive] = useState(0);

  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);

  useEffect(() => {
    let cancelled = false;
    const t = setTimeout(() => {
      const query = q.trim();
      const req = query
        ? api<Doc[]>(`/api/documents?q=${encodeURIComponent(query)}&include_locked=true`).then((d) => d.slice(0, 7))
        : api<Recommendation[]>("/api/recommendations?limit=5").then((r) => r.map((x) => x.document));
      req
        .then((d) => {
          if (cancelled) return;
          setDocs(d);
          setSuggested(!query);
          setActive(0);
        })
        .catch(() => {});
    }, 140);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [q]);

  const go = (path: string) => {
    onClose();
    navigate(path);
  };

  const items = useMemo<Item[]>(() => {
    const query = q.trim();
    const out: Item[] = [];
    if (query) {
      out.push({
        key: "ask",
        group: "Assistant",
        title: (
          <>
            Ask the assistant: <em>“{query}”</em>
          </>
        ),
        icon: <IconSparkle />,
        run: () => go(`/?ask=${encodeURIComponent(query)}`),
      });
    }
    const pages = [
      { label: "Assistant", path: "/", icon: <IconChat /> },
      { label: "Library", path: "/library", icon: <IconLibrary /> },
      { label: "Access requests", path: "/requests", icon: <IconInbox /> },
      ...(user?.is_admin ? [{ label: "Admin dashboard", path: "/admin", icon: <IconShield /> }] : []),
    ].filter((p) => !query || p.label.toLowerCase().includes(query.toLowerCase()));
    for (const p of pages) out.push({ key: p.path, group: "Go to", title: p.label, icon: p.icon, run: () => go(p.path) });
    for (const d of docs) {
      out.push({
        key: `doc-${d.id}`,
        group: suggested ? "Suggested for you" : "Documents",
        title: d.title,
        hint: (
          <>
            {d.owner_team}, {d.access.allowed ? "you can open this" : "needs access"}
          </>
        ),
        icon: d.access.allowed ? <IconFile /> : <IconLock />,
        edge: d.classification,
        run: () => go(`/library/${d.id}`),
      });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, docs, suggested, user]);

  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      items[active]?.run();
    }
  }

  let lastGroup = "";
  return (
    <dialog ref={ref} className="palette" onClose={onClose} onCancel={onClose} aria-label="Search and jump">
      <div className="palette-input">
        <IconSearch />
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey}
          placeholder="Search documents, or ask anything…"
          aria-label="Search"
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
        />
      </div>
      <ul className="palette-list" id="palette-list" role="listbox" ref={listRef}>
        {items.map((it, i) => {
          const header = it.group !== lastGroup ? it.group : null;
          lastGroup = it.group;
          return (
            <li key={it.key} role="presentation">
              {header && <div className="palette-group">{header}</div>}
              <div
                className="palette-item"
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onClick={it.run}
              >
                <span className={`doc-icon ${it.edge ? `class-edge-${it.edge}` : ""}`}>{it.icon}</span>
                <span className="palette-item-text">
                  <strong>{it.title}</strong>
                  {it.hint && <small>{it.hint}</small>}
                </span>
                {it.edge && <ClassTag value={it.edge as Doc["classification"]} />}
              </div>
            </li>
          );
        })}
        {items.length === 0 && <li className="palette-group">No matches. Try another word.</li>}
      </ul>
      <div className="palette-foot">
        <span>
          <span className="kbd">↑</span> <span className="kbd">↓</span> to move
        </span>
        <span>
          <span className="kbd">Enter</span> to open
        </span>
        <span>
          <span className="kbd">Esc</span> to close
        </span>
      </div>
    </dialog>
  );
}
