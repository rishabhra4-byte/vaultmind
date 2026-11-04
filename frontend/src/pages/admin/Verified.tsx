import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../../api";
import { Modal, Skeleton, timeAgo } from "../../components/bits";
import { DocPicker } from "../../components/DocPicker";
import { IconCheck, IconPlus } from "../../components/icons";
import { numberCitations, RichText } from "../../components/RichText";
import { useApp } from "../../state";
import type { DocRef, Team, VerifiedAnswer, VerifiedDraft } from "../../types";

const STATUS_CLASS: Record<VerifiedAnswer["status"], string> = {
  current: "ok",
  due: "warn",
  changed: "warn",
  missing: "warn",
  off: "",
};

const date = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

/** Approved answers to common questions: who verified them, and whether they're still current. */
export default function Verified() {
  const { notify } = useApp();
  const location = useLocation();
  const navigate = useNavigate();
  const [rows, setRows] = useState<VerifiedAnswer[] | null>(null);
  const [teams, setTeams] = useState<Team[]>([]);
  const [editing, setEditing] = useState<VerifiedAnswer | VerifiedDraft | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);

  const load = () =>
    api<VerifiedAnswer[]>("/api/admin/verified")
      .then(setRows)
      .catch(() => setRows([]));
  useEffect(() => {
    load();
    api<Team[]>("/api/teams").then(setTeams).catch(() => {});
  }, []);

  // "Make a verified answer" from a conversation or the fix-it queue arrives as navigation state.
  useEffect(() => {
    const draft = (location.state as { draft?: VerifiedDraft } | null)?.draft;
    if (draft) {
      setEditing(draft);
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location, navigate]);

  async function reviewed(va: VerifiedAnswer) {
    try {
      await api(`/api/admin/verified/${va.id}/reviewed`, { method: "POST" });
      notify("Marked as still right, verified today");
      load();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't save that", "error");
    }
  }

  async function remove(va: VerifiedAnswer) {
    try {
      await api(`/api/admin/verified/${va.id}`, { method: "DELETE" });
      notify("Deleted. People get an ordinary answer to that question now.");
      setDeleting(null);
      load();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't delete it", "error");
    }
  }

  const needsReview = rows?.filter((r) => r.status !== "current" && r.status !== "off").length ?? 0;
  return (
    <section>
      <div className="row between wrap admin-head">
        <h2>
          <IconCheck /> Verified answers
        </h2>
        <button className="btn primary" onClick={() => setEditing({ question: "", doc_ids: [] })}>
          <IconPlus /> Add one
        </button>
      </div>
      <p className="muted small section-note">
        Answers you've approved for common questions. People who ask one of them get it first, marked with who verified it and
        when, but only if they can open every source. When a source gets a new version or the review date passes, it stops being
        shown as verified until someone checks it again, and you're reminded.
      </p>
      {needsReview > 0 && (
        <p className="callout warn" role="status">
          {needsReview} {needsReview === 1 ? "answer needs" : "answers need"} a review. Until then, people get an ordinary answer.
        </p>
      )}
      {rows === null ? (
        <Skeleton rows={3} />
      ) : rows.length === 0 ? (
        <p className="muted small">
          None yet. Add one here, or press <strong>Verify</strong> under a good answer in the assistant.
        </p>
      ) : (
        <ul className="card-list">
          {rows.map((va) => (
            <li key={va.id} className="list-card verified-card">
              <div className="list-card-main">
                <div className="row wrap">
                  <strong>{va.question}</strong>
                  <span className={`status-pill ${STATUS_CLASS[va.status]}`}>{va.status_label}</span>
                </div>
                <p className="muted small">
                  {va.team} · verified by {va.by || "someone no longer here"} {timeAgo(va.verified_at)} · review by {date(va.review_by)}
                </p>
                <div className="verified-preview">
                  <RichText text={va.answer} cites={numberCitations(va.answer, new Map(va.sources.map((s) => [s.id, s.title])))} />
                </div>
                <div className="tags">
                  {va.sources.map((s) => (
                    <span key={s.id} className={`tag ${s.changed ? "changed" : ""}`}>
                      {s.title}
                      {s.changed && " · new version"}
                    </span>
                  ))}
                </div>
                {va.phrasings.length > 0 && <p className="muted small">Also matches: {va.phrasings.map((p) => `“${p}”`).join(", ")}</p>}
              </div>
              <div className="row wrap end">
                {va.status !== "current" && va.status !== "off" && va.status !== "missing" && (
                  <button className="btn small primary" onClick={() => reviewed(va)}>
                    Still right
                  </button>
                )}
                <button className="btn small" onClick={() => setEditing(va)} aria-label={`Edit “${va.question}”`}>
                  Edit
                </button>
                {deleting === va.id ? (
                  <>
                    <button className="btn small danger" onClick={() => remove(va)}>
                      Delete it
                    </button>
                    <button className="btn small ghost" onClick={() => setDeleting(null)}>
                      Keep
                    </button>
                  </>
                ) : (
                  <button className="btn small ghost danger-text" onClick={() => setDeleting(va.id)} aria-label={`Delete “${va.question}”`}>
                    Delete
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {editing && (
        <VerifiedDialog
          start={editing}
          teams={teams}
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

function VerifiedDialog({
  start,
  teams,
  onClose,
  onDone,
}: {
  start: VerifiedAnswer | VerifiedDraft;
  teams: Team[];
  onClose: () => void;
  onDone: () => void;
}) {
  const { notify } = useApp();
  const existing = "id" in start ? start : null;
  const [question, setQuestion] = useState(start.question);
  const [phrasings, setPhrasings] = useState(existing?.phrasings.join("\n") ?? "");
  const [answer, setAnswer] = useState(start.answer ?? "");
  const [docs, setDocs] = useState<DocRef[]>(existing?.sources.map((s) => ({ id: s.id, title: s.title })) ?? []);
  const [team, setTeam] = useState<number | "">(existing?.owner_team_id ?? "");
  const [days, setDays] = useState(existing?.review_days ?? 90);
  const [active, setActive] = useState(existing?.active ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // A draft names its sources by id; look their titles up once.
  useEffect(() => {
    if ("id" in start || start.doc_ids.length === 0) return;
    Promise.all(start.doc_ids.map((id: number) => api<DocRef & { owner_team_id: number }>(`/api/documents/${id}`)))
      .then((found) => {
        setDocs(found.map((d) => ({ id: d.id, title: d.title })));
        if (found[0]) setTeam(found[0].owner_team_id); // owned by the team that owns its first source
      })
      .catch(() => {});
  }, [start]);

  useEffect(() => {
    if (team === "" && teams.length) setTeam(teams[0].id);
  }, [team, teams]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(existing ? `/api/admin/verified/${existing.id}` : "/api/admin/verified", {
        method: existing ? "PUT" : "POST",
        json: {
          question: question.trim(),
          phrasings: phrasings.split("\n").map((p) => p.trim()).filter(Boolean),
          answer: answer.trim(),
          doc_ids: docs.map((d) => d.id),
          owner_team_id: team,
          review_days: days,
          active,
        },
      });
      notify(existing ? "Saved, and verified today" : "Verified answer added");
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save it");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={existing ? "Edit verified answer" : "Add a verified answer"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">
          <span>Question</span>
          <input value={question} onChange={(e) => setQuestion(e.target.value)} required maxLength={300} placeholder="e.g. How many days of earned leave do I get?" />
        </label>
        <label className="field">
          <span>Other ways people ask it (optional, one per line)</span>
          <textarea rows={2} value={phrasings} onChange={(e) => setPhrasings(e.target.value)} placeholder="How much annual leave do I get?" />
        </label>
        <DocPicker label="Where the answer comes from" value={docs} onChange={setDocs} max={10} />
        <label className="field">
          <span>Answer</span>
          <textarea rows={5} value={answer} onChange={(e) => setAnswer(e.target.value)} required maxLength={4000} />
          <small className="muted">Cite a source by writing its code, e.g. {docs[0] ? `[D${docs[0].id}]` : "[D12]"}. **Bold** works.</small>
        </label>
        <div className="row wrap">
          <label className="field grow">
            <span>Owned by</span>
            <select value={team} onChange={(e) => setTeam(Number(e.target.value))} required>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field grow">
            <span>Review it every</span>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[30, 90, 180, 365].map((d) => (
                <option key={d} value={d}>
                  {d} days
                </option>
              ))}
            </select>
          </label>
        </div>
        {existing && (
          <label className="check">
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Show it to people
          </label>
        )}
        <p className="muted small">Saving verifies it: today, by you, against the sources as they are now.</p>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy || !question.trim() || !answer.trim() || docs.length === 0 || team === ""}>
            {busy ? "Saving…" : "Save and verify"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
