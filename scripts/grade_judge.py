#!/usr/bin/env python3
"""
Layer 3 - LLM-as-judge scoring (GPT-5.5).

Three judge calls per rep, each emitting structured JSON:

  Judge A - SETTLED quality (1-5 per criterion):
      implementability, problem_solving, api_coherence, tradeoff_articulation

  Judge B - SIM fidelity (per-turn claim classification):
      For each SIM turn, classify each factual claim against the persona
      snippets as GROUNDED / HEDGED / FABRICATED. Reported separately from
      the composite (process check, not output quality).

  Judge C - Agent grilling quality (1-5 per criterion):
      question_sharpness, code_grounding, subagent_decomposition

Outputs: judge_report.json into the rep dir.

Cost: ~$0.03-0.06 per rep with gpt-5.5 (3 calls, each ~$0.01-0.02).

Usage:
    grade_judge.py <rep_dir>          # writes judge_report.json
    grade_judge.py <rep_dir> --print

Env:
    EVAL_PERSONA_PATH - override persona file (default: <repo>/personas/grilling_persona.md)
    EVAL_ENV_FILE     - override .env file (default: <repo>/.env)
    EVAL_JUDGE_MODEL  - override judge model (default: gpt-5.5)
    OPENAI_API_KEY    - required, read from EVAL_ENV_FILE
"""
import json
import os
import re
import sys
from pathlib import Path

from langchain_openai import ChatOpenAI

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
PERSONA_PATH = Path(os.environ.get(
    "EVAL_PERSONA_PATH", REPO_ROOT / "personas" / "grilling_persona.md"))
ENV_FILE = Path(os.environ.get("EVAL_ENV_FILE", REPO_ROOT / ".env"))

JUDGE_MODEL = os.environ.get("EVAL_JUDGE_MODEL", "gpt-5.5")


def load_env_var(name):
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError(f"{name} not found in {ENV_FILE}")


def parse_json_response(raw):
    """Strip code fences, find first { ... }, parse."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    # Try direct parse
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Find largest JSON-looking block
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


JUDGE_A_PROMPT = """You are scoring the quality of a SETTLED_DESIGN.md (an architecture handoff document for a DI-seam refactor).

DESIGN:
---
{settled}
---

Rate each criterion 1-5 (5 = excellent, 1 = poor):

- implementability: Could a SWE start implementation tomorrow without asking the design's author more questions? Are file paths, function signatures, and migration steps concrete enough?
- problem_solving: Does the design actually solve the DI-seam problem (replace two Prisma singletons + Anthropic singleton with a testable injection seam, no $disconnect() per request)?
- api_coherence: Is the public interface (types, function signatures) internally consistent? Does it preserve the existing call shapes routes use today?
- tradeoff_articulation: Does the design explain *why* this shape over alternatives? Are deferred decisions and out-of-scope items called out explicitly?

Reply with strict JSON only, no markdown:
{{"implementability": N, "problem_solving": N, "api_coherence": N, "tradeoff_articulation": N, "summary": "one sentence overall judgment (≤30 words)"}}"""


JUDGE_B_PROMPT = """You are auditing whether a simulated SWE persona stayed faithful to its snippet bundle in a multi-turn conversation. The simulator was instructed not to fabricate beyond what the snippets give it.

PERSONA SNIPPETS (what the SIM was given as ground truth):
---
{persona}
---

SIM TURNS (the simulator's messages, numbered):
{sim_turns}

For each SIM turn, classify EVERY distinct factual claim as one of:
- GROUNDED: traceable to a snippet, OR a clearly framed opinion / design preference (opinions are fine)
- HEDGED: an admitted uncertainty using phrases like "I don't recall", "I'd need to check", "my guess is", "I'm not sure", "no preference"
- FABRICATED: a positive factual claim NOT in the snippets, asserted without hedge (made-up backstory, invented numbers, fictional past incidents, claimed tooling preferences that the snippets don't grant, declared "intent" / "canonical" / "correct" when snippets are silent)

Reply with strict JSON only:
{{
  "turns": [
    {{"turn": 1, "grounded": N, "hedged": N, "fabricated": N, "fabrications": ["≤30-word verbatim quote 1", "..."]}},
    ...
  ],
  "totals": {{"grounded": N, "hedged": N, "fabricated": N}},
  "summary": "one sentence overall judgment (≤30 words)"
}}"""


JUDGE_C_PROMPT = """You are scoring the quality of the AGENT's grilling behavior in a multi-turn conversation about an architectural refactor.

AGENT TURNS (the agent's messages, numbered):
{agent_turns}

TOOL USE SUMMARY (counts from the run):
{tool_summary}

SUBAGENT DECOMPOSITIONS (titles only):
{subagent_titles}

Rate each criterion 1-5 (5 = excellent, 1 = poor):

- question_sharpness: Are the agent's questions specific and constraint-anchored ("does this design need to preserve HMR survival of the Prisma singleton?") rather than generic ("what's your testing strategy?")? Penalize hand-wavy questions.
- code_grounding: Do the agent's questions/statements show evidence of actually reading the code (cites file paths, line numbers, specific patterns from the repo) vs talking abstractly?
- subagent_decomposition: Did the agent spawn ≥3 INTERFACE-DESIGN parallel sub-agents with meaningfully different design constraints (minimal vs flexible vs ports/adapters etc.)? Penalize if 0-2 sub-agents or if all sub-agents got the same prompt.

Reply with strict JSON only:
{{"question_sharpness": N, "code_grounding": N, "subagent_decomposition": N, "summary": "one sentence overall judgment (≤30 words)"}}"""


def extract_sim_agent_turns(rep_dir):
    """Read trajectory.json + raw_export.jsonl + stats.json. Return sim/agent text + tool/subagent context."""
    traj = json.loads((rep_dir / "trajectory.json").read_text())
    msgs = traj.get("messages", [])
    sim_turns = []
    agent_turns = []
    for i, m in enumerate(msgs):
        role = m.get("role")
        content = m.get("content", "")
        if role == "user" and i > 0:  # skip seed
            sim_turns.append(content)
        elif role == "assistant":
            agent_turns.append(content)
    # Tool summary from stats
    stats = json.loads((rep_dir / "stats.json").read_text())
    tool_counts = stats.get("tool_counts", {})
    # Sub-agent titles from extracted/ subagents
    sub_dir = rep_dir / "subagents"
    titles = []
    if sub_dir.exists():
        for p in sorted(sub_dir.glob("agent_*.md")):
            # Title is the filename slug after agent_NN_
            stem = p.stem
            parts = stem.split("_", 2)
            if len(parts) >= 3:
                titles.append(parts[2].replace("_", " "))
    return sim_turns, agent_turns, tool_counts, titles


def main():
    rep_dir = Path(sys.argv[1])
    eval_dir = rep_dir / "eval"
    eval_dir.mkdir(exist_ok=True)
    if not (rep_dir / "extracted_files/SETTLED_DESIGN.md").exists():
        out = {"rep_dir": str(rep_dir), "skipped": "no_settled_design"}
        (eval_dir / "judge_report.json").write_text(json.dumps(out, indent=2))
        return

    settled_text = (rep_dir / "extracted_files/SETTLED_DESIGN.md").read_text()
    persona_text = PERSONA_PATH.read_text()

    # No-grilling reps (Option A baseline) have no trajectory.json - only
    # Judge A applies. Judge B (SIM fidelity) and Judge C (grilling) are
    # marked as not-applicable.
    has_trajectory = (rep_dir / "trajectory.json").exists()

    openai_key = load_env_var("OPENAI_API_KEY")
    client = ChatOpenAI(model=JUDGE_MODEL, api_key=openai_key, max_tokens=6000)

    # Judge A - SETTLED quality (always runs)
    raw_a = client.invoke(JUDGE_A_PROMPT.format(settled=settled_text[:9000])).content
    judge_a = parse_json_response(raw_a) or {"summary": "parse_failed", "raw": raw_a[:200]}

    if has_trajectory:
        sim_turns, agent_turns, tool_counts, sub_titles = extract_sim_agent_turns(rep_dir)
        # Judge B - SIM fidelity
        sim_turns_str = "\n\n".join(
            f"--- Turn {i+1} ({len(t)} chars) ---\n{t}"
            for i, t in enumerate(sim_turns)
        )
        raw_b = client.invoke(
            JUDGE_B_PROMPT.format(persona=persona_text[:6000], sim_turns=sim_turns_str[:8000])
        ).content
        judge_b = parse_json_response(raw_b) or {"summary": "parse_failed", "raw": raw_b[:200]}

        # Judge C - Agent grilling quality
        agent_turns_str = "\n\n".join(
            f"--- Turn {i+1} ({len(t)} chars) ---\n{t[:1500]}"
            for i, t in enumerate(agent_turns)
        )
        tool_summary = ", ".join(f"{k}={v}" for k, v in tool_counts.items())
        subagent_titles_str = "\n".join(f"- {t}" for t in sub_titles) or "(none)"
        raw_c = client.invoke(
            JUDGE_C_PROMPT.format(
                agent_turns=agent_turns_str[:8000],
                tool_summary=tool_summary,
                subagent_titles=subagent_titles_str,
            )
        ).content
        judge_c = parse_json_response(raw_c) or {"summary": "parse_failed", "raw": raw_c[:200]}
    else:
        # No SIM, no conversation - Judges B and C don't apply.
        judge_b = {"summary": "n/a - no-grilling baseline (no SIM)"}
        judge_c = {"summary": "n/a - no-grilling baseline (no conversation)"}

    # Aggregate
    def avg(d, keys):
        vals = [d[k] for k in keys if isinstance(d.get(k), (int, float))]
        return sum(vals) / len(vals) if vals else None

    judge_a_avg = avg(judge_a, ["implementability", "problem_solving",
                                 "api_coherence", "tradeoff_articulation"])
    judge_c_avg = avg(judge_c, ["question_sharpness", "code_grounding",
                                 "subagent_decomposition"])

    # SIM fidelity score (separate from composite)
    totals = judge_b.get("totals", {}) if isinstance(judge_b, dict) else {}
    g = totals.get("grounded", 0)
    h = totals.get("hedged", 0)
    f = totals.get("fabricated", 0)
    total_claims = g + h + f
    sim_fidelity_score = (g + h) / total_claims if total_claims > 0 else None

    summary = {
        "rep_dir": str(rep_dir),
        "judge_a_settled": judge_a,
        "judge_a_avg": judge_a_avg,
        "judge_b_sim_fidelity": judge_b,
        "sim_fidelity_score": sim_fidelity_score,
        "n_fabricated_claims": f,
        "judge_c_agent_grilling": judge_c,
        "judge_c_avg": judge_c_avg,
    }
    (eval_dir / "judge_report.json").write_text(json.dumps(summary, indent=2))

    if "--print" in sys.argv:
        print(f"=== {rep_dir.name} ===")
        print(f"  Judge A (SETTLED quality, 1-5 avg):    {judge_a_avg:.2f}" if judge_a_avg else "  Judge A: parse failed")
        print(f"    -> {judge_a.get('summary', '')}")
        print(f"  Judge B (SIM fidelity):                {sim_fidelity_score:.2%}" if sim_fidelity_score else "  Judge B: parse failed")
        print(f"    -> {f} fabricated / {total_claims} total claims")
        print(f"    -> {judge_b.get('summary', '')}")
        print(f"  Judge C (Agent grilling, 1-5 avg):     {judge_c_avg:.2f}" if judge_c_avg else "  Judge C: parse failed")
        print(f"    -> {judge_c.get('summary', '')}")


if __name__ == "__main__":
    main()
