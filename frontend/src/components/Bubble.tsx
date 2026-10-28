import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useApp } from "../state";
import type { AssistantAction, LockedHint, Message } from "../types";
import { ActionCard } from "./ActionCard";
import { ClassTag } from "./bits";
import { IconCopy, IconFile, IconHistory, IconLock, IconSave, IconSparkle } from "./icons";
import { numberCitations, RichText } from "./RichText";

const prefersReducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/** Reveal text progressively, like it's being typed, finishing in about a second and a half. */
export function useTypewriter(text: string, enabled: boolean): [string, boolean] {
  const [count, setCount] = useState(enabled && !prefersReducedMotion() ? 0 : text.length);
  useEffect(() => {
    if (count >= text.length) return;
    const step = Math.max(3, Math.ceil(text.length / 90));
    const t = setTimeout(() => setCount((c) => Math.min(text.length, c + step)), 16);
    return () => clearTimeout(t);
  }, [count, text.length]);
  return [text.slice(0, count), count >= text.length];
}

export const MODE_LABEL: Record<string, string> = {
  ai: "Written by Claude from documents you can open",
  offline: "Offline answer: matching passages, no AI",
  direct: "Prepared by VaultMind without AI",
};

export function Bubble({
  message,
  animate,
  onRequest,
  onUpdate,
  compact = false,
}: {
  message: Message;
  animate: boolean;
  /** Fallback for answers without an action card: open the classic request dialog. */
  onRequest: (l: LockedHint) => void;
  onUpdate: (m: Message) => void;
  compact?: boolean;
}) {
  const { notify } = useApp();
  const { meta } = message;
  const [shown, done] = useTypewriter(message.content, animate && message.role === "assistant");
  const [asking, setAsking] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const titles = useMemo(() => {
    const t = new Map<number, string>();
    for (const c of [...(meta.citations ?? []), ...(meta.suggestions ?? [])]) t.set(c.id, c.title);
    return t;
  }, [meta]);
  const cites = useMemo(() => numberCitations(message.content, titles), [message.content, titles]);

  if (message.role === "user") {
    return (
      <div className="msg msg-user">
        <p>
          <span className="sr-only">You asked: </span>
          {message.content}
        </p>
      </div>
    );
  }

  const actions = meta.actions ?? [];
  const requestFor = (docId: number) => actions.find((a) => a.kind === "request_access" && a.payload.document_id === docId);
  const otherActions = actions.filter((a) => a.kind !== "request_access");

  function updateAction(updated: AssistantAction) {
    const exists = actions.some((a) => a.id === updated.id);
    const next = exists ? actions.map((a) => (a.id === updated.id ? updated : a)) : [...actions, updated];
    const locked = meta.locked?.map((l) =>
      updated.kind === "request_access" && updated.status === "done" && updated.payload.document_id === l.id
        ? { ...l, request_pending: true }
        : l,
    );
    onUpdate({ ...message, meta: { ...meta, actions: next, locked } });
    if (updated.status !== "proposed") setAsking(null);
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content.replace(/\s?\[D\d+(?:\.\d+)?\]/g, "").replace(/\*\*/g, ""));
      notify("Answer copied");
    } catch {
      notify("Couldn't copy. Select the text instead.", "error");
    }
  }

  async function save() {
    setSaving(true);
    try {
      const action = await api<AssistantAction>("/api/assistant/actions", {
        method: "POST",
        json: { kind: "save_summary", message_id: message.id },
      });
      updateAction(action);
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't start saving", "error");
    } finally {
      setSaving(false);
    }
  }

  const sources = [...cites.entries()].map(([id, c]) => ({ id, ...c, card: meta.citations?.find((x) => x.id === id) }));
  const canSave =
    message.id > 0 && meta.mode !== "direct" && sources.length > 0 && !actions.some((a) => a.kind === "save_summary");
  return (
    <div className={`msg msg-assistant-row ${compact ? "compact" : ""}`} aria-busy={!done}>
      <span className="bot-avatar">
        <IconSparkle />
      </span>
      <div className="msg-assistant">
        <span className="sr-only">VaultMind answered: </span>
        {meta.fallback && <p className="fallback-note">{meta.fallback.message}</p>}
        {meta.notice && <p className="fallback-note">{meta.notice}</p>}
        <div>
          <RichText text={shown} cites={cites} />
          {!done && <span className="caret" aria-hidden />}
        </div>

        {done && sources.length > 0 && (
          <ol className="sources" aria-label="Sources">
            {sources.map((s) => (
              <li key={s.id}>
                <Link
                  to={`/library/${s.id}${s.card?.passages?.length ? `#p${s.card.passages[0]}` : ""}`}
                  className={`source ${s.card ? `class-edge-${s.card.classification}` : ""}`}
                  title={s.card ? `${s.card.team}, ${s.card.classification}` : undefined}
                >
                  <span className="source-n">{s.n}</span>
                  {s.title}
                </Link>
              </li>
            ))}
          </ol>
        )}

        {done && meta.mode === "ai" && meta.suggestions && meta.suggestions.length > 0 && cites.size === 0 && (
          <ul className="sources">
            {meta.suggestions.map((s) => (
              <li key={s.id}>
                <Link to={`/library/${s.id}`} className={`source class-edge-${s.classification}`}>
                  <span className="source-n">
                    <IconFile width={12} height={12} />
                  </span>
                  {s.title}
                </Link>
              </li>
            ))}
          </ul>
        )}

        {done && meta.changes && meta.changes.length > 0 && (
          <div className="row wrap">
            {meta.changes.slice(0, 3).map((c) => (
              <Link key={c.id} to={`/library/${c.id}?changes=1`} className="btn small">
                <IconHistory width={14} height={14} /> See what changed{meta.changes!.length > 1 ? ` in ${c.title}` : ""}
              </Link>
            ))}
          </div>
        )}

        {done && meta.ai_blocked && meta.ai_blocked.length > 0 && (
          <div className="ai-blocked">
            <p className="muted small">Also matching, but kept away from the AI by their owners. Open them to read:</p>
            <ul className="sources">
              {meta.ai_blocked.map((d) => (
                <li key={d.id}>
                  <Link to={`/library/${d.id}`} className={`source class-edge-${d.classification}`}>
                    <span className="source-n">
                      <IconFile width={12} height={12} />
                    </span>
                    {d.title}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}

        {done && meta.locked && meta.locked.length > 0 && (
          <div className="sealed-list">
            {meta.locked.map((l) => {
              const action = requestFor(l.id);
              return (
                <div key={l.id} className={`sealed class-edge-${l.classification}`}>
                  <div className="sealed-row">
                    <div className="sealed-main">
                      <span className="doc-icon">
                        <IconLock />
                      </span>
                      <div>
                        <div className="sealed-title">
                          {l.title} <ClassTag value={l.classification} />
                        </div>
                        <div className="sealed-reason">{l.reason}</div>
                      </div>
                    </div>
                    {l.request_pending || action?.status === "done" ? (
                      <span className="pill pending">Request pending</span>
                    ) : (
                      <button
                        className="btn small primary"
                        onClick={() => (action ? setAsking(asking === l.id ? null : l.id) : onRequest(l))}
                        aria-expanded={action ? asking === l.id : undefined}
                        aria-label={`Request access to ${l.title}`}
                      >
                        Request access
                      </button>
                    )}
                  </div>
                  {action && asking === l.id && action.status === "proposed" && (
                    <ActionCard action={action} onChange={updateAction} compact={compact} />
                  )}
                </div>
              );
            })}
          </div>
        )}

        {done && otherActions.map((a) => <ActionCard key={a.id} action={a} onChange={updateAction} compact={compact} />)}

        {done && meta.search && (meta.search.expanded?.length || meta.search.rewritten) && (
          <p className="search-note">
            {meta.search.rewritten
              ? `Searched for “${meta.search.rewritten}” too`
              : `Also searched for ${meta.search.expanded!.slice(0, 4).join(", ")}`}
          </p>
        )}

        {done && (
          <div className="msg-tools">
            <span className={`answer-label ${meta.mode === "ai" ? "ai" : ""}`}>{MODE_LABEL[meta.mode ?? "offline"]}</span>
            {canSave && (
              <button className="icon-btn" onClick={save} disabled={saving} aria-label="Save this answer as a document">
                <IconSave width={14} height={14} /> Save
              </button>
            )}
            <button className="icon-btn" onClick={copy} aria-label="Copy answer">
              <IconCopy width={14} height={14} /> Copy
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
