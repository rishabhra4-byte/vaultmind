import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../api";
import { useApp } from "../../state";
import type { Conversation, Doc, DocDetail, Intent, LockedHint, Message } from "../../types";
import { RequestAccessDialog } from "../bits";
import { Bubble } from "../Bubble";
import { IconPlus, IconSend, IconSparkle } from "../icons";

interface ChatResponse {
  conversation: Conversation;
  user_message: Message;
  reply: Message;
}

/**
 * Ask about the open document, beside it. Answers come from this document only, cite its
 * paragraphs, and the conversation is kept, so it's there next time the page is opened.
 */
export function DocAsk({ doc, onClose }: { doc: DocDetail; onClose: () => void }) {
  const { notify, meta } = useApp();
  const [conversation, setConversation] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [freshId, setFreshId] = useState<number | null>(null);
  const [comparing, setComparing] = useState(false);
  const [others, setOthers] = useState<Doc[] | null>(null);
  const [requesting, setRequesting] = useState<LockedHint | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Carry on with the latest conversation about this document, if there is one.
  useEffect(() => {
    let live = true;
    setLoading(true);
    api<Conversation[]>(`/api/assistant/conversations?document_id=${doc.id}`)
      .then(async (list) => {
        if (!live || list.length === 0) return;
        const detail = await api<{ messages: Message[] }>(`/api/assistant/conversations/${list[0].id}`);
        if (live) {
          setConversation(list[0].id);
          setMessages(detail.messages);
        }
      })
      .catch(() => {})
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [doc.id]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !document.querySelector("dialog[open]") && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const send = useCallback(
    async (message: string, intent?: Intent, compareWith?: number) => {
      if (!message.trim() && !intent) return;
      setSending(true);
      setDraft("");
      const optimistic: Message = {
        id: -1,
        role: "user",
        content: message.trim() || "…",
        meta: {},
        created_at: new Date().toISOString(),
      };
      setMessages((m) => [...m, optimistic]);
      try {
        const res = await api<ChatResponse>("/api/assistant/chat", {
          method: "POST",
          json: {
            message: message.trim(),
            conversation_id: conversation,
            document_id: conversation ? undefined : doc.id,
            intent,
            compare_with: compareWith,
          },
        });
        setConversation(res.conversation.id);
        setFreshId(res.reply.id);
        setMessages((m) => [...m.filter((x) => x.id !== -1), res.user_message, res.reply]);
      } catch (err) {
        setMessages((m) => m.filter((x) => x.id !== -1));
        if (!intent) setDraft(message);
        notify(err instanceof ApiError ? err.message : "The assistant didn't respond. Check your connection.", "error");
      } finally {
        setSending(false);
      }
    },
    [conversation, doc.id, notify],
  );

  async function openCompare() {
    setComparing(true);
    if (others === null) {
      try {
        const docs = await api<Doc[]>("/api/documents?include_locked=false");
        setOthers(docs.filter((d) => d.id !== doc.id).sort((a, b) => a.title.localeCompare(b.title)));
      } catch {
        setOthers([]);
      }
    }
  }

  const quick: { label: string; intent: Intent; text: string }[] = [
    { label: "Summarise", intent: "summary", text: "Summarise this document" },
    { label: "Key points", intent: "key_points", text: "What are the key points?" },
    { label: "What it means for me", intent: "for_me", text: "What does this mean for me?" },
  ];
  if (doc.version > 1) quick.push({ label: "What changed", intent: "changes", text: "What changed in the latest version?" });

  return (
    <aside className="ask-drawer" aria-label="Ask about this document">
      <div className="ask-head">
        <div>
          <h2>
            <IconSparkle /> Ask about this document
          </h2>
          <p className="muted small">Answers come from “{doc.title}” only.</p>
        </div>
        <div className="row">
          {messages.length > 0 && (
            <button
              className="icon-btn"
              onClick={() => {
                setConversation(null);
                setMessages([]);
              }}
              aria-label="Start a new conversation about this document"
              title="New conversation"
            >
              <IconPlus width={16} height={16} />
            </button>
          )}
          <button className="icon-btn" onClick={onClose} aria-label="Close the assistant panel">
            ×
          </button>
        </div>
      </div>

      {!doc.ai_allowed && meta?.ai_mode === "ai" && (
        <p className="fallback-note ask-note">
          {doc.ai_enabled ? doc.ai_blocked_reason : "AI reading is turned off for this document."} Answers are built from
          its text without AI.
        </p>
      )}

      <div className="ask-scroll" role="log" aria-live="polite" aria-label="Conversation about this document">
        {loading ? (
          <p className="muted small">Loading…</p>
        ) : (
          messages.length === 0 && <p className="muted small">Pick a quick action, or ask your own question.</p>
        )}
        {messages.map((m) => (
          <Bubble
            key={m.id}
            message={m}
            animate={m.id === freshId}
            compact
            onRequest={setRequesting}
            onUpdate={(updated) => setMessages((ms) => ms.map((x) => (x.id === updated.id ? updated : x)))}
          />
        ))}
        {sending && (
          <div className="thinking" aria-label="Reading the document">
            <span className="dots">
              <span />
              <span />
              <span />
            </span>
            Reading the document
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="ask-quick" role="group" aria-label="Quick actions">
        {quick.map((q) => (
          <button key={q.intent} className="chip" disabled={sending} onClick={() => send(q.text, q.intent)}>
            {q.label}
          </button>
        ))}
        <button className="chip" disabled={sending} onClick={openCompare} aria-expanded={comparing}>
          Compare with…
        </button>
      </div>
      {comparing && (
        <div className="ask-compare">
          <label className="field">
            <span>Compare with</span>
            <select
              defaultValue=""
              onChange={(e) => {
                const other = others?.find((d) => d.id === Number(e.target.value));
                if (!other) return;
                setComparing(false);
                send(`Compare this document with “${other.title}”`, "compare", other.id);
              }}
              disabled={others === null}
            >
              <option value="" disabled>
                {others === null ? "Loading documents…" : "Choose a document you can open"}
              </option>
              {others?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.title}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      <form
        className="composer ask-composer"
        onSubmit={(e) => {
          e.preventDefault();
          if (!sending) send(draft);
        }}
      >
        <textarea
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (!sending) send(draft);
            }
          }}
          placeholder="Ask about this document"
          rows={1}
          aria-label="Question about this document"
          maxLength={2000}
        />
        <button className="btn primary" disabled={!draft.trim() || sending} aria-label="Send">
          <IconSend />
        </button>
      </form>

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
    </aside>
  );
}
