#!/usr/bin/env python3
"""
Extract artifacts from a grilling-baseline run's raw_export.jsonl.

What it recovers:
    - Every file the agent wrote (`write`/`edit` tool_use) → `extracted_files/`
    - Each sub-agent's prompt + output (`task` tool_use) → `subagents/agent_<N>_<slug>.md`
    - A flat timeline of all events → `timeline.jsonl`
    - A summary stats JSON → `stats.json`

Usage:
    python3 extract_grilling_artifacts.py <rep_dir>
"""
import json
import re
import sys
from pathlib import Path


def slugify(s, maxlen=50):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.lower()).strip("_")
    return s[:maxlen]


def main():
    rep_dir = Path(sys.argv[1])
    raw_export = rep_dir / "raw_export.jsonl"
    if not raw_export.exists():
        print(f"ERROR: {raw_export} not found")
        sys.exit(1)

    events = []
    for line in raw_export.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    # 1) Extract files the agent wrote/edited
    # - `write` events have full file content; save as-is.
    # - `edit` events have oldString/newString diffs; append each to a
    #   per-file `.edits.md` log so we can see the cumulative changes.
    files_dir = rep_dir / "extracted_files"
    files_dir.mkdir(exist_ok=True)
    written = []
    for e in events:
        if e.get("type") != "tool_use":
            continue
        tool = e.get("part", {}).get("tool")
        if tool not in ("write", "edit"):
            continue
        input_ = e["part"]["state"].get("input", {})
        fp = input_.get("filePath") or input_.get("path")
        if not fp:
            continue
        # Sanitise the host-side filename: strip leading /app, replace / with __
        rel = fp.replace("/app/", "").replace("/app", "")
        host_name = rel.replace("/", "__")

        if tool == "write":
            content = input_.get("content", "")
            out = files_dir / host_name
            out.write_text(content)
            written.append({
                "original_path": fp, "host_file": str(out),
                "len": len(content), "tool": tool,
            })
        else:  # edit
            old_s = input_.get("oldString", "")
            new_s = input_.get("newString", "")
            out = files_dir / f"{host_name}.edits.md"
            block = (
                "\n---\n## edit (oldString → newString)\n\n"
                f"### old\n```\n{old_s}\n```\n\n"
                f"### new\n```\n{new_s}\n```\n"
            )
            with open(out, "a") as f:
                f.write(block)
            written.append({
                "original_path": fp, "host_file": str(out),
                "len": len(new_s), "old_len": len(old_s), "tool": tool,
            })

    # 2) Extract sub-agent prompt+output pairs
    sub_dir = rep_dir / "subagents"
    sub_dir.mkdir(exist_ok=True)
    sub_agents = []
    n = 0
    for e in events:
        if e.get("type") != "tool_use":
            continue
        if e.get("part", {}).get("tool") != "task":
            continue
        n += 1
        state = e["part"]["state"]
        input_ = state.get("input", {})
        desc = input_.get("description", f"task_{n}")
        prompt = input_.get("prompt", "")
        subagent_type = input_.get("subagent_type", "")
        output = state.get("output", "")
        status = state.get("status", "")
        slug = slugify(desc)
        fname = sub_dir / f"agent_{n:02d}_{slug}.md"
        body = [
            f"# Sub-agent {n}: {desc}",
            "",
            f"- Subagent type: `{subagent_type}`",
            f"- Status: `{status}`",
            f"- Prompt length: {len(prompt)} chars",
            f"- Output length: {len(output)} chars",
            "",
            "## Prompt to sub-agent",
            "",
            "```",
            prompt,
            "```",
            "",
            "## Sub-agent output",
            "",
            output if output else "_(no output captured)_",
            "",
        ]
        fname.write_text("\n".join(body))
        sub_agents.append({
            "n": n, "description": desc, "subagent_type": subagent_type,
            "status": status, "prompt_len": len(prompt),
            "output_len": len(output), "host_file": str(fname),
        })

    # 3) Flat timeline (one line per event with type + key info)
    timeline_path = rep_dir / "timeline.jsonl"
    with open(timeline_path, "w") as f:
        for e in events:
            t = e.get("type")
            ts = e.get("timestamp")
            entry = {"type": t, "timestamp": ts, "sessionID": e.get("sessionID")}
            if t == "text":
                entry["text_preview"] = e["part"]["text"][:200]
                entry["text_len"] = len(e["part"]["text"])
            elif t == "tool_use":
                p = e["part"]
                entry["tool"] = p.get("tool")
                entry["status"] = p.get("state", {}).get("status")
                inp = p.get("state", {}).get("input", {}) or {}
                if "filePath" in inp:
                    entry["filePath"] = inp["filePath"]
                if "command" in inp:
                    entry["command"] = inp["command"][:120]
                if "description" in inp:
                    entry["description"] = inp["description"]
            elif t in ("step_start", "step_finish"):
                if t == "step_finish":
                    entry["tokens"] = e["part"].get("tokens")
                    entry["cost"] = e["part"].get("cost")
            f.write(json.dumps(entry) + "\n")

    # 4) Stats summary
    text_events = [e for e in events if e.get("type") == "text"]
    tool_events = [e for e in events if e.get("type") == "tool_use"]
    step_finishes = [e for e in events if e.get("type") == "step_finish"]
    tool_counts = {}
    for e in tool_events:
        t = e["part"].get("tool", "?")
        tool_counts[t] = tool_counts.get(t, 0) + 1
    total_cost = sum(e["part"].get("cost", 0) or 0 for e in step_finishes)
    total_tokens_in = sum((e["part"].get("tokens", {}) or {}).get("input", 0) for e in step_finishes)
    total_tokens_out = sum((e["part"].get("tokens", {}) or {}).get("output", 0) for e in step_finishes)
    stats = {
        "rep_dir": str(rep_dir),
        "n_events": len(events),
        "n_text_events": len(text_events),
        "n_tool_uses": len(tool_events),
        "n_step_finishes": len(step_finishes),
        "tool_counts": tool_counts,
        "files_written_count": len(written),
        "files_written": written,
        "subagent_count": len(sub_agents),
        "subagents": sub_agents,
        "cost_usd": round(total_cost, 4),
        "tokens_in": total_tokens_in,
        "tokens_out": total_tokens_out,
    }
    (rep_dir / "stats.json").write_text(json.dumps(stats, indent=2))

    print(f"=== extracted to {rep_dir} ===")
    print(f"  files written:    {len(written)}  → extracted_files/")
    print(f"  sub-agents:       {len(sub_agents)}  → subagents/")
    print(f"  timeline events:  {len(events)}  → timeline.jsonl")
    print(f"  cost:             ${total_cost:.4f}  ({total_tokens_in} in, {total_tokens_out} out)")
    print(f"  stats:            stats.json")


if __name__ == "__main__":
    main()
