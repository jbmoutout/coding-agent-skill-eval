#!/usr/bin/env python3
"""
tool_use.py - extract the tool-use panel from a run's raw_export.jsonl.

Per the rubric's "Tool-use metric definitions" section, some metrics are
mechanical and some are manual. This script:
- Auto-computes: tool_call_count, context_pollution_events, time_to_first_candidate,
  tokens_to_first_candidate, tool-by-tool breakdown, exploration breadth heuristics.
- Heuristic-flags: useful_call_ratio (matches each tool's output content against
  the final text; flags as candidate, not authoritative).
- Stubs for manual: exploration_convergence label, useful-call ratio confirmation.

Output: <results-dir>/tool_use.json with `extraction: "automatic" | "heuristic" |
"manual_pending"` per metric so noise-floor stays visible.

Usage:
  tool_use.py <results-dir>
"""
import argparse
import json
import pathlib
import re
import sys


def find_first_candidate_event(events):
    """Find the first event containing a candidate-shaped block (## 1. ... Files/Problem/Solution/Benefits)."""
    candidate_re = re.compile(r'^##\s+\d+\.\s+', re.MULTILINE)
    for e in events:
        if e["type"] == "text":
            text = e["part"].get("text", "")
            if candidate_re.search(text):
                return e
    return None


def cited_in_final(tool_output, final_text, min_len=20):
    """Heuristic: does any non-trivial substring of the tool output appear in the final text?

    Splits the tool output into lines, looks for any line ≥min_len chars that
    appears in the final text (substring match). Returns the matching line or None.
    """
    if not isinstance(tool_output, str):
        tool_output = str(tool_output)
    for line in tool_output.split("\n"):
        line = line.strip()
        if len(line) < min_len:
            continue
        # Skip pure noise lines (paths only would be too short to be useful)
        if line in final_text:
            return line[:120]
        # Try filename-level: extract file paths from tool output and see if any appear in final
    # Filename heuristic: extract paths like foo/bar/baz.ts, see if they're in final
    paths = re.findall(r'(?:[\w/-]+/)?[\w/.-]+\.(?:tsx?|jsx?|prisma|json|md)', tool_output)
    for p in paths:
        if len(p) >= 8 and p in final_text:
            return f"[path] {p}"
    return None


def count_file_reads(events):
    """Track which files were accessed how many times via read/bash/glob tools."""
    file_access = {}
    file_path_re = re.compile(r'(?:[\w/-]+/)?[\w/.-]+\.(?:tsx?|jsx?|prisma|json|md)')
    for e in events:
        if e["type"] == "tool_use":
            inp = e["part"]["state"].get("input", {})
            # Read tool: filePath
            if "filePath" in inp:
                fp = inp["filePath"]
                file_access[fp] = file_access.get(fp, 0) + 1
            # Bash/glob: look at command/pattern
            cmd = inp.get("command", "") or inp.get("pattern", "")
            for p in file_path_re.findall(cmd):
                file_access[p] = file_access.get(p, 0) + 1
    return file_access


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir")
    args = ap.parse_args()
    results = pathlib.Path(args.results_dir)
    events = [
        json.loads(line)
        for line in (results / "raw_export.jsonl").read_text().splitlines()
        if line.strip()
    ]

    if not events:
        print("no events", file=sys.stderr)
        sys.exit(1)

    # Final text
    text_events = [e for e in events if e["type"] == "text"]
    final_text = text_events[-1]["part"]["text"] if text_events else ""

    # Tool calls
    tool_calls = [e for e in events if e["type"] == "tool_use"]
    tool_call_count = len(tool_calls)

    # Tool breakdown
    by_tool = {}
    for e in tool_calls:
        t = e["part"]["tool"]
        by_tool[t] = by_tool.get(t, 0) + 1

    # Useful-call ratio (HEURISTIC)
    useful_per_call = []
    for e in tool_calls:
        tool = e["part"]["tool"]
        output = e["part"]["state"].get("output", "")
        match = cited_in_final(output, final_text) if output else None
        useful_per_call.append({
            "tool": tool,
            "callID": e["part"].get("callID", "?"),
            "input_summary": str(e["part"]["state"].get("input", {}))[:120],
            "output_chars": len(str(output)) if output else 0,
            "cited_match": match,
            "cited_heuristic": match is not None,
        })
    cited_count = sum(1 for c in useful_per_call if c["cited_heuristic"])
    useful_call_ratio_heuristic = round(cited_count / tool_call_count, 3) if tool_call_count else 0

    # Time-to-first-candidate
    first_cand_event = find_first_candidate_event(events)
    if first_cand_event:
        wall_to_first_cand = (first_cand_event["timestamp"] - events[0]["timestamp"]) / 1000
    else:
        wall_to_first_cand = None

    # Tokens-to-first-candidate (SUM of per-step step_finish.tokens.total for all
    # steps that completed before the first candidate event).
    # Per-step, not cumulative - opencode reports tokens per step_finish.
    tokens_to_first_cand = None
    if first_cand_event:
        running_total = 0
        for e in events:
            if e is first_cand_event:
                break
            if e["type"] == "step_finish":
                running_total += e["part"]["tokens"]["total"]
        tokens_to_first_cand = running_total

    # Context-pollution events: compaction-type events, re-reads of same file
    compaction_event_count = sum(1 for e in events if "compact" in e.get("type", "").lower() or "compact" in str(e.get("part", {})).lower()[:200])
    file_access = count_file_reads(events)
    re_reads = sum(max(0, n - 1) for n in file_access.values())  # any file read >1x counts as 1 re-read each extra time

    # Exploration breadth heuristic
    distinct_files = len(file_access)
    total_accesses = sum(file_access.values())
    breadth = round(distinct_files / total_accesses, 3) if total_accesses else 0
    # exploration_convergence label: heuristic guess
    if total_accesses == 0:
        conv_heuristic = "no_exploration"
    elif breadth >= 0.9:
        conv_heuristic = "narrowed"  # every access was a distinct file → no thrashing
    elif breadth >= 0.6:
        conv_heuristic = "mostly_narrowed"
    elif breadth >= 0.3:
        conv_heuristic = "mixed"
    else:
        conv_heuristic = "thrashed"  # many re-reads of same files

    # Wall time
    wall_seconds = (events[-1]["timestamp"] - events[0]["timestamp"]) / 1000

    out = {
        "run_id": results.name,
        "schema": "tool_use v1",
        "tool_call_count": {"value": tool_call_count, "extraction": "automatic"},
        "by_tool": {"value": by_tool, "extraction": "automatic"},
        "useful_call_ratio": {
            "value_heuristic": useful_call_ratio_heuristic,
            "cited_count_heuristic": cited_count,
            "total_calls": tool_call_count,
            "extraction": "heuristic",
            "note": "Per-call substring-match against final_text. False positives possible (matching noise lines); manual ratification recommended.",
        },
        "useful_call_per_call": useful_per_call,
        "context_pollution_events": {
            "value": compaction_event_count + re_reads,
            "compaction_events": compaction_event_count,
            "file_re_reads": re_reads,
            "extraction": "automatic",
        },
        "exploration_convergence": {
            "value_heuristic": conv_heuristic,
            "distinct_files_touched": distinct_files,
            "total_file_accesses": total_accesses,
            "breadth_ratio": breadth,
            "extraction": "heuristic",
            "manual_override": None,
            "note": "Higher breadth = each file accessed once (narrowed). Lower = many re-reads (thrashed). Human override expected for borderline cases.",
        },
        "time_to_first_candidate_seconds": {
            "value": wall_to_first_cand,
            "extraction": "automatic",
            "note": "Wall-time from run start to first event containing a '## N. ...' candidate header.",
        },
        "tokens_to_first_candidate": {
            "value": tokens_to_first_cand,
            "extraction": "automatic",
            "note": "Cumulative step_finish.tokens.total at the step containing the first candidate.",
        },
        "wall_seconds_total": {"value": round(wall_seconds, 1), "extraction": "automatic"},
        "_note": "Per the rubric: tool_use sits parallel to the rubric, never collapsed into the rubric total. Manual ratification of useful_call_ratio and exploration_convergence recommended before cross-run comparison.",
    }
    (results / "tool_use.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({
        "tool_call_count": tool_call_count,
        "by_tool": by_tool,
        "useful_call_ratio_heuristic": useful_call_ratio_heuristic,
        "context_pollution_events": compaction_event_count + re_reads,
        "exploration_convergence_heuristic": conv_heuristic,
        "time_to_first_candidate_seconds": wall_to_first_cand,
        "tokens_to_first_candidate": tokens_to_first_cand,
        "wall_seconds": round(wall_seconds, 1),
    }, indent=2))


if __name__ == "__main__":
    main()
