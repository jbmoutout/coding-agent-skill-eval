#!/usr/bin/env python3
"""Reconcile OpenRouter actual costs against harness-reported lower-bound costs.

For each rep listed in --runs-config, sums all OpenRouter activity rows whose
created_at falls within the rep's [start - 15s, end + 30s] window, then
compares against the harness's self-reported usd_lower_bound from
<rep>/score.json. Harness self-reports are typically several-X under for
opencode runs; this script makes the gap explicit.

Usage:
    reconcile_openrouter_costs.py
        --csv /path/to/openrouter_activity.csv
        --runs-config /path/to/runs.json
        [--results-root /path/to/results]

--runs-config schema:
    [
      {"rep": "<name>", "start": "ISO timestamp", "end": "ISO timestamp"},
      ...
    ]
    `start` and `end` mark the wall-clock window of the rep.

Env:
    EVAL_RESULTS_ROOT - default results root (overridden by --results-root)
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
DEFAULT_RESULTS_ROOT = Path(
    os.environ.get("EVAL_RESULTS_ROOT", REPO_ROOT / "results"))

BUFFER_BEFORE = timedelta(seconds=15)
BUFFER_AFTER = timedelta(seconds=30)


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.split(".")[0])


def load_csv(path: Path):
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                r["_ts"] = parse_ts(r["created_at"])
                r["_cost"] = float(r.get("cost_total") or 0)
                r["_cache_discount"] = float(r.get("cost_cache") or 0)
                r["_p"] = int(r.get("tokens_prompt") or 0)
                r["_c"] = int(r.get("tokens_completion") or 0)
                r["_cached"] = int(r.get("tokens_cached") or 0)
                rows.append(r)
            except (KeyError, ValueError):
                pass
    return rows


def reconcile(rows, runs, results_root):
    results = []
    matched_ids = set()
    for run in runs:
        name = run["rep"]
        t0 = parse_ts(run["start"]) - BUFFER_BEFORE
        t1 = parse_ts(run["end"]) + BUFFER_AFTER
        window = [r for r in rows if t0 <= r["_ts"] <= t1]
        cost = sum(r["_cost"] for r in window)
        discount = sum(r["_cache_discount"] for r in window)
        p = sum(r["_p"] for r in window)
        c = sum(r["_c"] for r in window)
        cached = sum(r["_cached"] for r in window)
        by_model = {}
        for r in window:
            m = r.get("model_permaslug", "?")
            by_model.setdefault(m, [0, 0.0])
            by_model[m][0] += 1
            by_model[m][1] += r["_cost"]
            matched_ids.add(r["generation_id"])

        score_path = results_root / name / "score.json"
        reported = None
        if score_path.exists():
            s = json.loads(score_path.read_text())
            reported = (s.get("cost", {})
                         .get("agent_run", {})
                         .get("usd_lower_bound"))

        ratio = (cost / reported) if reported else None
        cache_hit_ratio = (cached / p) if p else 0

        results.append({
            "run": name,
            "window_start": t0.isoformat(),
            "window_end": t1.isoformat(),
            "n_calls": len(window),
            "actual_cost_usd": round(cost, 4),
            "cache_discount_usd": round(discount, 4),
            "reported_lower_bound_usd": reported,
            "discrepancy_x": round(ratio, 2) if ratio else None,
            "tokens_prompt": p,
            "tokens_completion": c,
            "tokens_cached": cached,
            "real_cache_hit_ratio": round(cache_hit_ratio, 3),
            "by_model": {
                m: {"n": v[0], "cost": round(v[1], 4)}
                for m, v in by_model.items()
            },
        })
    return results, matched_ids


def print_summary(results, rows, matched_ids):
    header = (
        f"{'run':<45} {'reported':>10} {'actual':>10} "
        f"{'x':>6} {'calls':>6} {'cache_hit':>10}"
    )
    print(header)
    total_reported = 0
    total_actual = 0
    for r in results:
        reported = r["reported_lower_bound_usd"] or 0
        print(
            f"{r['run']:<45} {reported:>10.4f} "
            f"{r['actual_cost_usd']:>10.4f} "
            f"{str(r['discrepancy_x']) + 'x':>6} "
            f"{r['n_calls']:>6} "
            f"{r['real_cache_hit_ratio']:>10.3f}"
        )
        total_reported += reported
        total_actual += r["actual_cost_usd"]
    overall_x = (
        round(total_actual / total_reported, 2)
        if total_reported else "?"
    )
    print(
        f"\n{'TOTAL':<45} {total_reported:>10.4f} {total_actual:>10.4f} "
        f"{overall_x}x"
    )

    unmatched = [r for r in rows if r["generation_id"] not in matched_ids]
    unmatched_cost = sum(r["_cost"] for r in unmatched)
    print(
        f"\nUnmatched OpenRouter activity (outside all run windows): "
        f"n={len(unmatched)}, cost=${unmatched_cost:.4f}"
    )
    print("First 5 unmatched timestamps:")
    for r in unmatched[:5]:
        print(
            f"  {r['_ts'].isoformat()}  ${r['_cost']:.4f}  "
            f"{r.get('model_permaslug')}"
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, required=True,
                    help="OpenRouter activity export CSV.")
    ap.add_argument("--runs-config", type=Path, required=True,
                    help="JSON list of {rep, start, end} per run.")
    ap.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT,
                    help="Root containing <rep>/score.json files.")
    ap.add_argument("--output", type=Path, default=None,
                    help="Write reconciliation results JSON here "
                         "(default: <results-root>/reconcile_result.json).")
    args = ap.parse_args()

    if not args.csv.exists():
        print(f"CSV not found: {args.csv}", file=sys.stderr)
        sys.exit(2)

    rows = load_csv(args.csv)
    print(f"loaded {len(rows)} rows\n")

    runs = json.loads(args.runs_config.read_text())
    results, matched_ids = reconcile(rows, runs, args.results_root)

    print_summary(results, rows, matched_ids)

    out_path = args.output or (args.results_root / "reconcile_result.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "runs": results,
        "total_reported": round(
            sum((r["reported_lower_bound_usd"] or 0) for r in results), 4),
        "total_actual": round(
            sum(r["actual_cost_usd"] for r in results), 4),
    }, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
