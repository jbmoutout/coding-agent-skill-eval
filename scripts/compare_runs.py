#!/usr/bin/env python3
"""
Compare all rep dirs that have an eval_report.json. Produces a ranking table
sorted by composite score, plus per-axis breakdown + SIM fidelity.

Usage:
    compare_runs.py             # scans $EVAL_RESULTS_ROOT (default: <repo>/results/)
    compare_runs.py --md > ranking.md

Env:
    EVAL_RESULTS_ROOT - override default results directory.
"""
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
RESULTS = Path(os.environ.get("EVAL_RESULTS_ROOT", REPO_ROOT / "results"))

sys.path.insert(0, str(SCRIPTS))
from model_names import resolve_cell  # noqa: E402


def load_reports():
    reports = []
    # Reports live in <rep>/eval/eval_report.json (post-refactor layout).
    # Glob covers both arch-001-grilling-* AND arch-001-no-grilling-* cells.
    patterns = [
        "arch-001-grilling-*/rep*/eval/eval_report.json",
        "arch-001-no-grilling-*/rep*/eval/eval_report.json",
    ]
    for pat in patterns:
        for p in sorted(RESULTS.glob(pat)):
            try:
                r = json.loads(p.read_text())
                r["_path"] = str(p.parent.parent.relative_to(RESULTS))
                reports.append(r)
            except (json.JSONDecodeError, OSError):
                pass
    return reports


def cell_label(path):
    # Strip "arch-001-grilling-" prefix to keep table narrow
    return path.replace("arch-001-grilling-", "")


def main():
    reports = load_reports()
    if not reports:
        print("No eval_report.json files found.", file=sys.stderr)
        return

    rows = []
    for r in reports:
        composite = r.get("composite_score")
        facts = r.get("facts") or {}
        constraints = r.get("constraints") or {}
        judge = r.get("judge") or {}
        cell_short = (r["_path"].split("/")[0]
                      .replace("arch-001-no-grilling-", "no-grilling-")
                      .replace("arch-001-grilling-", ""))
        mn = resolve_cell(cell_short)
        rows.append({
            "cell_rep": cell_label(r["_path"]),
            "agent": mn["agent"],
            "sim": mn["sim"],
            "persona": mn["persona"],
            "human_label": mn["human"],
            "composite": composite,
            "tier": r.get("validity_tier", "UNKNOWN"),
            "facts": facts.get("facts_score"),
            "coverage": constraints.get("coverage_score"),
            "judge_a": judge.get("judge_a_avg"),
            "judge_c": judge.get("judge_c_avg"),
            "sim_fidelity": judge.get("sim_fidelity_score"),
            "n_fabricated": judge.get("n_fabricated_claims"),
        })
    # Sort: composite desc, None last
    rows.sort(key=lambda r: r["composite"] if r["composite"] is not None else -1, reverse=True)

    fmt = lambda v, dp=3: f"{v:.{dp}f}" if isinstance(v, (int, float)) else "-"
    pct = lambda v: f"{v:.1%}" if isinstance(v, (int, float)) else "-"

    TIER_EMOJI = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴", "UNKNOWN": "⚪", "N/A": "⬜"}
    md = "--md" in sys.argv
    if md:
        print("| Rank | Agent | SIM | Persona | Composite | Tier | Facts | Coverage | Judge A | Judge C | SIM fidelity | Fabricated |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(rows, 1):
            tier = r.get("tier", "UNKNOWN")
            print(f"| {i} | {r['agent']} | {r['sim']} | {r['persona']} | "
                  f"**{fmt(r['composite'])}** | {TIER_EMOJI[tier]} {tier} | "
                  f"{fmt(r['facts'], 2)} | {fmt(r['coverage'], 3)} | "
                  f"{fmt(r['judge_a'], 2)} | {fmt(r['judge_c'], 2)} | "
                  f"{pct(r['sim_fidelity'])} | "
                  f"{r['n_fabricated'] if r['n_fabricated'] is not None else '-'} |")
    else:
        print(f"{'#':>2}  {'agent':22} {'sim':22} {'persona':10} {'comp':>6} {'tier':>9} {'facts':>6} {'cov':>5} {'jA':>4} {'jC':>4} {'sim':>6} {'fab':>4}")
        print("-" * 134)
        for i, r in enumerate(rows, 1):
            tier = r.get("tier", "UNKNOWN")
            print(f"{i:>2}  {r['agent']:22} {r['sim']:22} {r['persona']:10} "
                  f"{fmt(r['composite']):>6} {TIER_EMOJI[tier] + tier:>9} "
                  f"{fmt(r['facts'], 2):>6} "
                  f"{fmt(r['coverage'], 3):>5} {fmt(r['judge_a'], 2):>4} "
                  f"{fmt(r['judge_c'], 2):>4} {pct(r['sim_fidelity']):>6} "
                  f"{r['n_fabricated'] if r['n_fabricated'] is not None else '-':>4}")


if __name__ == "__main__":
    main()
