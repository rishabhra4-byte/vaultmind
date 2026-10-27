import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { ClassTag, RequestAccessDialog } from "../components/bits";
import { numberCitations, RichText } from "../components/RichText";
import { useApp } from "../state";
import type { Conversation, LockedHint, Message, Recommendation } from "../types";

function starterPrompts(position: number): string[] {
  const base = ["How many days of earned leave do I get?", "What are the salary bands for SDE-2?"];
  return position <= 2
    ? ["I'm new here. What should I read first?", ...base, "How do we handle a production incident?"]
    : ["Which documents should I look at this week?", ...base, "What's in the engineering hiring plan?"];
}

export default function Assistant() {
  const { user, meta, notify } = useApp();
  const [params, setParams] = useSearchParams();
  const conversationId = params.get("c") ? Number(params.get("c")) : null;

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [requesting, setRequesting] = useState<LockedHint | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const loadConversations = useCallback(() => {
    api<Conversation[]>("/api/assistant/conversations").then(setConversations).catch(() => {});
  }, []);

  useEffect(() => {
    loadConversations();
    api<Recommendation[]>("/api/recommendations?limit=5").then(setRecs).catch(() => {});
  }, [loadConversations]);

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      return;
    }
    api<{ messages: Message[] }>(`/api/assistant/conversations/${conversationId}`)
      .then((c) => setMessages(c.messages))
      .catch(() => setParams({}));
  }, [conversationId, setParams]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || sending) return;
    setSending(true);
    setDraft("");
    const optimistic: Message = { id: -1, role: "user", content: message, meta: {}, created_at: new Date().toISOString() };
    setMessages((m) => [...m, optimistic]);
    try {
      const res = await api<{ conversation: Conversation; user_message: Message; reply: Message }>("/api/assistant/chat", {
        method: "POST",
        json: { message, conversation_id: conversationId },
      });
      setMessages((m) => [...m.filter((x) => x.id !== -1), res.user_message, res.reply]);
      if (res.conversation.id !== conversationId) setParams({ c: String(res.conversation.id) }, { replace: true });
      loadConversations();
    } catch (err) {
      setMessages((m) => m.filter((x) => x.id !== -1));
      setDraft(message);
      notify(err instanceof ApiError ? err.message : "The assistant didn't respond", "error");
    } finally {
      setSending(false);
    }
  }

  async function remove(id: number) {
    await api(`/api/assistant/conversations/${id}`, { method: "DELETE" });
    if (id === conversationId) setParams({});
    loadConversations();
  }

  if (!user) return null;

  return (
    <div className="assistant">
      <aside className="threads" aria-label="Conversations">
        <button className="btn primary block" onClick={() => setParams({})}>
          New conversation
        </button>
        <ul>
          {conversations.map((c) => (
            <li key={c.id} className={c.id === conversationId ? "active" : ""}>
              <button className="thread" onClick={() => setParams({ c: String(c.id) })}>
                {c.title}
              </button>
              <button className="icon-btn subtle" onClick={() => remove(c.id)} aria-label={`Delete “${c.title}”`}>
                ×
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="chat">
        <div className="chat-scroll">
          {messages.length === 0 ? (
            <div className="chat-empty">
              <h1>What are you looking for, {user.full_name.split(" ")[0]}?</h1>
              <p className="muted">
                I search the documents you can open as {/^[aeiou]/i.test(user.position_label) ? "an" : "a"}{" "}
                {user.position_label} in {user.team_name}, and tell you when something exists that you'd need access for.
              </p>
              <div className="starters">
                {starterPrompts(user.position).map((p) => (
                  <button key={p} className="starter" onClick={() => send(p)}>
                    {p}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m) => <Bubble key={m.id} message={m} onRequest={setRequesting} />)
          )}
          {sending && (
            <div className="msg msg-assistant">
              <div className="thinking" aria-label="Searching your documents">
                Searching documents you can open<span>.</span>
                <span>.</span>
                <span>.</span>
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send(draft);
          }}
        >
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(draft);
              }
            }}
            placeholder="Ask about a policy, or describe the file you need"
            rows={1}
            aria-label="Message"
            maxLength={2000}
          />
          <button className="btn primary" disabled={sending || !draft.trim()}>
            Ask
          </button>
        </form>
        {meta?.ai_mode === "offline" && (
          <p className="composer-note">
            Offline mode: answers are built from matching passages. Set an Anthropic API key for written answers.
          </p>
        )}
      </section>

      <aside className="for-you" aria-label="Suggested for you">
        <h2>Suggested for you</h2>
        {recs.length === 0 && <p className="muted">Nothing yet.</p>}
        <ul>
          {recs.map((r) => (
            <li key={r.document.id}>
              <Link to={`/library/${r.document.id}`} className="rec">
                <span className="rec-title">{r.document.title}</span>
                <span className="rec-why">{r.reasons[0]}</span>
                {r.reasons.length > 1 && <span className="rec-more">{r.reasons.slice(1).join(", ")}</span>}
              </Link>
            </li>
          ))}
        </ul>
      </aside>

      {requesting && (
        <RequestAccessDialog
          doc={requesting}
          onClose={() => setRequesting(null)}
          onDone={() =>
            setMessages((ms) =>
              ms.map((m) => ({
                ...m,
                meta: {
                  ...m.meta,
                  locked: m.meta.locked?.map((l) => (l.id === requesting.id ? { ...l, request_pending: true } : l)),
                },
              })),
            )
          }
        />
      )}
    </div>
  );
}

function Bubble({ message, onRequest }: { message: Message; onRequest: (l: LockedHint) => void }) {
  const { meta } = message;
  const titles = useMemo(() => {
    const t = new Map<number, string>();
    for (const c of [...(meta.citations ?? []), ...(meta.suggestions ?? [])]) t.set(c.id, c.title);
    return t;
  }, [meta]);
  const cites = useMemo(() => numberCitations(message.content, titles), [message.content, titles]);

  if (message.role === "user") {
    return (
      <div className="msg msg-user">
        <p>{message.content}</p>
      </div>
    );
  }

  const sources = [...cites.entries()].map(([id, c]) => ({ id, ...c, card: meta.citations?.find((x) => x.id === id) }));
  return (
    <div className="msg msg-assistant">
      <RichText text={message.content} cites={cites} />

      {sources.length > 0 && (
        <ol className="sources" aria-label="Sources">
          {sources.map((s) => (
            <li key={s.id}>
              <Link to={`/library/${s.id}`}>{s.title}</Link>
              {s.card && <ClassTag value={s.card.classification} />}
            </li>
          ))}
        </ol>
      )}

      {meta.mode === "ai" && meta.suggestions && meta.suggestions.length > 0 && cites.size === 0 && (
        <ul className="sources">
          {meta.suggestions.map((s) => (
            <li key={s.id}>
              <Link to={`/library/${s.id}`}>{s.title}</Link>
            </li>
          ))}
        </ul>
      )}

      {meta.locked && meta.locked.length > 0 && (
        <div className="sealed-list">
          {meta.locked.map((l) => (
            <div key={l.id} className={`sealed class-edge-${l.classification}`}>
              <div>
                <div className="sealed-title">
                  {l.title} <ClassTag value={l.classification} />
                </div>
                <div className="sealed-reason">{l.reason}</div>
              </div>
              {l.request_pending ? (
                <span className="pill">Request pending</span>
              ) : (
                <button className="btn small" onClick={() => onRequest(l)}>
                  Request access
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
