import { useEffect, useId, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useApp } from "../state";
import type { AnswerFeedback, AssistantAction, Conflict, DocCard, FeedbackReason, LockedHint, Message } from "../types";
import { ActionCard } from "./ActionCard";
import { ClassTag, timeAgo } from "./bits";
import {
  IconAlert,
  IconCopy,
  IconExternal,
  IconFile,
  IconHistory,
  IconLock,
  IconSave,
  IconSparkle,
  IconThumbDown,
  IconThumbUp,
} from "./icons";
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

/** The offline answer states a disagreement in a fixed sentence; the callout below shows it instead. */
const DISAGREE_LINE = /^These documents disagree: .*$/gm;

/** What the search did, in words: corrected spelling, acronyms, extra words, the filters used. */
function searchNotes(search: Message["meta"]["search"]): string[] {
  if (!search) return [];
  const notes: string[] = [];
  for (const c of search.corrected ?? []) notes.push(`Showing results for “${c.to}” (you typed “${c.from}”)`);
  for (const a of search.acronyms ?? []) notes.push(`Read ${a.acronym} as “${a.means}”`);
  if (search.rewritten) notes.push(`Searched for “${search.rewritten}” too`);
  else if (search.expanded?.length) notes.push(`Also searched for ${search.expanded.slice(0, 4).join(", ")}`);
  if (search.scope) notes.push(`Only searched ${search.scope}`);
  return notes;
}

function passageLink(id: number, passage?: number) {
  return `/library/${id}${passage !== undefined ? `#p${passage}` : ""}`;
}

/** Two documents that disagree, newest first, so the person can check which applies. */
function ConflictNote({ conflicts }: { conflicts: Conflict[] }) {
  return (
    <div className="conflict-note" role="note">
      <IconAlert width={16} height={16} />
      <div>
        <p className="conflict-title">{conflicts.length > 1 ? "Some documents disagree" : "These documents disagree"}</p>
        {conflicts.map((c) => (
          <p key={`${c.a.id}-${c.b.id}`}>
            <Link to={passageLink(c.a.id, c.a.passage)}>{c.a.title}</Link>{" "}
            <span className="muted">(newer, updated {timeAgo(c.a.updated_at)})</span> says <strong>{c.a.says.join(" and ")}</strong>;{" "}
            <Link to={passageLink(c.b.id, c.b.passage)}>{c.b.title}</Link>{" "}
            <span className="muted">(updated {timeAgo(c.b.updated_at)})</span> says <strong>{c.b.says.join(" and ")}</strong>.
          </p>
        ))}
        <p className="muted small">The newer one usually applies. Check with its owner if it matters.</p>
      </div>
    </div>
  );
}

/** Facts about one source: how fresh, who owns it, where it comes from, why it was used. */
function SourceFacts({ n, title, card }: { n: number; title: string; card: DocCard }) {
  const origin = card.source
    ? `${card.source.name} (${card.source.kind})${card.source.synced_at ? `, synced ${timeAgo(card.source.synced_at)}` : ""}`
    : card.owner;
  return (
    <li>
      <p className="facts-title">
        <span className="source-n">{n}</span> {title}
      </p>
      <dl className="facts">
        {card.updated_at && (
          <>
            <dt>Updated</dt>
            <dd>
              {timeAgo(card.updated_at)}
              {card.version && card.version > 1 ? `, version ${card.version}` : ""}
            </dd>
          </>
        )}
        {origin && (
          <>
            <dt>{card.source ? "Synced from" : "Owner"}</dt>
            <dd>
              {origin}
              {card.source?.url && (
                <>
                  {" "}
                  <a href={card.source.url} target="_blank" rel="noopener noreferrer" className="facts-link">
                    Open the original <IconExternal width={12} height={12} />
                    <span className="sr-only"> (opens in a new tab)</span>
                  </a>
                </>
              )}
            </dd>
          </>
        )}
        <dt>Team</dt>
        <dd>
          {card.team}, <ClassTag value={card.classification} />
        </dd>
        {card.matched && card.matched.length > 0 && (
          <>
            <dt>Matched</dt>
            <dd>{card.matched.join(", ")}</dd>
          </>
        )}
        {card.access && (
          <>
            <dt>Your access</dt>
            <dd>{card.access}</dd>
          </>
        )}
        {card.outdated && (
          <>
            <dt>Check</dt>
            <dd className="facts-stale">May be out of date. {card.outdated}</dd>
          </>
        )}
      </dl>
    </li>
  );
}

const REASONS: [FeedbackReason, string][] = [
  ["wrong", "The answer was wrong"],
  ["missing", "Something was missing"],
  ["source", "It used the wrong document"],
  ["should_abstain", "It should have said it doesn't know"],
  ["other", "Something else"],
];

/** "What was wrong?" after a thumbs-down: a reason and an optional note. */
function FeedbackForm({ onSend, onCancel }: { onSend: (reason: FeedbackReason, note: string) => void; onCancel: () => void }) {
  const [reason, setReason] = useState<FeedbackReason>("");
  const [note, setNote] = useState("");
  const groupId = useId();
  return (
    <form
      className="feedback-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSend(reason, note);
      }}
    >
      <fieldset>
        <legend id={groupId}>What was wrong? (optional)</legend>
        <div className="chip-row" role="radiogroup" aria-labelledby={groupId}>
          {REASONS.map(([value, label]) => (
            <label key={value} className={`chip ${reason === value ? "selected" : ""}`}>
              <input
                type="radio"
                name={`reason-${groupId}`}
                value={value}
                checked={reason === value}
                onChange={() => setReason(value)}
              />
              {label}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="field">
        <span>Anything else? (optional)</span>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={2} />
      </label>
      <div className="row">
        <button className="btn small primary">Send</button>
        <button type="button" className="btn small ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

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
  const conflicts = meta.conflicts ?? [];
  // Shown once, as the callout, rather than also as a sentence of the answer.
  const text = useMemo(
    () => (conflicts.length ? message.content.replace(DISAGREE_LINE, "").replace(/\n{3,}/g, "\n\n").trim() : message.content),
    [message.content, conflicts.length],
  );
  const [shown, done] = useTypewriter(text, animate && message.role === "assistant");
  const labelId = useId();
  const [asking, setAsking] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [opened, setOpened] = useState<number | null>(null); // an action card the person opened themselves
  const [explaining, setExplaining] = useState(false); // the "what was wrong?" form after a thumbs-down
  const titles = useMemo(() => {
    const t = new Map<number, string>();
    for (const c of [...(meta.citations ?? []), ...(meta.suggestions ?? [])]) t.set(c.id, c.title);
    return t;
  }, [meta]);
  const cites = useMemo(() => numberCitations(text, titles), [text, titles]);

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

  async function rate(rating: AnswerFeedback["rating"] | null, reason: FeedbackReason = "", note = "") {
    try {
      const r = await api<{ feedback: AnswerFeedback | null }>(`/api/assistant/messages/${message.id}/feedback`, {
        method: "POST",
        json: { rating, reason, note },
      });
      onUpdate({ ...message, meta: { ...meta, feedback: r.feedback ?? undefined } });
      if (rating) notify(rating === "up" ? "Thanks, glad it helped" : "Thanks, this helps improve the answers");
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't send that", "error");
    }
  }

  async function save() {
    setSaving(true);
    try {
      const action = await api<AssistantAction>("/api/assistant/actions", {
        method: "POST",
        json: { kind: "save_summary", message_id: message.id },
      });
      setOpened(action.id);
      updateAction(action);
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't start saving", "error");
    } finally {
      setSaving(false);
    }
  }

  const sources = [...cites.entries()].map(([id, c]) => ({ id, ...c, card: meta.citations?.find((x) => x.id === id) }));
  // Not enough evidence: what's listed are the nearest documents, not the basis of an answer.
  const weak = meta.evidence?.enough === false;
  const notes = searchNotes(meta.search);
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

        {done && conflicts.length > 0 && <ConflictNote conflicts={conflicts} />}

        {done && sources.length > 0 && (
          <div className="answer-sources">
            <p className="sources-label" id={labelId}>
              {weak ? "Closest matches, which may not answer it" : "Sources"}
            </p>
            <ol className="sources" aria-labelledby={labelId}>
              {sources.map((s) => (
                <li key={s.id}>
                  <Link
                    to={passageLink(s.id, s.card?.passages?.[0])}
                    className={`source ${s.card ? `class-edge-${s.card.classification}` : ""}`}
                    title={s.card ? `${s.card.team}, ${s.card.classification}` : undefined}
                  >
                    <span className="source-n">{s.n}</span>
                    {s.title}
                    {s.card?.outdated && <span className="stale-tag">May be out of date</span>}
                  </Link>
                </li>
              ))}
            </ol>
            {sources.some((s) => s.card?.updated_at) && (
              <details className="why-sources">
                <summary>Why these sources</summary>
                <ol>
                  {sources.map((s) => s.card && <SourceFacts key={s.id} n={s.n} title={s.title} card={s.card} />)}
                </ol>
              </details>
            )}
          </div>
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
                    <ActionCard action={action} onChange={updateAction} compact={compact} autoFocus />
                  )}
                </div>
              );
            })}
          </div>
        )}

        {done &&
          otherActions.map((a) => (
            <ActionCard key={a.id} action={a} onChange={updateAction} compact={compact} autoFocus={a.id === opened} />
          ))}

        {done && notes.length > 0 && <p className="search-note">{notes.join(" · ")}</p>}

        {done && (
          <div className="msg-tools">
            <span className={`answer-label ${meta.mode === "ai" ? "ai" : ""}`}>{MODE_LABEL[meta.mode ?? "offline"]}</span>
            {message.id > 0 && (
              <>
                <button
                  className="icon-btn"
                  aria-label="Helpful"
                  title="Helpful"
                  aria-pressed={meta.feedback?.rating === "up"}
                  onClick={() => {
                    setExplaining(false);
                    rate(meta.feedback?.rating === "up" ? null : "up");
                  }}
                >
                  <IconThumbUp width={14} height={14} />
                </button>
                <button
                  className="icon-btn"
                  aria-label="Not helpful"
                  title="Not helpful"
                  aria-pressed={meta.feedback?.rating === "down"}
                  aria-expanded={explaining}
                  onClick={() => {
                    if (meta.feedback?.rating === "down") {
                      setExplaining(false);
                      rate(null);
                    } else setExplaining(!explaining);
                  }}
                >
                  <IconThumbDown width={14} height={14} />
                </button>
              </>
            )}
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
        {done && explaining && (
          <FeedbackForm
            onSend={(reason, note) => {
              setExplaining(false);
              rate("down", reason, note);
            }}
            onCancel={() => setExplaining(false)}
          />
        )}
      </div>
    </div>
  );
}
