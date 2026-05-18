#!/usr/bin/env python3
"""
Orchestrator: runs all 3 grader layers for one rep dir + composite + report.

Composite = 0.35 * facts_score
          + 0.30 * coverage_score
          + 0.20 * judge_a_avg / 5
          + 0.15 * judge_c_avg / 5
SIM fidelity reported separately (process check, not output quality).

Usage:
    grade_run.py <rep_dir> --facts-config <path>
    grade_run.py <rep_dir> --facts-config <path> --rubric <path>
    grade_run.py <rep_dir> --facts-config <path> --force

Env:
    EVAL_PYTHON - python interpreter used to spawn graders
                  (default: current sys.executable)
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
PYTHON = os.environ.get("EVAL_PYTHON", sys.executable)

sys.path.insert(0, str(SCRIPTS))
from model_names import resolve_cell  # noqa: E402

WEIGHTS = {
    "facts": 0.30,
    "coverage": 0.25,
    "judge_a": 0.20,
    "judge_c": 0.15,
    "sim_fidelity": 0.10,  # soft penalty for hallucinating SIMs (A)
}

# Validity-tier thresholds (B): a hard signal that the conversation was
# contaminated by the SIM, separate from the design quality score.
# Read alongside composite - a RED rep is "design may be shaped by
# fabricated input"; reader should not directly rank it against GREEN reps.
VALIDITY_TIER_RULES = [
    # (tier, max_fabricated, min_fidelity)
    ("GREEN", 2, 0.95),
    ("YELLOW", 5, 0.90),
    ("RED", None, None),  # catch-all
]


def validity_tier(sim_fidelity, n_fabricated, has_trajectory=True):
    """Return (tier_label, reason_str). sim_fidelity may be None (judge B
    parse-fail OR no-grilling baseline); distinguish those two cases."""
    if not has_trajectory:
        return "N/A", "no-grilling baseline - no SIM, so SIM fidelity doesn't apply"
    if sim_fidelity is None or n_fabricated is None:
        return "UNKNOWN", "judge B parse-failed; SIM fidelity not measured"
    for tier, max_fab, min_fid in VALIDITY_TIER_RULES:
        if max_fab is None:  # RED catch-all
            return tier, (
                f"sim_fidelity={sim_fidelity:.1%} "
                f"({n_fabricated} fabricated claims) - design may be shaped by "
                f"fabricated input; not directly comparable to higher-tier reps"
            )
        if n_fabricated <= max_fab and sim_fidelity >= min_fid:
            reason = (
                f"sim_fidelity={sim_fidelity:.1%}, "
                f"{n_fabricated} fabricated claim(s)"
            )
            return tier, reason


def run_grader(name, script, rep_dir, extra_args=None):
    eval_dir = rep_dir / "eval"
    eval_dir.mkdir(exist_ok=True)
    out_file = eval_dir / f"{name}_report.json"
    if out_file.exists():
        print(f"  {name}: cached -> eval/{out_file.name}")
        return json.loads(out_file.read_text())
    print(f"  {name}: running...")
    cmd = [PYTHON, str(SCRIPTS / script), str(rep_dir)]
    if extra_args:
        cmd.extend(extra_args)
    r = subprocess.run(
        cmd, capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        print(f"    ERROR: {r.stderr[:200]}")
        return None
    return json.loads(out_file.read_text())


def composite_score(facts, constraints, judge):
    if not (facts and constraints and judge):
        return None
    if facts.get("skipped") or constraints.get("skipped") or judge.get("skipped"):
        return None
    fs = facts.get("facts_score")
    cs = constraints.get("coverage_score")
    ja = judge.get("judge_a_avg")
    jc = judge.get("judge_c_avg")
    if None in (fs, cs, ja):
        return None
    # SIM fidelity: prefer measured, fall back to 1.0 if Judge B parse-failed
    # (validity_tier UNKNOWN flag conveys that separately).
    sim = judge.get("sim_fidelity_score")
    sim_term = sim if sim is not None else 1.0
    # No-grilling reps have no judge_c (no conversation to grade). Re-normalize
    # weights over the applicable terms so composites are comparable.
    if jc is None:
        applicable = WEIGHTS["facts"] + WEIGHTS["coverage"] + WEIGHTS["judge_a"] + WEIGHTS["sim_fidelity"]
        return (
            WEIGHTS["facts"] * fs +
            WEIGHTS["coverage"] * cs +
            WEIGHTS["judge_a"] * (ja / 5.0) +
            WEIGHTS["sim_fidelity"] * sim_term
        ) / applicable
    return (
        WEIGHTS["facts"] * fs +
        WEIGHTS["coverage"] * cs +
        WEIGHTS["judge_a"] * (ja / 5.0) +
        WEIGHTS["judge_c"] * (jc / 5.0) +
        WEIGHTS["sim_fidelity"] * sim_term
    )


def render_md(rep_dir, facts, constraints, judge, composite,
              tier="UNKNOWN", tier_reason=""):
    # Strip both possible prefixes: "arch-001-grilling-" and "arch-001-no-grilling-".
    cell_short = (rep_dir.parent.name
                  .replace("arch-001-no-grilling-", "no-grilling-")
                  .replace("arch-001-grilling-", ""))
    mn = resolve_cell(cell_short)
    name = rep_dir.parent.name + "/" + rep_dir.name
    tier_emoji = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴", "UNKNOWN": "⚪", "N/A": "⬜"}.get(tier, "⚪")
    lines = [
        f"# Eval report - {name}",
        "",
        f"**Models**: Agent = `{mn['agent']}`, SIM = `{mn['sim']}`, Persona = `{mn['persona']}`",
        "",
        f"## Composite score: **{composite:.3f}**  {tier_emoji} {tier}" if composite else f"## Composite: skipped (incomplete)  {tier_emoji} {tier}",
        f"_Validity:_ {tier_reason}",
        "",
        "## Layer 1 - Mechanical facts",
        f"- facts_score: **{facts.get('facts_score')}**" if facts and not facts.get('skipped') else "- skipped",
    ]
    if facts and not facts.get("skipped"):
        lines.append(f"- pass={facts['n_pass']} fail={facts['n_fail']} unverifiable={facts['n_unverifiable']}")
        for r in facts.get("results", []):
            sym = {"PASS": "✓", "FAIL": "✗", "UNVERIFIABLE": "?"}[r["verdict"]]
            lines.append(f"  - {sym} [{r['category']}] {r['claim']}")

    lines += ["", "## Layer 2 - Constraint coverage"]
    if constraints and not constraints.get("skipped"):
        lines.append(f"- coverage_score: **{constraints['coverage_score']}**")
        lines.append(f"- cleanly={constraints['n_addressed_cleanly']} weakly={constraints['n_addressed_weakly']} not={constraints['n_not_addressed']}")
        for r in constraints.get("results", []):
            sym = {"ADDRESSED_CLEANLY": "✓", "ADDRESSED_WEAKLY": "~", "NOT_ADDRESSED": "✗"}[r["verdict"]]
            lines.append(f"  - {sym} [{r['severity']:13}] **{r['id']}** - {r['rationale']}")
            if r.get("quote"):
                lines.append(f"    > {r['quote'][:200]}")
    else:
        lines.append("- skipped")

    lines += ["", "## Layer 3 - LLM judge"]
    if judge and not judge.get("skipped"):
        lines.append(f"- Judge A (SETTLED quality): **{judge.get('judge_a_avg'):.2f} / 5**")
        ja = judge.get("judge_a_settled", {})
        lines.append(f"  - implementability={ja.get('implementability')}, problem_solving={ja.get('problem_solving')}, api_coherence={ja.get('api_coherence')}, tradeoff_articulation={ja.get('tradeoff_articulation')}")
        lines.append(f"  - {ja.get('summary', '')}")
        sf = judge.get("sim_fidelity_score")
        fab = judge.get("n_fabricated_claims", 0)
        lines.append(f"- Judge B (SIM fidelity, separate): **{sf:.1%}** ({fab} fabricated claims)" if sf is not None else "- Judge B: parse failed")
        jb = judge.get("judge_b_sim_fidelity", {})
        if isinstance(jb, dict):
            lines.append(f"  - {jb.get('summary', '')}")
            for t in jb.get("turns", []):
                if t.get("fabricated", 0) > 0:
                    lines.append(f"  - Turn {t['turn']}: {t['fabricated']} fabrication(s)")
                    for q in t.get("fabrications", []):
                        lines.append(f"    > {q[:200]}")
        jc_avg = judge.get("judge_c_avg")
        if jc_avg is not None:
            lines.append(f"- Judge C (Agent grilling): **{jc_avg:.2f} / 5**")
            jc = judge.get("judge_c_agent_grilling", {})
            lines.append(f"  - question_sharpness={jc.get('question_sharpness')}, code_grounding={jc.get('code_grounding')}, subagent_decomposition={jc.get('subagent_decomposition')}")
            lines.append(f"  - {jc.get('summary', '')}")
        else:
            jc = judge.get("judge_c_agent_grilling", {})
            lines.append(f"- Judge C (Agent grilling): _{jc.get('summary', 'n/a')}_")
    else:
        lines.append("- skipped")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rep_dir", type=Path)
    ap.add_argument("--facts-config", type=Path, required=True,
                    help="JSON config of task-specific ground truth for grade_facts.py")
    ap.add_argument("--rubric", type=Path, default=None,
                    help="Constraint rubric for grade_constraints.py (uses default if omitted)")
    ap.add_argument("--force", action="store_true",
                    help="Re-run even if cached reports exist")
    args = ap.parse_args()

    rep_dir = args.rep_dir
    eval_dir = rep_dir / "eval"
    eval_dir.mkdir(exist_ok=True)

    if args.force:
        for f in ("facts_report.json", "constraints_report.json",
                  "judge_report.json", "eval_report.json", "eval_report.md"):
            (eval_dir / f).unlink(missing_ok=True)

    # Strip both possible prefixes: "arch-001-grilling-" and "arch-001-no-grilling-".
    cell_short = (rep_dir.parent.name
                  .replace("arch-001-no-grilling-", "no-grilling-")
                  .replace("arch-001-grilling-", ""))
    mn = resolve_cell(cell_short)
    print(f"=== {rep_dir.parent.name}/{rep_dir.name} ===")
    print(f"    {mn['human']}")

    constraints_args = []
    if args.rubric is not None:
        constraints_args = ["--rubric", str(args.rubric)]

    facts = run_grader(
        "facts", "grade_facts.py", rep_dir,
        extra_args=["--config", str(args.facts_config)],
    )
    constraints = run_grader(
        "constraints", "grade_constraints.py", rep_dir,
        extra_args=constraints_args or None,
    )
    judge = run_grader("judge", "grade_judge.py", rep_dir)

    composite = composite_score(facts, constraints, judge)

    # Validity tier (B): a separate flag, not folded into composite.
    sim_fid = (judge or {}).get("sim_fidelity_score") if judge else None
    n_fab = (judge or {}).get("n_fabricated_claims") if judge else None
    has_trajectory = (rep_dir / "trajectory.json").exists()
    tier, tier_reason = validity_tier(sim_fid, n_fab, has_trajectory)

    report = {
        "rep_dir": str(rep_dir),
        "cell": cell_short,
        "agent_model": mn["agent"],
        "sim_model": mn["sim"],
        "persona": mn["persona"],
        "composite_score": composite,
        "validity_tier": tier,
        "validity_reason": tier_reason,
        "weights": WEIGHTS,
        "facts": facts,
        "constraints": constraints,
        "judge": judge,
    }
    (eval_dir / "eval_report.json").write_text(json.dumps(report, indent=2))
    (eval_dir / "eval_report.md").write_text(
        render_md(rep_dir, facts, constraints, judge, composite,
                  tier, tier_reason)
    )
    print(f"  composite: {composite:.3f}  [{tier}]" if composite else f"  composite: SKIPPED  [{tier}]")
    print(f"  validity: {tier_reason}")
    print("  → eval/eval_report.json + eval/eval_report.md")


if __name__ == "__main__":
    main()
