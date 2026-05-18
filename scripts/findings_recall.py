#!/usr/bin/env python3
"""
findings_recall.py - FActScore-style per-finding entailment verification.

For each positive_finding in <task-id>.findings.json, ask an LLM judge:
  "Given the agent's advisory output, does it surface the architectural
   issue described by `atomic_claim` at `location`? Return structured."

For each negative_finding (false-positive trap), ask the symmetric question:
  "Does the agent assert this false claim? Is the assertion hedged?"

Per-finding atomic judge call:
- Isolated judge per criterion - no batching across findings
- Structured output (form-filling) - JSON with fixed fields
- "Way out" - judge can return Unknown if evidence insufficient
- Evidence span required - sanity-check vs verbosity bias

Calibration biases NOT addressed by this script (need pipeline-level):
- Self-preference / family bias - run with multiple judge families
- Self-consistency - run 3x on calibration set
- Catalog quality - done by hand via audit_log (see schema v1.2)

Replaces the v1 regex/keyword detector (which was BLEU/ROUGE-class and
overfit to one transcript). FActScore: Min et al. 2023, arxiv 2305.14251.

Usage:
  pip install anthropic   # or: pip install openai
  findings_recall.py <results-dir> <findings.json> [--judge anthropic|openai] [--model MODEL]

Env:
  ANTHROPIC_API_KEY or OPENAI_API_KEY must be set.

Reads:
  <results-dir>/raw_export.jsonl       (final text event)
  <findings.json>                       (catalog v1.2+)

Writes:
  <results-dir>/findings_recall.json    (per-finding verdicts + recall + FP count)
  <results-dir>/findings_recall.judge_log.jsonl   (raw judge prompts + responses)
"""
import argparse
import json
import pathlib
import sys
import time


JUDGE_SYSTEM_POSITIVE = """You are a careful adversarial evaluator. Your job is to determine whether a given piece of advisory text surfaces a specific architectural finding.

You will be given:
1. An ATOMIC CLAIM describing a specific architectural issue at a specific location in a codebase.
2. The AGENT OUTPUT - a long-form architectural review.

Your task: decide whether the agent's output **describes and discusses** the issue named in the atomic claim. The agent does NOT need to use the same wording - paraphrase counts. But the agent MUST be discussing the same architectural issue, not merely mentioning the same file or counting the issue's existence.

Return JSON with these fields (no markdown, no preamble):
- surfaced: "yes" | "no" | "unknown"
- evidence_span: if "yes", a verbatim quote from the agent output (≤300 chars) showing the surfacing. If "no" or "unknown", null.
- evidence_quality: "explicit" (issue described clearly with the defect named) | "implicit" (issue described in different terms but the defect is unmistakable) | "weak" (file or area mentioned, defect arguable) | "absent"
- confidence: "high" | "med" | "low"
- reasoning: one sentence

STRICT RULES - apply these before "yes":

1. **Mentioning the file alone is NOT surfacing.** A file path like `api/users/route.ts` being listed doesn't surface a defect in that file.
2. **Counting the issue's existence in a tally is NOT surfacing.** If the agent writes "13 inline-verify, 5 missing entirely, 1 commented out" - that counts the defect's existence in a per-route inventory, but does NOT describe the defect. Mark as "no" or "weak" (at most), not "yes".
3. **A related-but-different observation is NOT surfacing the catalog's claim.** If the catalog says "matcher includes stale route X" and the agent says "matcher is misleading because it misses many routes" - that's a different middleware concern. Different architectural defect. Mark as "no".
4. **The agent must describe the defect's character** (what's wrong with the code), not merely point at where it lives.

Soft rules:
- Partial surfacing of sub_claims (if provided) → "yes" with evidence_quality "implicit" or "weak".
- Use "unknown" if the agent's text is genuinely ambiguous between two readings; do not guess.
- The agent's output is the ONLY source of truth - do not infer from the location what the agent "must have" said.

When in doubt between "yes/weak" and "no", default to "no" and explain in reasoning. Surfacing has a specific meaning: the agent's reader would learn about this defect from reading the agent's text."""


JUDGE_SYSTEM_NEGATIVE = """You are a careful adversarial evaluator. Your job is to detect whether a given piece of advisory text falsely asserts a specific claim that has been verified to be incorrect.

You will be given:
1. A TRAP CLAIM that is factually WRONG at this commit (and an explanation of why it's wrong).
2. The AGENT OUTPUT - a long-form architectural review.

Your task: decide whether the agent asserts the trap claim, and whether the assertion is hedged.

Return JSON with these fields (no markdown, no preamble):
- asserted: "yes" | "no" | "unknown"
- hedged: "yes" | "no" | "n/a"  (n/a if asserted=no)
- evidence_span: if asserted=yes, the verbatim quote from the agent (≤300 chars). Otherwise null.
- hedge_quote: if hedged=yes, the verbatim hedge language. Otherwise null.
- confidence: "high" | "med" | "low"
- reasoning: one sentence

Hedge examples (partial mitigation, hedged=yes):
- "or used by something I didn't trace"
- "appears unused" (vs the unhedged "is unused")
- "may be dead" (vs "is dead")
- "I couldn't find imports" (vs "has no imports")

Unhedged = definitive claim of the trap. Hedged = qualified language.

Use "unknown" only if the agent's text is genuinely ambiguous."""


def call_anthropic(model, system, user, max_tokens=1024):
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return {
        "response_text": msg.content[0].text,
        "usage": {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens},
        "model": msg.model,
    }


def call_openai(model, system, user, max_tokens=1024):
    import os
    from openai import OpenAI
    if os.environ.get("OPENROUTER_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"],
                        base_url="https://openrouter.ai/api/v1")
    else:
        client = OpenAI()
    # GPT-5.x and o1+ require max_completion_tokens; older models use max_tokens.
    needs_completion_tokens = any(p in model for p in ["gpt-5", "o1", "o3", "o4"])
    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    if needs_completion_tokens:
        kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["max_tokens"] = max_tokens
    resp = client.chat.completions.create(**kwargs)
    return {
        "response_text": resp.choices[0].message.content,
        "usage": {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens},
        "model": resp.model,
    }


def parse_judge_json(text):
    """Robust JSON extraction - strips ``` fences and any preamble."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json\n"):
            t = t[5:]
        if "```" in t:
            t = t.rsplit("```", 1)[0]
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        t = t[start:end+1]
    return json.loads(t)


def verify_positive(finding, agent_text, call_fn, model, log):
    atomic = finding["atomic_claim"]
    location = finding.get("location_primary") or finding.get("location") or "(unspecified)"
    sub_claims = finding.get("sub_claims", [])
    sub_block = ""
    if sub_claims:
        sub_block = "\n\nSUB-CLAIMS (any of these surfacing implies the atomic claim is surfaced):\n" + \
                    "\n".join(f"- {s}" for s in sub_claims)
    user = f"""ATOMIC CLAIM:
{atomic}

LOCATION (for context, not for credit):
{location}{sub_block}

AGENT OUTPUT:
\"\"\"
{agent_text}
\"\"\"

Return only the JSON object."""
    result = call_fn(model, JUDGE_SYSTEM_POSITIVE, user)
    log.write(json.dumps({
        "finding_id": finding["id"], "kind": "positive",
        "raw_response": result["response_text"],
        "usage": result["usage"], "model": result["model"],
    }) + "\n")
    try:
        parsed = parse_judge_json(result["response_text"])
    except Exception as e:
        parsed = {"surfaced": "unknown", "evidence_span": None, "evidence_quality": "absent",
                  "confidence": "low", "reasoning": f"judge response unparseable: {e}"}
    parsed["finding_id"] = finding["id"]
    parsed["_usage"] = result["usage"]
    return parsed


def verify_negative(finding, agent_text, call_fn, model, log):
    trap = finding["atomic_claim"]
    why_wrong = finding.get("why_wrong", "(unspecified)")
    user = f"""TRAP CLAIM (factually wrong at this commit):
{trap}

WHY IT'S WRONG:
{why_wrong}

AGENT OUTPUT:
\"\"\"
{agent_text}
\"\"\"

Return only the JSON object."""
    result = call_fn(model, JUDGE_SYSTEM_NEGATIVE, user)
    log.write(json.dumps({
        "finding_id": finding["id"], "kind": "negative",
        "raw_response": result["response_text"],
        "usage": result["usage"], "model": result["model"],
    }) + "\n")
    try:
        parsed = parse_judge_json(result["response_text"])
    except Exception as e:
        parsed = {"asserted": "unknown", "hedged": "n/a", "evidence_span": None,
                  "hedge_quote": None, "confidence": "low",
                  "reasoning": f"judge response unparseable: {e}"}
    parsed["finding_id"] = finding["id"]
    parsed["_usage"] = result["usage"]
    return parsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir")
    ap.add_argument("findings_json")
    ap.add_argument("--judge", choices=["anthropic", "openai"], default="anthropic")
    ap.add_argument("--model", default=None,
                    help="Anthropic default: claude-opus-4-7; OpenAI default: gpt-5")
    ap.add_argument("--text-file", default=None,
                    help="Path to text file to evaluate instead of the last text event in raw_export.jsonl (for synthesis_locus=subagent runs).")
    args = ap.parse_args()

    results = pathlib.Path(args.results_dir)
    catalog = json.loads(pathlib.Path(args.findings_json).read_text())

    if args.text_file:
        agent_text = pathlib.Path(args.text_file).read_text()
        text_source = args.text_file
    else:
        events = [json.loads(l) for l in (results / "raw_export.jsonl").read_text().splitlines() if l.strip()]
        text_events = [e for e in events if e["type"] == "text"]
        if not text_events:
            print("no text events", file=sys.stderr); sys.exit(1)
        agent_text = text_events[-1]["part"]["text"]
        text_source = "raw_export.jsonl last text event"

    if args.judge == "anthropic":
        model = args.model or "claude-opus-4-7"
        call_fn = call_anthropic
    else:
        model = args.model or "gpt-5"
        call_fn = call_openai

    log_path = results / "findings_recall.judge_log.jsonl"
    log = log_path.open("w")
    print(f"Judge: {args.judge}/{model}")
    print(f"Agent output: {len(agent_text)} chars")
    print(f"Catalog: {len(catalog['positive_findings'])} positives + {len(catalog['negative_findings'])} negatives")
    print()

    positive_verdicts = []
    for f in catalog["positive_findings"]:
        print(f"  positive: {f['id']} ... ", end="", flush=True)
        v = verify_positive(f, agent_text, call_fn, model, log)
        print(f"surfaced={v.get('surfaced')} quality={v.get('evidence_quality')}")
        positive_verdicts.append(v)
        time.sleep(0.5)

    negative_verdicts = []
    for f in catalog["negative_findings"]:
        print(f"  negative: {f['id']} ... ", end="", flush=True)
        v = verify_negative(f, agent_text, call_fn, model, log)
        print(f"asserted={v.get('asserted')} hedged={v.get('hedged')}")
        negative_verdicts.append(v)
        time.sleep(0.5)

    log.close()

    surfaced_yes = [v for v in positive_verdicts if v.get("surfaced") == "yes"]
    recall = len(surfaced_yes) / len(positive_verdicts) if positive_verdicts else 0
    asserted_unhedged = [v for v in negative_verdicts if v.get("asserted") == "yes" and v.get("hedged") == "no"]
    asserted_hedged = [v for v in negative_verdicts if v.get("asserted") == "yes" and v.get("hedged") == "yes"]
    total_in = sum(v.get("_usage", {}).get("input_tokens", 0) for v in positive_verdicts + negative_verdicts)
    total_out = sum(v.get("_usage", {}).get("output_tokens", 0) for v in positive_verdicts + negative_verdicts)

    out = {
        "task_id": catalog["task_id"],
        "run_id": results.name,
        "catalog_source": pathlib.Path(args.findings_json).name,
        "catalog_schema": catalog.get("schema_version", "?"),
        "text_source": text_source,
        "judge": {"provider": args.judge, "model": model},
        "total_positives": len(positive_verdicts),
        "positives_surfaced_count": len(surfaced_yes),
        "positives_surfaced_ids": [v["finding_id"] for v in surfaced_yes],
        "recall": round(recall, 3),
        "negatives_asserted_unhedged": [v["finding_id"] for v in asserted_unhedged],
        "negatives_asserted_hedged": [v["finding_id"] for v in asserted_hedged],
        "false_positive_count_unhedged": len(asserted_unhedged),
        "false_positive_count_hedged": len(asserted_hedged),
        "positive_verdicts": positive_verdicts,
        "negative_verdicts": negative_verdicts,
        "judge_total_input_tokens": total_in,
        "judge_total_output_tokens": total_out,
        "note": "Per-finding atomic entailment via LLM-as-judge (FActScore family). Single-judge, single-pass. Self-consistency (3x sampling) and cross-family validation are calibration-phase steps; not run here. Human-ratify before treating as authoritative.",
    }
    (results / "findings_recall.json").write_text(json.dumps(out, indent=2))

    summary = {
        "judge": f"{args.judge}/{model}",
        "recall": out["recall"],
        "positives_surfaced": out["positives_surfaced_ids"],
        "negatives_asserted_unhedged": out["negatives_asserted_unhedged"],
        "negatives_asserted_hedged": out["negatives_asserted_hedged"],
        "judge_tokens_in_out": [total_in, total_out],
    }
    print()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
