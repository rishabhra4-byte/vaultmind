import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, getToken } from "../../api";
import { Skeleton, timeAgo } from "../../components/bits";
import { DocPicker } from "../../components/DocPicker";
import { IconChecklist, IconDownload } from "../../components/icons";
import { useApp } from "../../state";
import type { DocRef, ReviewCase, ReviewItem, ReviewQueue } from "../../types";

const MODE: Record<string, string> = { ai: "AI answer", offline: "Offline answer", direct: "VaultMind", verified: "Verified answer" };

/** Real questions the assistant got wrong: say what the right answer comes from, and check
 *  whether it gets them right now. */
export default function Review() {
  const { notify } = useApp();
  const [data, setData] = useState<ReviewQueue | null>(null);
  const [checking, setChecking] = useState(false);

  const load = () =>
    api<ReviewQueue>("/api/admin/review")
      .then(setData)
      .catch(() => setData({ days: 90, summary: { open: 0, resolved: 0, dismissed: 0, checked: 0, passing: 0 }, open: [], resolved: [] }));
  useEffect(() => {
    load();
  }, []);

  async function checkAll() {
    setChecking(true);
    try {
      const r = await api<{ checked: number; passing: number }>("/api/admin/review/check-all", { method: "POST" });
      notify(`Checked ${r.checked}: ${r.passing} answered right`);
      load();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't check them", "error");
    } finally {
      setChecking(false);
    }
  }

  async function download() {
    const res = await fetch("/api/admin/review/export", { headers: { Authorization: `Bearer ${getToken() ?? ""}` } });
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: "vaultmind-cases.jsonl" });
    a.click();
    URL.revokeObjectURL(url);
  }

  const s = data?.summary;
  return (
    <section>
      <div className="row between wrap admin-head">
        <h2>
          <IconChecklist /> Fix answers
        </h2>
        <div className="row wrap">
          <button className="btn" onClick={download} disabled={!s?.resolved}>
            <IconDownload /> Download cases
          </button>
          <button className="btn primary" onClick={checkAll} disabled={checking || !s?.resolved}>
            {checking ? "Checking…" : "Check them all again"}
          </button>
        </div>
      </div>
      <p className="muted small section-note">
        Answers people marked unhelpful, and questions where nothing matched or the documents didn't hold enough, from the last{" "}
        {data?.days ?? 90} days, without who asked. Say what the right answer comes from, or that nothing answers it, and
        VaultMind asks again as the same person to check. Checks use offline answers: the same search, evidence check and
        verified answers people get, without spending AI calls.
      </p>
      {data === null ? (
        <Skeleton rows={4} />
      ) : (
        <>
          <div className="review-score" role="status">
            <div>
              <strong>{s!.open}</strong>
              <span>waiting</span>
            </div>
            <div>
              <strong>{s!.resolved}</strong>
              <span>decided</span>
            </div>
            <div>
              <strong>{s!.checked ? `${Math.round((100 * s!.passing) / s!.checked)}%` : "–"}</strong>
              <span>
                answered right{s!.checked ? ` (${s!.passing} of ${s!.checked})` : ""}
              </span>
            </div>
          </div>

          <h3 className="list-heading">Waiting</h3>
          {data.open.length === 0 ? (
            <p className="muted small">Nothing waiting. Questions that go wrong will show up here.</p>
          ) : (
            <ul className="card-list">
              {data.open.map((item) => (
                <OpenItem key={item.key} item={item} onDone={load} />
              ))}
            </ul>
          )}

          <h3 className="list-heading">Decided</h3>
          {data.resolved.length === 0 ? (
            <p className="muted small">None yet.</p>
          ) : (
            <ul className="card-list">
              {data.resolved.map((c) => (
                <DecidedCase key={c.id} c={c} onDone={load} />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function OpenItem({ item, onDone }: { item: ReviewItem; onDone: () => void }) {
  const { notify } = useApp();
  const [deciding, setDeciding] = useState(false);

  async function dismiss() {
    try {
      await api("/api/admin/review", { method: "POST", json: { question: item.question, status: "dismissed" } });
      notify("Dismissed");
      onDone();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't dismiss it", "error");
    }
  }

  return (
    <li className="list-card review-item">
      <div className="list-card-main">
        <strong className="review-q">“{item.question}”</strong>
        <p className="muted small">
          Asked {item.asked} {item.asked === 1 ? "time" : "times"} · last {timeAgo(item.last)}
        </p>
        <div className="tags">
          {item.why.map((w) => (
            <span key={w.kind} className={`tag why-${w.kind}`}>
              {w.label}
              {w.count > 1 ? ` ×${w.count}` : ""}
            </span>
          ))}
        </div>
        {item.notes.map((n, i) => (
          <p key={i} className="small review-note">
            {n.reason && <strong>{n.reason}. </strong>}
            {n.note}
          </p>
        ))}
        {item.answer && (
          <div className="review-answer small">
            <span className="muted">{MODE[item.answer.mode] ?? "Answer"}</span>
            {item.answer.cited.length > 0 ? <> used {item.answer.cited.map((d) => `“${d.title}”`).join(", ")}</> : <> used no document</>}
            {item.answer.text && <p className="muted clamp">{item.answer.text.replace(/\[D\d+(?:\.\d+)?\]/g, "").replace(/\*\*/g, "")}</p>}
          </div>
        )}
        {deciding && (
          <DecideForm
            question={item.question}
            start={item.answer?.cited ?? []}
            onCancel={() => setDeciding(false)}
            onDone={() => {
              setDeciding(false);
              onDone();
            }}
          />
        )}
      </div>
      {!deciding && (
        <div className="row wrap end">
          <button className="btn small primary" onClick={() => setDeciding(true)}>
            Say what answers it
          </button>
          <button className="btn small ghost" onClick={dismiss}>
            Dismiss
          </button>
        </div>
      )}
    </li>
  );
}

function DecideForm({ question, start, onCancel, onDone }: { question: string; start: DocRef[]; onCancel: () => void; onDone: () => void }) {
  const { notify } = useApp();
  const [expect, setExpect] = useState<"answer" | "abstain">("answer");
  const [docs, setDocs] = useState<DocRef[]>(start.slice(0, 1));
  const [says, setSays] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const c = await api<ReviewCase>("/api/admin/review", {
        method: "POST",
        json: {
          question,
          expect,
          doc_ids: expect === "answer" ? docs.map((d) => d.id) : [],
          says: expect === "answer" ? says.split(",").map((x) => x.trim()).filter(Boolean) : [],
        },
      });
      notify(c.check.passed ? "Saved. It answers this right now." : "Saved. It doesn't answer this right yet: see why under Decided.");
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save it");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack decide-form" onSubmit={save}>
      <fieldset className="radio-row">
        <legend className="sr-only">What's the right answer?</legend>
        <label className="check">
          <input type="radio" name={`expect-${question}`} checked={expect === "answer"} onChange={() => setExpect("answer")} /> These
          documents answer it
        </label>
        <label className="check">
          <input type="radio" name={`expect-${question}`} checked={expect === "abstain"} onChange={() => setExpect("abstain")} /> Nothing
          answers this: it should say so
        </label>
      </fieldset>
      {expect === "answer" && (
        <>
          <DocPicker label="The right documents" value={docs} onChange={setDocs} />
          <label className="field">
            <span>Words the answer must contain (optional, separate with commas)</span>
            <input value={says} onChange={(e) => setSays(e.target.value)} placeholder="e.g. 7,000" />
          </label>
        </>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="row end">
        <button type="button" className="btn small ghost" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn small primary" disabled={busy || (expect === "answer" && docs.length === 0)}>
          {busy ? "Checking…" : "Save and check"}
        </button>
      </div>
    </form>
  );
}

function DecidedCase({ c, onDone }: { c: ReviewCase; onDone: () => void }) {
  const { notify } = useApp();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const check = c.check;

  async function again() {
    setBusy(true);
    try {
      const r = await api<ReviewCase>(`/api/admin/review/${c.id}/check`, { method: "POST" });
      notify(r.check.passed ? "Answered right" : "Not right yet");
      onDone();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't check it", "error");
    } finally {
      setBusy(false);
    }
  }

  async function reopen() {
    try {
      await api(`/api/admin/review/${c.id}`, { method: "DELETE" });
      notify("Back in the queue");
      onDone();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't reopen it", "error");
    }
  }

  const state = check.passed === true ? "pass" : check.passed === false ? "fail" : "unknown";
  return (
    <li className={`list-card review-case ${state}`}>
      <div className="list-card-main">
        <div className="row wrap">
          <strong className="review-q">“{c.question}”</strong>
          <span className={`status-pill ${state === "pass" ? "ok" : state === "fail" ? "warn" : ""}`}>
            {state === "pass" ? "Answered right" : state === "fail" ? "Not right yet" : "Can't check"}
          </span>
        </div>
        <p className="muted small">
          {c.expect === "abstain"
            ? "Nothing answers this; it should say so."
            : `Answered by ${c.docs.map((d) => `“${d.title}”`).join(" or ")}${c.says.length ? `, saying ${c.says.map((x) => `“${x}”`).join(", ")}` : ""}.`}
          {check.at && <> Checked {timeAgo(check.at)}.</>}
        </p>
        {check.problems && check.problems.length > 0 && (
          <ul className="review-problems small">
            {check.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
        {check.got && (
          <p className="muted small">
            Now: {MODE[check.got.mode] ?? "answer"}
            {check.got.cited.length > 0 && <> using {check.got.cited.map((d) => `“${d.title}”`).join(", ")}</>}
            {check.got.enough === false && <>, saying there isn't enough evidence</>}.
          </p>
        )}
      </div>
      <div className="row wrap end">
        <button className="btn small" onClick={again} disabled={busy}>
          {busy ? "Checking…" : "Check again"}
        </button>
        {c.expect === "answer" && (
          <button
            className="btn small"
            onClick={() => navigate("/admin/verified", { state: { draft: { question: c.question, doc_ids: c.docs.map((d) => d.id) } } })}
          >
            Make a verified answer
          </button>
        )}
        <button className="btn small ghost" onClick={reopen}>
          Reopen
        </button>
      </div>
    </li>
  );
}
