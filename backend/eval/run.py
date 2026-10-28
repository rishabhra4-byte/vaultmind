"""Measure VaultMind's search and answers against the benchmark in eval/cases.py.

    cd backend
    python -m eval.run                      # report on every case
    python -m eval.run --split test         # only the held-out cases
    python -m eval.run --out report.json    # also write every case's details
    python -m eval.run --check              # fail if anything got worse than eval/baseline.json
    python -m eval.run --save-baseline      # record the current numbers as the baseline

It builds a throwaway copy of the demo company (SQLite, demo data), asks every question as the
person in the case, and scores the replies. Answers are built offline (no AI service), so the
numbers are deterministic and need no API key; with --ai and VAULTMIND_ANTHROPIC_API_KEY set,
the same checks run on the model's answers.
"""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "baseline.json"

# Metrics compared against the baseline by --check: (key, higher is better).
TRACKED = [
    ("recall_at_5", True),
    ("mrr", True),
    ("ndcg_at_10", True),
    ("answer_accuracy", True),
    ("citation_precision", True),
    ("abstain_recall", True),
    ("false_abstain_rate", False),
    ("deny_accuracy", True),
    ("conflict_recall", True),
    ("outdated_recall", True),
]
TOLERANCE = 0.01


def _setup_environment(ai: bool) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="vaultmind-eval-"))
    (tmp / "shared").mkdir()
    os.environ.update(
        {
            "VAULTMIND_DATABASE_URL": f"sqlite:///{tmp}/eval.db",
            "VAULTMIND_STORAGE_DIR": str(tmp / "storage"),
            "VAULTMIND_SEED_DEMO_DATA": "true",
            "VAULTMIND_ENV": "development",
            "VAULTMIND_SCHEDULER_ENABLED": "false",
            "VAULTMIND_JWT_SECRET": "eval-secret-that-is-long-enough-for-hs256",
            "VAULTMIND_FOLDER_ROOTS": json.dumps([str(tmp / "shared")]),
            "VAULTMIND_MALWARE_SCANNER": "",
        }
    )
    if not ai:
        os.environ["VAULTMIND_ANTHROPIC_API_KEY"] = ""
    return tmp


# ---------------------------------------------------------------- text checks


def normal(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[`*_]", "", text.lower())
    return re.sub(r"\s+", " ", text).strip()


_TOKEN = re.compile(r"[a-z0-9]+")
SHINGLE = 7  # words in a row that count as quoting a document


def shingles(text: str) -> set[tuple[str, ...]]:
    words = _TOKEN.findall(normal(text))
    return {tuple(words[i : i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def split_of(case_id: str) -> str:
    return "test" if int(hashlib.sha1(case_id.encode()).hexdigest(), 16) % 10 < 4 else "dev"


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def ndcg(ranked: list[int], grades: dict[int, int], k: int = 10) -> float:
    dcg = sum((2 ** grades.get(d, 0) - 1) / math.log2(i + 2) for i, d in enumerate(ranked[:k]))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def abstained(reply) -> bool:
    evidence = reply.meta.get("evidence")
    if isinstance(evidence, dict) and "enough" in evidence:
        return not evidence["enough"]
    return reply.hits == 0 and not reply.suggestions  # before the evidence check existed


# ---------------------------------------------------------------- the run


def run(split: str | None, ai: bool) -> dict:
    tmp = _setup_environment(ai)
    sys.path.insert(0, str(HERE.parent))
    try:
        return _run(tmp, split, ai)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: Path, split: str | None, ai: bool) -> dict:
    from sqlalchemy import select

    from app import assistant, retrieval
    from app.database import Base, SessionLocal, engine
    from app.llm import get_llm
    from app.models import User
    from app.policy import is_discoverable_to
    from app.seed import seed
    from app.services import access_view, document_text, rebuild_index

    from .cases import CASES

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
        rebuild_index(db)
    if ai and get_llm() is None:
        raise SystemExit("--ai needs VAULTMIND_ANTHROPIC_API_KEY")

    results: list[dict] = []
    errors: list[str] = []
    with SessionLocal() as db:
        users = {u.email.split("@")[0]: u for u in db.scalars(select(User))}
        first_view = access_view(db, users["dev"])  # an admin's view: every document
        by_title = {d.title: d.id for d in first_view.docs.values()}
        text_of = {d.id: document_text(d) for d in first_view.docs.values()}
        doc_shingles = {i: shingles(t) for i, t in text_of.items()}

        for case in CASES:
            case_split = split_of(case.id)
            if split and case_split != split:
                continue
            user = users.get(case.who)
            missing = [t for t in (*case.docs, *(case.conflict or ()), *case.outdated, *case.locked) if t not in by_title]
            if user is None or missing:
                errors.append(f"{case.id}: unknown person or document {missing or case.who}")
                continue
            view = access_view(db, user)
            ids = [by_title[t] for t in case.docs]
            if case.expect == "answer" and ids and ids[0] not in view.allowed:
                errors.append(f"{case.id}: {case.who} can't open {case.docs[0]}, so it can't be an 'answer' case")
            if case.expect == "deny" and ids and ids[0] in view.allowed:
                errors.append(f"{case.id}: {case.who} can open {case.docs[0]}, so it isn't a 'deny' case")

            t0 = time.perf_counter()
            ranking = retrieval.rank(db, view, case.q, top_k=10)
            rank_ms = (time.perf_counter() - t0) * 1000
            ranked = [h.doc_id for h in ranking.hits]

            t0 = time.perf_counter()
            reply = assistant.respond(db, user, case.q, [])
            answer_ms = (time.perf_counter() - t0) * 1000
            db.rollback()  # nothing a reply might have staged is kept between cases

            meta = reply.meta
            blob = normal(reply.content + "\n" + json.dumps(meta, ensure_ascii=False))
            cited = [c["id"] for c in reply.citations]
            row: dict = {
                "id": case.id,
                "split": case_split,
                "expect": case.expect,
                "tags": list(case.tags),
                "who": case.who,
                "ranked": ranked,
                "rank_ms": round(rank_ms, 2),
                "answer_ms": round(answer_ms, 2),
                "mode": reply.mode,
                "cited": cited,
                "abstained": abstained(reply),
                "coverage": (reply.meta.get("evidence") or {}).get("coverage"),
                "missing": (reply.meta.get("evidence") or {}).get("missing"),
                "top": (reply.citations[0].get("snippet", "")[:160] if reply.citations else ""),
            }

            # Leaks: anything from a document this person can't open, in any part of the reply.
            leaks: list[str] = []
            outside = [d for d in (*ranked, *cited, *(s["id"] for s in reply.suggestions)) if d not in view.allowed]
            if outside:
                leaks.append(f"documents outside their access: {sorted(set(outside))}")
            for hint in reply.locked:
                doc = view.docs.get(hint["id"])
                if doc is None or hint["id"] in view.allowed or not is_discoverable_to(user, doc):
                    leaks.append(f"locked hint for a document that shouldn't be hinted: {hint['id']}")
            allowed_shingles: set = set().union(*(doc_shingles[i] for i in view.allowed if i in doc_shingles))
            forbidden = set().union(*(doc_shingles[i] for i in doc_shingles if i not in view.allowed)) - allowed_shingles
            quoted = shingles(reply.content + " " + " ".join(str(c.get("snippet", "")) for c in reply.citations)) & forbidden
            if quoted:
                leaks.append(f"quotes a document they can't open: {' '.join(next(iter(quoted)))}")
            for secret in case.secret:
                if normal(secret) in blob:
                    leaks.append(f"secret phrase: {secret}")
            row["leaks"] = leaks

            if case.expect == "answer":
                grades = {by_title[t]: (2 if i == 0 else 1) for i, t in enumerate(case.docs)}
                top5 = set(ranked[:5])
                row["recall_at_5"] = len(top5 & set(grades)) / len(grades)
                first = next((i for i, d in enumerate(ranked) if d in grades), None)
                row["rr"] = 1 / (first + 1) if first is not None else 0.0
                row["ndcg_at_10"] = ndcg(ranked, grades)
                supported = any(normal(s) in normal(reply.content) for s in case.says)
                relevant_cited = [c for c in cited if c in grades]
                row["says"] = supported
                row["cites_relevant"] = bool(relevant_cited)
                row["citation_precision"] = len(relevant_cited) / len(cited) if cited else 0.0
                row["correct"] = (not row["abstained"]) and supported and bool(relevant_cited) and not leaks
            elif case.expect == "abstain":
                row["correct"] = row["abstained"] and not leaks
            else:  # deny
                hinted = {h["title"] for h in reply.locked}
                row["hinted"] = all(t in hinted for t in case.locked)
                row["correct"] = not leaks and row["hinted"]

            conflicts = meta.get("conflicts") or []
            found_pairs = [{c["a"]["id"], c["b"]["id"]} for c in conflicts if "a" in c and "b" in c]
            row["conflicts"] = [sorted(p) for p in found_pairs]
            if case.conflict:
                want = {by_title[t] for t in case.conflict}
                row["conflict_found"] = any(p == want for p in found_pairs)
            outdated_cited = {c["id"] for c in reply.citations if c.get("outdated")}
            if case.outdated:
                row["outdated_found"] = all(by_title[t] in outdated_cited for t in case.outdated)
            results.append(row)

    freshness = _freshness(tmp)
    return {"summary": summarise(results), "by_tag": by_tag(results), "freshness": freshness, "errors": errors, "cases": results}


def _freshness(tmp: Path) -> dict:
    """How long after a sync starts a new or changed file can be found (a shared folder)."""
    from app import retrieval
    from app.database import SessionLocal
    from app.models import Source, SourceKind, Team, User
    from app.services import access_view
    from app.sync import sync_source

    folder = tmp / "shared" / "freshness"
    folder.mkdir(parents=True)
    out: dict = {}
    with SessionLocal() as db:
        admin = db.query(User).filter(User.email.like("dev@%")).one()
        team = db.query(Team).first()
        source = Source(
            kind=SourceKind.FOLDER.value,
            name="Freshness check",
            config={"path": str(folder), "rules": []},
            owner_team_id=team.id,
            classification="public",
            permission_mode="rules",
            sync_interval_minutes=60,
            created_by=admin.id,
            stats={},
        )
        db.add(source)
        db.commit()
        for label, word in (("new_file_seconds", "zanzibarquokka"), ("changed_file_seconds", "marmalademeridian")):
            (folder / "note.md").write_text(f"# Freshness note\n\nThe code word is {word}.\n")
            start = time.perf_counter()
            sync_source(db, source)
            db.commit()
            found = False
            while time.perf_counter() - start < 30:
                view = access_view(db, admin)
                hits = retrieval.rank(db, view, word, top_k=3).hits
                if hits and word in hits[0].chunks[0].text:
                    found = True
                    break
                time.sleep(0.05)
            out[label] = round(time.perf_counter() - start, 3) if found else None
    return out


def _rate(rows: list[dict], key: str) -> float | None:
    values = [r[key] for r in rows if key in r]
    return round(sum(values) / len(values), 4) if values else None


def summarise(rows: list[dict]) -> dict:
    answers = [r for r in rows if r["expect"] == "answer"]
    abstains = [r for r in rows if r["expect"] == "abstain"]
    denies = [r for r in rows if r["expect"] == "deny"]
    conflict_rows = [r for r in rows if "conflict_found" in r]
    outdated_rows = [r for r in rows if "outdated_found" in r]
    no_conflict = [r for r in rows if "no-conflict" in r["tags"]]
    return {
        "cases": len(rows),
        "recall_at_5": _rate(answers, "recall_at_5"),
        "mrr": _rate(answers, "rr"),
        "ndcg_at_10": _rate(answers, "ndcg_at_10"),
        "answer_accuracy": _rate(answers, "correct"),
        "answer_supported": _rate(answers, "says"),
        "citation_precision": _rate(answers, "citation_precision"),
        "abstain_recall": _rate(abstains, "correct"),
        "false_abstain_rate": round(sum(r["abstained"] for r in answers) / len(answers), 4) if answers else None,
        "deny_accuracy": _rate(denies, "correct"),
        "leak_rate": round(sum(bool(r["leaks"]) for r in rows) / len(rows), 4) if rows else None,
        "leaks": sum(bool(r["leaks"]) for r in rows),
        "conflict_recall": _rate(conflict_rows, "conflict_found"),
        "false_conflicts_on_no_conflict_cases": sum(bool(r["conflicts"]) for r in no_conflict),
        "outdated_recall": _rate(outdated_rows, "outdated_found"),
        "rank_ms_p50": round(percentile([r["rank_ms"] for r in rows], 0.5), 2),
        "rank_ms_p95": round(percentile([r["rank_ms"] for r in rows], 0.95), 2),
        "answer_ms_p50": round(percentile([r["answer_ms"] for r in rows], 0.5), 2),
        "answer_ms_p95": round(percentile([r["answer_ms"] for r in rows], 0.95), 2),
    }


def by_tag(rows: list[dict]) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        for t in r["tags"] or ["untagged"]:
            groups[t].append(r)
    return {
        t: {"cases": len(g), "correct": _rate(g, "correct"), "recall_at_5": _rate(g, "recall_at_5")}
        for t, g in sorted(groups.items())
    }


# ---------------------------------------------------------------- reporting


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def print_report(report: dict, label: str) -> None:
    s = report["summary"]
    print(f"\nVaultMind answer-quality benchmark ({label}): {s['cases']} cases\n")
    rows = [
        ("Recall@5", "recall_at_5"),
        ("MRR", "mrr"),
        ("nDCG@10", "ndcg_at_10"),
        ("Answer accuracy", "answer_accuracy"),
        ("Answer contains the fact", "answer_supported"),
        ("Citation precision", "citation_precision"),
        ("Abstains when nothing answers it", "abstain_recall"),
        ("Abstains when it shouldn't", "false_abstain_rate"),
        ("No-access questions handled", "deny_accuracy"),
        ("Permission leaks (cases)", "leaks"),
        ("Conflicts found", "conflict_recall"),
        ("Conflicts claimed where there are none", "false_conflicts_on_no_conflict_cases"),
        ("Out-of-date sources flagged", "outdated_recall"),
        ("Search p50 / p95 (ms)", None),
        ("Whole answer p50 / p95 (ms)", None),
    ]
    for name, key in rows:
        if key is None:
            pre = "rank" if name.startswith("Search") else "answer"
            value = f"{_fmt(s[pre + '_ms_p50'])} / {_fmt(s[pre + '_ms_p95'])}"
        else:
            value = _fmt(s[key])
        print(f"  {name:<42} {value}")
    f = report["freshness"]
    fresh = f"{_fmt(f.get('new_file_seconds'))} / {_fmt(f.get('changed_file_seconds'))}"
    print(f"  {'Searchable after a sync (new / changed file, s)':<42} {fresh}")
    print("\n  By kind of question:")
    for tag, g in report["by_tag"].items():
        print(f"    {tag:<14} {g['cases']:>4} cases   correct {_fmt(g['correct'])}   recall@5 {_fmt(g['recall_at_5'])}")
    if report["errors"]:
        print("\n  Problems with the cases themselves:")
        for e in report["errors"]:
            print("   -", e)
    leaks = [r for r in report["cases"] if r["leaks"]]
    for r in leaks:
        print(f"\n  LEAK in {r['id']} ({r['who']}): {'; '.join(r['leaks'])}")


def check(report: dict, baseline: dict) -> list[str]:
    problems = []
    now, before = report["summary"], baseline["summary"]
    if now["leaks"]:
        problems.append(f"{now['leaks']} case(s) leaked something the person can't open")
    if report["errors"]:
        problems.append(f"{len(report['errors'])} case(s) don't match the demo data")
    for key, higher in TRACKED:
        a, b = now.get(key), before.get(key)
        if a is None or b is None:
            continue
        worse = a < b - TOLERANCE if higher else a > b + TOLERANCE
        if worse:
            problems.append(f"{key} went from {b:.3f} to {a:.3f}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=["dev", "test"])
    parser.add_argument("--out", type=Path, help="write the full report (every case) as JSON")
    parser.add_argument("--check", action="store_true", help="fail if worse than eval/baseline.json")
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--ai", action="store_true", help="use the AI service (needs VAULTMIND_ANTHROPIC_API_KEY)")
    args = parser.parse_args(argv)

    report = run(args.split, args.ai)
    print_report(report, args.split or "all cases")
    if args.out:
        args.out.write_text(json.dumps(report, indent=1))
    if args.save_baseline:
        if args.split or args.ai:
            raise SystemExit("Save the baseline from an offline run over all cases")
        BASELINE.write_text(json.dumps({"summary": report["summary"], "by_tag": report["by_tag"]}, indent=1) + "\n")
        print(f"\nSaved {BASELINE.relative_to(HERE.parent)}")
    if args.check:
        baseline = json.loads(BASELINE.read_text())
        problems = check(report, baseline)
        if problems:
            print("\nWorse than the baseline:")
            for p in problems:
                print("  -", p)
            return 1
        print("\nNo regressions against the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
