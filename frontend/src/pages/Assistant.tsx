import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { RequestAccessDialog, teamColor, usePageTitle } from "../components/bits";
import { Bubble } from "../components/Bubble";
import {
  IconCheck,
  IconChecklist,
  IconCoins,
  IconCompass,
  IconFile,
  IconPlus,
  IconSend,
  IconSiren,
  IconSparkle,
  IconSun,
  IconTrash,
} from "../components/icons";
import { useApp } from "../state";
import type { Conversation, LockedHint, Message, Onboarding, Recommendation } from "../types";

interface Starter {
  text: string;
  icon: ReactNode;
  color: string;
}

function starterPrompts(position: number): Starter[] {
  const leave = { text: "How many days of earned leave do I get?", icon: <IconSun />, color: "#10b981" };
  const salary = { text: "What are the salary bands for SDE-2?", icon: <IconCoins />, color: "#f59e0b" };
  return position <= 2
    ? [
        { text: "I'm new here. What should I read first?", icon: <IconCompass />, color: "#6366f1" },
        leave,
        salary,
        { text: "How do we handle a production incident?", icon: <IconSiren />, color: "#f43f5e" },
      ]
    : [
        { text: "Which documents should I look at this week?", icon: <IconCompass />, color: "#6366f1" },
        leave,
        salary,
        { text: "What's in the engineering hiring plan?", icon: <IconFile />, color: "#ec4899" },
      ];
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
  const [freshId, setFreshId] = useState<number | null>(null);
  const [requesting, setRequesting] = useState<LockedHint | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const askedRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  usePageTitle("Assistant");

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
    if (messages.some((m) => m.id > 0)) return; // already showing this conversation
    api<{ messages: Message[] }>(`/api/assistant/conversations/${conversationId}`)
      .then((c) => setMessages(c.messages))
      .catch(() => setParams({}));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, setParams]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  const send = useCallback(
    async (text: string, convo: number | null) => {
      const message = text.trim();
      if (!message) return;
      setSending(true);
      setDraft("");
      const optimistic: Message = { id: -1, role: "user", content: message, meta: {}, created_at: new Date().toISOString() };
      setMessages((m) => (convo ? [...m, optimistic] : [optimistic]));
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const res = await api<{ conversation: Conversation; user_message: Message; reply: Message }>("/api/assistant/chat", {
          method: "POST",
          json: { message, conversation_id: convo },
          signal: controller.signal,
        });
        setFreshId(res.reply.id);
        setMessages((m) => [...m.filter((x) => x.id !== -1), res.user_message, res.reply]);
        if (res.conversation.id !== convo) setParams({ c: String(res.conversation.id) }, { replace: true });
        loadConversations();
      } catch (err) {
        setMessages((m) => m.filter((x) => x.id !== -1));
        setDraft(message);
        if (controller.signal.aborted) notify("Stopped waiting for that answer. Your question is back in the box.");
        else notify(err instanceof ApiError ? err.message : "The assistant didn't respond. Check your connection.", "error");
      } finally {
        abortRef.current = null;
        setSending(false);
      }
    },
    [loadConversations, notify, setParams],
  );

  // Questions from the command palette or a document page arrive as navigation state, which
  // only the app itself can set (unlike a URL parameter, an outside link can't trigger it).
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    const ask = (location.state as { ask?: unknown } | null)?.ask;
    if (typeof ask === "string" && ask.trim() && askedRef.current !== location.key) {
      askedRef.current = location.key;
      navigate(location.pathname, { replace: true, state: null });
      send(ask.slice(0, 2000), null);
    }
  }, [location, navigate, send]);

  async function remove(id: number) {
    await api(`/api/assistant/conversations/${id}`, { method: "DELETE" });
    if (id === conversationId) setParams({});
    loadConversations();
  }

  function open(id: number) {
    setMessages([]);
    setParams({ c: String(id) });
  }

  if (!user) return null;
  const submit = () => !sending && send(draft, conversationId);

  return (
    <div className="assistant">
      <aside className="threads" aria-label="Conversations">
        <button
          className="btn primary block"
          onClick={() => {
            setMessages([]);
            setParams({});
          }}
        >
          <IconPlus /> New conversation
        </button>
        {conversations.length > 0 && <p className="threads-label">Recent</p>}
        <ul>
          {conversations.map((c) => (
            <li key={c.id} className={c.id === conversationId ? "active" : ""}>
              <button className="thread" onClick={() => open(c.id)} title={c.title}>
                {c.document_id && (
                  <>
                    <IconFile width={13} height={13} className="thread-doc" />
                    <span className="sr-only">About a document: </span>
                  </>
                )}
                {c.title}
              </button>
              <button className="icon-btn subtle" onClick={() => remove(c.id)} aria-label={`Delete “${c.title}”`}>
                <IconTrash width={15} height={15} />
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="chat">
        {messages.length > 0 && <h1 className="sr-only">Assistant</h1>}
        <div className="chat-scroll" role="log" aria-live="polite" aria-label="Conversation">
          {messages.length === 0 ? (
            <div className="chat-empty">
              <span className="pill hello-badge">
                <IconSparkle width={14} height={14} /> {user.position_label}, {user.team_name}
              </span>
              <h1>
                Hi {user.full_name.split(" ")[0]}, what are you looking for?
              </h1>
              <p className="muted">
                I search the documents you can open as {/^[aeiou]/i.test(user.position_label) ? "an" : "a"}{" "}
                {user.position_label} in {user.team_name}, and tell you when something exists that you'd need access for.
              </p>
              <FirstWeek />
              <div className="starters">
                {starterPrompts(user.position).map((s) => (
                  <button
                    key={s.text}
                    className="starter"
                    style={{ "--sc": s.color } as CSSProperties}
                    onClick={() => send(s.text, null)}
                  >
                    <span className="starter-icon">{s.icon}</span>
                    <span>{s.text}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <Bubble
                key={m.id}
                message={m}
                animate={m.id === freshId}
                onRequest={setRequesting}
                onUpdate={(updated) => setMessages((ms) => ms.map((x) => (x.id === updated.id ? updated : x)))}
              />
            ))
          )}
          {sending && (
            <div className="msg-assistant-row msg">
              <span className="bot-avatar">
                <IconSparkle />
              </span>
              <div className="msg-assistant">
                <div className="thinking" aria-label="Searching your documents">
                  <span className="dots">
                    <span />
                    <span />
                    <span />
                  </span>
                  Searching documents you can open
                </div>
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        <div className="composer-wrap">
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder="Ask about a policy, or describe the file you need"
              rows={1}
              aria-label="Message"
              maxLength={2000}
            />
            {sending ? (
              <button type="button" className="btn stop" onClick={() => abortRef.current?.abort()} aria-label="Stop waiting">
                <span className="stop-square" aria-hidden />
              </button>
            ) : (
              <button className="btn primary" disabled={!draft.trim()} aria-label="Send">
                <IconSend />
              </button>
            )}
          </form>
          {meta?.ai_mode === "offline" && (
            <p className="composer-note">
              Offline mode: answers are built from matching passages. Set an Anthropic API key for written answers.
            </p>
          )}
        </div>
      </section>

      <aside className="for-you" aria-label="Suggested for you">
        <h2>
          <IconSparkle /> Suggested for you
        </h2>
        {recs.length === 0 && <p className="muted">Nothing yet.</p>}
        <ul>
          {recs.map((r) => (
            <li key={r.document.id}>
              <Link
                to={`/library/${r.document.id}`}
                className="rec"
                style={{ "--av": teamColor(r.document.owner_team) } as CSSProperties}
              >
                <span className="rec-icon">
                  <IconFile />
                </span>
                <span className="rec-body">
                  <span className="rec-title">{r.document.title}</span>
                  <span className="rec-why">{r.reasons[0]}</span>
                  {r.reasons.length > 1 && <span className="rec-more">{r.reasons.slice(1).join(", ")}</span>}
                </span>
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

/** The new joiner's first-week checklist; it ticks itself off as they use VaultMind. */
function FirstWeek() {
  const [data, setData] = useState<Onboarding | null>(null);
  useEffect(() => {
    api<Onboarding>("/api/onboarding")
      .then(setData)
      .catch(() => setData(null));
  }, []);
  if (!data?.active || data.total === 0) return null;
  async function dismiss() {
    setData(await api<Onboarding>("/api/onboarding/dismiss", { method: "POST" }));
  }
  const pct = Math.round((data.done / data.total) * 100);
  return (
    <section className="first-week" aria-label="Your first week">
      <div className="row between">
        <h2>
          <IconChecklist /> Your first week
        </h2>
        <span className="muted small">
          {data.done} of {data.total} done
        </span>
      </div>
      <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Progress">
        <span style={{ width: `${pct}%` }} />
      </div>
      <ul>
        {data.steps.map((s) => (
          <li key={s.id} className={s.done ? "done" : ""}>
            <span className="tick" aria-hidden>
              {s.done && <IconCheck width={12} height={12} />}
            </span>
            <span>
              {s.link && !s.done ? <Link to={s.link}>{s.label}</Link> : s.label}
              <span className="sr-only">{s.done ? " (done)" : " (to do)"}</span>
              {s.hint && !s.done && <span className="muted small step-hint">{s.hint}</span>}
            </span>
          </li>
        ))}
      </ul>
      <button className="link-btn small" onClick={dismiss}>
        {data.done === data.total ? "All done. Hide this" : "Hide this checklist"}
      </button>
    </section>
  );
}
