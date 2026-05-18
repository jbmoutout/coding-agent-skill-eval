#!/usr/bin/env python3
"""
Layer 2 - Constraint coverage scoring of SETTLED_DESIGN.md against the
hand-authored DI-seam rubric.

Two-pass approach per constraint:
  Pass A: keyword pre-filter. Search the SETTLED for any of the constraint's
          search terms. If none match → NOT_ADDRESSED, no LLM call.
  Pass B: substantive check via small GPT-5.5 call with the constraint's
          verification question. Returns one of:
              ADDRESSED_CLEANLY / ADDRESSED_WEAKLY / NOT_ADDRESSED
          plus a short rationale + verbatim quote (≤200 chars).

This keeps cost low: ~5-9 LLM calls per rep (only constraints with at least
one keyword hit reach Pass B). Expected ~$0.02-0.05 per rep.

Usage:
    grade_constraints.py <rep_dir> [--rubric path/to/rubric.md]
    grade_constraints.py <rep_dir> --print

Env:
    EVAL_RUBRIC_PATH  - default rubric (overridden by --rubric)
    EVAL_ENV_FILE     - .env file (default: <repo>/.env)
    EVAL_JUDGE_MODEL  - judge model (default: gpt-5.5)
    OPENAI_API_KEY    - required, read from EVAL_ENV_FILE
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from langchain_openai import ChatOpenAI

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
DEFAULT_RUBRIC = Path(os.environ.get(
    "EVAL_RUBRIC_PATH",
    REPO_ROOT / "rubrics" / "grilling_di_seam_rubric.md",
))
ENV_FILE = Path(os.environ.get("EVAL_ENV_FILE", REPO_ROOT / ".env"))

JUDGE_MODEL = os.environ.get("EVAL_JUDGE_MODEL", "gpt-5.5")

SEVERITY_WEIGHTS = {"critical": 1.0, "important": 0.7, "nice-to-have": 0.4}
VERDICT_SCORES = {"ADDRESSED_CLEANLY": 1.0, "ADDRESSED_WEAKLY": 0.5, "NOT_ADDRESSED": 0.0}


def load_env_var(name):
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError(f"{name} not found in {ENV_FILE}")


def parse_rubric(text):
    """Parse the rubric markdown into a list of constraint dicts."""
    constraints = []
    sections = re.split(r"\n##\s+(\d+)\.\s+(\w+)\s*[--]\s*", text)
    # sections[0] is preamble; then triples (num, id, body)
    for i in range(1, len(sections), 3):
        if i + 2 >= len(sections):
            break
        num, cid, body = sections[i], sections[i + 1], sections[i + 2]
        # Cut at next section
        body = body.split("\n## ")[0].split("\n---")[0]
        severity = "important"
        sm = re.search(r"\*\*severity\*\*:\s*(critical|important|nice-to-have)", body)
        if sm:
            severity = sm.group(1)
        terms = []
        tm = re.search(r"\*\*search_terms\*\*:\s*([^\n]+)", body)
        if tm:
            terms = [t.strip().strip("`'\"") for t in tm.group(1).split(",")]
        question = ""
        qm = re.search(r"\*\*question\*\*:\s*([^\n]+)", body)
        if qm:
            question = qm.group(1).strip()
        constraints.append({
            "n": int(num), "id": cid, "severity": severity,
            "search_terms": terms, "question": question,
        })
    return constraints


def keyword_match(settled_text, terms):
    """Return list of terms that matched (case-insensitive substring)."""
    found = []
    low = settled_text.lower()
    for t in terms:
        if t.lower() in low:
            found.append(t)
    return found


JUDGE_PROMPT_TEMPLATE = """You are grading whether a software architecture design document substantively addresses a specific constraint that the design must satisfy.

CONSTRAINT QUESTION:
{question}

DESIGN DOCUMENT (SETTLED_DESIGN.md):
---
{settled}
---

Reply with strictly valid JSON (no markdown, no prose), with these keys:
- "verdict": one of "ADDRESSED_CLEANLY" | "ADDRESSED_WEAKLY" | "NOT_ADDRESSED"
- "rationale": one short sentence (max 30 words) explaining the verdict
- "quote": verbatim quote from the design supporting the verdict (max 200 chars), or empty string if NOT_ADDRESSED

Definitions:
- ADDRESSED_CLEANLY: explicit, substantive treatment with concrete shape, rationale, or sequencing
- ADDRESSED_WEAKLY: mentioned but vague, hand-waved, partial, or buried - would leave a SWE asking follow-up questions
- NOT_ADDRESSED: no mention OR contradicted by the design

Output JSON only."""


def judge_constraint(client, settled_text, constraint):
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        question=constraint["question"],
        settled=settled_text[:8000],  # truncate to keep prompt small
    )
    resp = client.invoke(prompt)
    raw = resp.content if hasattr(resp, "content") else str(resp)
    # Strip code fences if the judge wrapped them
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "verdict": "NOT_ADDRESSED",
            "rationale": f"judge JSON parse failed: {raw[:80]}",
            "quote": "",
        }
    if data.get("verdict") not in VERDICT_SCORES:
        data["verdict"] = "NOT_ADDRESSED"
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rep_dir", type=Path)
    ap.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC,
                    help=f"Rubric markdown (default: {DEFAULT_RUBRIC})")
    ap.add_argument("--print", action="store_true",
                    help="Also print summary to stdout.")
    args = ap.parse_args()

    rep_dir = args.rep_dir
    eval_dir = rep_dir / "eval"
    eval_dir.mkdir(exist_ok=True)
    settled_path = rep_dir / "extracted_files" / "SETTLED_DESIGN.md"
    if not settled_path.exists():
        out = {"rep_dir": str(rep_dir), "skipped": "no_settled_design"}
        (eval_dir / "constraints_report.json").write_text(json.dumps(out, indent=2))
        return

    settled_text = settled_path.read_text()
    constraints = parse_rubric(args.rubric.read_text())

    openai_key = load_env_var("OPENAI_API_KEY")
    client = ChatOpenAI(model=JUDGE_MODEL, api_key=openai_key, max_tokens=1200)

    results = []
    for c in constraints:
        matched = keyword_match(settled_text, c["search_terms"])
        if not matched:
            results.append({
                "id": c["id"], "severity": c["severity"],
                "verdict": "NOT_ADDRESSED",
                "rationale": "no keyword hits in SETTLED - short-circuited",
                "quote": "",
                "score": 0.0, "weight": SEVERITY_WEIGHTS[c["severity"]],
                "judge_called": False,
            })
            continue
        verdict_data = judge_constraint(client, settled_text, c)
        v = verdict_data["verdict"]
        results.append({
            "id": c["id"], "severity": c["severity"],
            "verdict": v,
            "rationale": verdict_data.get("rationale", ""),
            "quote": verdict_data.get("quote", ""),
            "score": VERDICT_SCORES[v],
            "weight": SEVERITY_WEIGHTS[c["severity"]],
            "judge_called": True,
            "keyword_matches": matched,
        })

    total_weight = sum(r["weight"] for r in results)
    weighted_score = sum(r["score"] * r["weight"] for r in results)
    coverage_score = weighted_score / total_weight if total_weight > 0 else 0.0

    summary = {
        "rep_dir": str(rep_dir),
        "coverage_score": round(coverage_score, 4),
        "n_constraints": len(results),
        "n_addressed_cleanly": sum(1 for r in results if r["verdict"] == "ADDRESSED_CLEANLY"),
        "n_addressed_weakly": sum(1 for r in results if r["verdict"] == "ADDRESSED_WEAKLY"),
        "n_not_addressed": sum(1 for r in results if r["verdict"] == "NOT_ADDRESSED"),
        "n_judge_calls": sum(1 for r in results if r["judge_called"]),
        "results": results,
    }
    (eval_dir / "constraints_report.json").write_text(json.dumps(summary, indent=2))

    if args.print:
        print(f"=== {rep_dir.name} - coverage_score={summary['coverage_score']} ===")
        print(f"  cleanly={summary['n_addressed_cleanly']} weakly={summary['n_addressed_weakly']} not={summary['n_not_addressed']} (judge_calls={summary['n_judge_calls']})")
        for r in results:
            sym = {"ADDRESSED_CLEANLY": "+", "ADDRESSED_WEAKLY": "~", "NOT_ADDRESSED": "-"}[r["verdict"]]
            print(f"  {sym} [{r['severity']:13}] {r['id']:25}  {r['rationale'][:80]}")


if __name__ == "__main__":
    main()
