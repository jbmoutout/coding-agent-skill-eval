#!/usr/bin/env python3
"""
No-grilling baseline: same task, same SETTLED_DESIGN.md protocol, but ZERO
SIM. The agent gets ONE shot to read the repo, do the design work, and write
SETTLED_DESIGN.md.

Counterfactual control for the grilling-leg eval. Output is graded with the
same Part II rubric, isolating whether the multi-turn loop contributes
anything over a single call.

Cell convention: `<task>-no-grilling-<agent_short>` (e.g.
arch-001-no-grilling-kimi). Output layout mirrors the grilling runs (rep1/,
raw_export.jsonl, etc.) so extract_grilling_artifacts.py + the Part II
graders work unchanged.

Usage:
    run_no_grilling.py --cell <name> --rep <N>
        --agent-model <slug> --docker-image <tag> --prompt-file <path>
        [--max-wall-seconds 1800]

Env:
    EVAL_RESULTS_ROOT - root for output dirs (default: <repo>/results)
    EVAL_STATE_ROOT   - root for opencode SQLite (default: /tmp)
    EVAL_ENV_FILE     - .env file with OPENROUTER_API_KEY
                        (default: <repo>/.env)
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
SAFE_CELL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def load_env_var(name: str, env_file: Path) -> str:
    for line in env_file.read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError(f"{name} not found in {env_file}")


def validate_cell(cell: str) -> str:
    if not SAFE_CELL_RE.fullmatch(cell):
        raise ValueError(
            "cell must be a slug containing only letters, numbers, '.', '_', "
            "or '-', and must start with a letter or number"
        )
    return cell


def contained_path(root: Path, *parts: str) -> Path:
    root = root.resolve()
    path = root.joinpath(*parts).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"path escapes root: {path}")
    return path


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cell", required=True,
                    help="Cell name (e.g. 'arch-001-no-grilling-kimi'). "
                         "Output dir = $EVAL_RESULTS_ROOT/<cell>/rep<N>")
    ap.add_argument("--rep", type=int, required=True,
                    help="Rep number within the cell.")
    ap.add_argument("--agent-model", required=True,
                    help="Agent model slug as opencode understands it.")
    ap.add_argument("--docker-image", required=True,
                    help="Docker image tag for the agent container.")
    ap.add_argument("--prompt-file", type=Path, required=True,
                    help="Path to the INITIAL_PROMPT text file (sole turn).")
    ap.add_argument("--max-wall-seconds", type=int, default=1800,
                    help="Subprocess timeout (default: 1800).")
    return ap.parse_args()


def main():
    args = parse_args()

    results_root = Path(
        os.environ.get("EVAL_RESULTS_ROOT", REPO_ROOT / "results"))
    state_root = Path(os.environ.get("EVAL_STATE_ROOT", "/tmp"))
    env_file = Path(os.environ.get("EVAL_ENV_FILE", REPO_ROOT / ".env"))
    cell = validate_cell(args.cell)

    output_dir = contained_path(results_root, cell, f"rep{args.rep}")
    state_dir = contained_path(state_root, f"opencode-{cell}-rep{args.rep}")
    raw_export = output_dir / "raw_export.jsonl"
    run_log = output_dir / "run.log"

    output_dir.mkdir(parents=True, exist_ok=True)
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    for f in (raw_export, run_log):
        if f.exists():
            f.unlink()

    initial_prompt = args.prompt_file.read_text()
    (output_dir / "initial_prompt.txt").write_text(initial_prompt)

    or_key = load_env_var("OPENROUTER_API_KEY", env_file)
    # --dangerously-skip-permissions is required: the eval runs inside an
    # ephemeral docker container with `--rm`, the only secret is the
    # OpenRouter key (rate-limited dev tier), and the workspace is
    # throwaway. Without it, opencode blocks on permission prompts that
    # cannot be answered in a non-TTY subprocess and every tool call
    # stalls. The shell-injection class is closed separately by the argv
    # form below — do NOT reintroduce shell-wrapped launches.
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{output_dir}:/results",
        "-v", f"{state_dir}:/root/.local/share/opencode",
        "-e", "OPENROUTER_API_KEY",
        "-w", "/app",
        args.docker_image,
        "/root/.opencode/bin/opencode", "run",
        "--dangerously-skip-permissions",
        "--format", "json",
        "--model", args.agent_model,
        initial_prompt,
    ]
    child_env = os.environ.copy()
    child_env["OPENROUTER_API_KEY"] = or_key

    def ts():
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    with open(run_log, "a") as f:
        f.write(
            f"{ts()} === no-grilling baseline: "
            f"agent={args.agent_model} cell={args.cell} ===\n"
        )

    print(f"=== {args.cell}/rep{args.rep} ===")
    print(f"    agent={args.agent_model}")
    print(f"    one-shot, no SIM, no multi-turn")
    t0 = time.time()
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=args.max_wall_seconds, env=child_env,
    )
    elapsed = time.time() - t0
    if result.stdout:
        with open(raw_export, "a") as f:
            f.write(result.stdout)
    with open(run_log, "a") as f:
        f.write(f"{ts()} docker exit={result.returncode}, wall={elapsed:.1f}s\n")
        if result.stderr:
            f.write(result.stderr)
            if not result.stderr.endswith("\n"):
                f.write("\n")
    print(f"  docker exit={result.returncode}, wall={elapsed:.1f}s")
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[:200]}")
        sys.exit(1)

    # Quick sanity: did the agent write SETTLED_DESIGN.md?
    events = []
    if raw_export.exists():
        for line in raw_export.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    writes_to_settled = sum(
        1 for e in events
        if e.get("type") == "tool_use"
        and e.get("part", {}).get("tool") == "write"
        and "SETTLED_DESIGN" in str(
            e.get("part", {}).get("state", {}).get("input", {}).get("filePath", ""))
    )
    print(f"  events={len(events)}, SETTLED_DESIGN.md writes={writes_to_settled}")


if __name__ == "__main__":
    main()
