#!/usr/bin/env python3
"""
Grilling-leg orchestrator using OpenEvals as the multi-turn substrate.

Wiring:
    app=opencode_app (docker subprocess wrapping)
    user=create_llm_simulated_user (any LLM with persona prompt)
    stopping_condition=settled_design_written (agent writes SETTLED_DESIGN.md)

OpenEvals handles:
    - Role inversion for the simulator
    - Trajectory accumulation
    - Loop control with max_turns + stopping condition

We handle:
    - Wrapping opencode as a Python callable (it's CLI, not native Python)
    - Reading the agent's latest text from raw_export.jsonl after each docker run
    - Session-id persistence across docker spin-ups (closure state)
    - Custom stopping condition that peeks at opencode's event log

Usage:
    run_grilling_openevals.py --cell <name> --rep <N>
        --agent-model <slug> --simulator-model <slug>
        --docker-image <tag> --prompt-file <path>
        [--persona <path>] [--opencode-config <path>]
        [--max-turns 15]

Env (all optional):
    EVAL_RESULTS_ROOT  - root for output dirs (default: <repo>/results)
    EVAL_STATE_ROOT    - root for opencode SQLite (default: /tmp)
    EVAL_PERSONA_PATH  - default persona (overridden by --persona)
    EVAL_OPENCODE_CONFIG - default opencode.json (overridden by --opencode-config)
    EVAL_ENV_FILE      - .env file holding OPENROUTER_API_KEY / OPENAI_API_KEY
                         (default: <repo>/.env)
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI
from openevals.simulators import (
    run_multiturn_simulation,
    create_llm_simulated_user,
)

# Module-level config; populated by configure() inside main(). Helper
# functions access these by name (Python's dynamic lookup makes this safe
# as long as main() runs first).
REP = None
MAX_TURNS = 15
CELL = None
OUTPUT_DIR = None
STATE_DIR = None
PERSONA_FILE = None
RAW_EXPORT = None
RUN_LOG = None
TRANSCRIPT = None
TRAJECTORY = None
SIM_LOG = None
ENV_FILE = None
DOCKER_IMAGE = None
AGENT_MODEL = None
OPENCODE_CONFIG = None
SIMULATOR_MODEL = None
INITIAL_PROMPT = None

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent


# -------- Helpers --------
def log(msg):
    line = f"[orchestrator rep{REP}] {msg}"
    print(line, flush=True)
    with open(RUN_LOG, "a") as f:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        f.write(f"{ts} {msg}\n")


def load_env_var(name, env_file=None):
    # ENV_FILE is None at import; resolved by configure() before this is called.
    env_file = env_file or ENV_FILE
    with open(env_file) as f:
        for line in f:
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError(f"{name} not found in {env_file}")


def read_events():
    if not RAW_EXPORT.exists():
        return []
    out = []
    for line in RAW_EXPORT.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def latest_agent_text():
    events = read_events()
    text_events = [e for e in events if e.get("type") == "text"]
    if not text_events:
        return None
    return text_events[-1]["part"]["text"]


def latest_session_id():
    events = read_events()
    for e in reversed(events):
        if "sessionID" in e:
            return e["sessionID"]
    return None


def total_task_uses():
    events = read_events()
    return sum(1 for e in events
               if e.get("type") == "tool_use"
               and e.get("part", {}).get("tool") == "task")


def latest_error_after(text_event_idx):
    """Return the latest `type=error` event if one appeared in raw_export
    AFTER the text event at `text_event_idx` (i.e. since the last successful
    agent text). Used to detect opencode/provider failures (402 credits,
    rate limits, API outages) that don't surface as docker non-zero exit.
    """
    events = read_events()
    # Find where the Nth text event sits in the event list
    text_idx_in_events = -1
    seen_text = 0
    for i, e in enumerate(events):
        if e.get("type") == "text":
            if seen_text == text_event_idx:
                text_idx_in_events = i
                break
            seen_text += 1
    # Look for an error event AFTER that text event
    start = text_idx_in_events + 1 if text_idx_in_events >= 0 else 0
    for e in events[start:]:
        if e.get("type") == "error":
            return e
    return None


def count_text_events():
    return sum(1 for e in read_events() if e.get("type") == "text")


def recover_lost_text_from_db():
    """Read the most recent assistant message's text from opencode's SQLite.

    Failure mode this addresses: opencode persists message parts to its
    SQLite (verified across multiple failed runs - text + step-finish with
    reason='stop') but sometimes does NOT flush the corresponding `text`
    event to its --format=json stdout before the docker process exits.
    The data is intact in the DB; we read it back.

    Returns: (text, message_id, completed) or (None, None, False) if nothing
    recoverable. `completed` is True if the message has a step-finish with
    reason='stop' (i.e. genuinely complete, not mid-stream).
    """
    db_path = STATE_DIR / "opencode.db"
    if not db_path.exists():
        return None, None, False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        cur = conn.cursor()
        sess_id = _session_id["value"]
        if sess_id is None:
            # Fall back to the latest session in the DB
            row = cur.execute(
                "SELECT id FROM session ORDER BY time_created DESC LIMIT 1"
            ).fetchone()
            if not row:
                conn.close()
                return None, None, False
            sess_id = row[0]
        # Most recent message in this session (any role - opencode stores
        # the agent's reply as a new message after the user message).
        msg_row = cur.execute(
            "SELECT id FROM message WHERE session_id = ? "
            "ORDER BY time_created DESC LIMIT 1",
            (sess_id,),
        ).fetchone()
        if not msg_row:
            conn.close()
            return None, None, False
        msg_id = msg_row[0]
        # All parts of that message in chronological order
        parts = cur.execute(
            "SELECT data FROM part WHERE message_id = ? "
            "ORDER BY time_created ASC",
            (msg_id,),
        ).fetchall()
        conn.close()
        if not parts:
            return None, msg_id, False
        text_chunks = []
        completed = False
        for (data_str,) in parts:
            try:
                part = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            ptype = part.get("type")
            if ptype == "text":
                text_chunks.append(part.get("text", ""))
            elif ptype == "step-finish" and part.get("reason") == "stop":
                completed = True
        if not text_chunks:
            return None, msg_id, completed
        return "".join(text_chunks), msg_id, completed
    except sqlite3.Error as e:
        log(f"recover_lost_text_from_db: sqlite error: {e}")
        return None, None, False


def synthesize_text_event(text, msg_id):
    """Append a synthetic `text` event to raw_export.jsonl so downstream
    extraction + audit tooling sees the recovered text uniformly.
    """
    event = {
        "type": "text",
        "timestamp": int(time.time() * 1000),
        "sessionID": _session_id["value"],
        "_recovered_from_sqlite": True,
        "_message_id": msg_id,
        "part": {"text": text},
    }
    with open(RAW_EXPORT, "a") as f:
        f.write(json.dumps(event) + "\n")


# -------- Simulator API call logger --------
class SimulatorLogger(BaseCallbackHandler):
    """LangChain callback: records every simulator LLM call (prompts in,
    response out, token usage, latency) to SIM_LOG.
    """

    def __init__(self, log_path):
        self.log_path = log_path
        self._t0 = {}

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self._t0[run_id] = time.time()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self._t0[run_id] = time.time()
        # Capture prompts for the next on_llm_end pairing
        self._t0[f"{run_id}_msgs"] = messages

    def on_llm_end(self, response, *, run_id, **kwargs):
        elapsed = time.time() - self._t0.pop(run_id, time.time())
        msgs = self._t0.pop(f"{run_id}_msgs", None)
        # Flatten messages → role/content pairs
        flat_msgs = []
        if msgs and isinstance(msgs, list) and msgs and isinstance(msgs[0], list):
            for m in msgs[0]:
                role = getattr(m, "type", None) or m.__class__.__name__
                content = getattr(m, "content", None)
                flat_msgs.append({"role": role, "content": content})
        # Extract generated text + token usage
        gen_text, usage = None, {}
        if response.generations:
            gen_text = response.generations[0][0].text
        if response.llm_output and "token_usage" in response.llm_output:
            usage = response.llm_output["token_usage"]
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": str(run_id),
            "elapsed_s": round(elapsed, 2),
            "messages_in": flat_msgs,
            "response_text": gen_text,
            "usage": usage,
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def on_llm_error(self, error, *, run_id, **kwargs):
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": str(run_id),
            "error": f"{type(error).__name__}: {error}",
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")


# -------- Opencode app (the agent under test) --------
_session_id = {"value": None}  # closure-state for session id


def opencode_app(message, thread_id=None, **kwargs):
    """OpenEvals app callable.

    Receives a SINGLE message dict (the simulator's latest output) and returns
    a SINGLE message dict (the agent's response). NOT a trajectory wrapper.
    """
    # The simulator's first response IS the INITIAL_PROMPT via fixed_responses[]
    message_to_send = message.get("content", "") if message else ""
    if not message_to_send:
        message_to_send = "Please continue."

    sess_flag = (
        f"--session {_session_id['value']}" if _session_id["value"] else ""
    )
    escaped = message_to_send.replace("'", "'\\''")
    cmd_inside = (
        f"cd /app && /root/.opencode/bin/opencode run "
        f"--dangerously-skip-permissions --format json "
        f"--model {AGENT_MODEL} {sess_flag} '{escaped}'"
    )
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{OUTPUT_DIR}:/results",
        "-v", f"{STATE_DIR}:/root/.local/share/opencode",
        "-v", f"{OPENCODE_CONFIG}:/app/opencode.json:ro",
        "-e", f"OPENROUTER_API_KEY={OPENROUTER_API_KEY}",
        "--entrypoint", "/bin/sh",
        DOCKER_IMAGE,
        "-c",
        f"({cmd_inside}) >> /results/raw_export.jsonl 2>> /results/run.log",
    ]

    # Snapshot the text-event count BEFORE the call so we can detect whether
    # a new agent text actually landed (docker can exit 0 with no new events
    # if opencode/the provider errored - see e.g. OpenRouter 402 credits).
    pre_text_count = count_text_events()

    log(f"opencode_app: sending msg len={len(message_to_send)}, "
        f"session={_session_id['value'] or '<new>'}")
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    elapsed = time.time() - t0
    log(f"opencode_app: docker exit={result.returncode}, wall={elapsed:.1f}s")
    if result.returncode != 0:
        log(f"opencode_app: stderr: {result.stderr[:500]}")
        raise RuntimeError(f"opencode exited {result.returncode}: {result.stderr[:200]}")

    # Capture session id on first turn
    if _session_id["value"] is None:
        _session_id["value"] = latest_session_id()
        log(f"opencode_app: captured session_id={_session_id['value']}")

    # Detect provider/API errors that don't surface as docker non-zero exit
    # (e.g. OpenRouter 402 credits, rate limits, transient API outages).
    # If no new text event landed AND an `error` event appeared since the
    # last text, abort the simulation cleanly.
    post_text_count = count_text_events()
    if post_text_count == pre_text_count:
        err = latest_error_after(max(0, pre_text_count - 1))
        if err is not None:
            err_payload = err.get("error", {})
            err_data = err_payload.get("data", {}) if isinstance(err_payload, dict) else {}
            err_msg = err_data.get("message") or str(err_payload)[:300]
            err_code = err_data.get("statusCode") or err_payload.get("name") or "?"
            log(f"opencode_app: PROVIDER ERROR detected (code={err_code}): {err_msg}")
            raise RuntimeError(f"opencode/provider error {err_code}: {err_msg}")
        # Silent stall - no new text event, no error event. Known opencode
        # failure mode: message parts (text + step-finish reason='stop') are
        # persisted to its SQLite but the corresponding stdout JSON event is
        # never flushed before docker exits. Recover from the DB.
        recovered, msg_id, completed = recover_lost_text_from_db()
        if recovered:
            synthesize_text_event(recovered, msg_id)
            log(f"opencode_app: RECOVERED {len(recovered)} chars from sqlite "
                f"(msg={msg_id}, completed={completed}) - synthesized text event")
            agent_text = recovered
            log(f"opencode_app: agent returned text len={len(agent_text)}, "
                f"total task_uses so far={total_task_uses()}")
            return {"role": "assistant", "content": agent_text}
        log("opencode_app: silent stall AND sqlite recovery found no text - aborting")
        raise RuntimeError("opencode silent stall, no text in SQLite either")

    agent_text = latest_agent_text() or "[no text emitted]"
    log(f"opencode_app: agent returned text len={len(agent_text)}, "
        f"total task_uses so far={total_task_uses()}")

    return {"role": "assistant", "content": agent_text}


# -------- Stopping condition --------
# Primary signal: the agent writes SETTLED_DESIGN.md - explicit "design phase
# done" marker baked into the initial prompt's end-of-grilling protocol.
# Fallback signal: the agent emits a heading like "## Settled design" or
# "Design phase is closed" in its text (in case it forgets to write the file).
SETTLED_FILE_RE = re.compile(r"/SETTLED_DESIGN\.md$", re.IGNORECASE)
SETTLED_TEXT_RE = re.compile(
    r"##\s*settled design|design phase (?:is )?closed|design (?:is )?locked",
    re.IGNORECASE,
)


def settled_design_written():
    """True if the agent has emitted a `write` tool_use for SETTLED_DESIGN.md."""
    events = read_events()
    for e in events:
        if e.get("type") != "tool_use":
            continue
        p = e.get("part", {})
        if p.get("tool") != "write":
            continue
        fp = (p.get("state", {}).get("input", {}) or {}).get("filePath", "")
        if SETTLED_FILE_RE.search(fp or ""):
            return True
    return False


def is_terminal(trajectory, turn_counter=None, **kwargs):
    """Stop when the agent has explicitly closed the design phase:
        primary: `write` tool_use for SETTLED_DESIGN.md (the protocol bakes
                 this into the initial prompt so the agent emits it).
        fallback: agent text contains a heading like "## Settled design" or
                  "Design phase is closed" or "Design is locked".

    Both are post-hoc inferences from the events stream - no dependency on
    recommendation phrasing or trajectory length heuristics.
    """
    if settled_design_written():
        log("is_terminal: SETTLED_DESIGN.md write detected - stopping")
        return True
    txt = latest_agent_text() or ""
    if SETTLED_TEXT_RE.search(txt):
        log("is_terminal: settled-design heading detected in agent text - stopping")
        return True
    return False


# -------- Transcript writer --------
def write_transcript(trajectory, terminal_reason):
    lines = [
        f"# Grilling run - {CELL} rep {REP} (OpenEvals)",
        f"Agent: `{AGENT_MODEL}` via opencode (`{DOCKER_IMAGE}`)",
        f"Simulator: `{SIMULATOR_MODEL}`",
        f"Substrate: openevals `run_multiturn_simulation`",
        f"Persona: {PERSONA_FILE.name}",
        f"Terminal: {terminal_reason}",
        f"Total task tool_uses: {total_task_uses()}",
        "",
        "---",
        "",
        "## Initial prompt (to agent)",
        "",
        "```",
        INITIAL_PROMPT.strip(),
        "```",
        "",
    ]
    for i, m in enumerate(trajectory.get("messages", []), 1):
        role = m["role"]
        who = (f"Agent ({AGENT_MODEL} via opencode)"
               if role == "assistant"
               else f"Simulator ({SIMULATOR_MODEL} persona)")
        lines.extend([
            f"## Turn {i} - {who}",
            "",
            m["content"],
            "",
        ])
    TRANSCRIPT.write_text("\n".join(lines))
    # Also dump raw trajectory JSON
    TRAJECTORY.write_text(json.dumps(trajectory, indent=2, default=str))


# -------- Configuration --------
def configure(args):
    """Populate module-level config from parsed args + env.

    Helper functions throughout this module reference these as globals; this
    function is the single place that sets them. Must be called before any
    helper that touches paths or model names.
    """
    global REP, MAX_TURNS, CELL, OUTPUT_DIR, STATE_DIR, PERSONA_FILE
    global RAW_EXPORT, RUN_LOG, TRANSCRIPT, TRAJECTORY, SIM_LOG, ENV_FILE
    global DOCKER_IMAGE, AGENT_MODEL, OPENCODE_CONFIG, SIMULATOR_MODEL
    global INITIAL_PROMPT

    REP = args.rep
    MAX_TURNS = args.max_turns
    CELL = args.cell
    AGENT_MODEL = args.agent_model
    SIMULATOR_MODEL = args.simulator_model
    DOCKER_IMAGE = args.docker_image

    results_root = Path(os.environ.get("EVAL_RESULTS_ROOT", REPO_ROOT / "results"))
    state_root = Path(os.environ.get("EVAL_STATE_ROOT", "/tmp"))

    OUTPUT_DIR = results_root / CELL / f"rep{REP}"
    STATE_DIR = state_root / f"opencode-grilling-{CELL}-rep{REP}"
    RAW_EXPORT = OUTPUT_DIR / "raw_export.jsonl"
    RUN_LOG = OUTPUT_DIR / "run.log"
    TRANSCRIPT = OUTPUT_DIR / "transcript.md"
    TRAJECTORY = OUTPUT_DIR / "trajectory.json"
    SIM_LOG = OUTPUT_DIR / "simulator_log.jsonl"

    PERSONA_FILE = args.persona or Path(os.environ.get(
        "EVAL_PERSONA_PATH",
        REPO_ROOT / "personas" / "grilling_persona.md",
    ))
    OPENCODE_CONFIG = args.opencode_config or Path(os.environ.get(
        "EVAL_OPENCODE_CONFIG",
        REPO_ROOT / "config" / "opencode.json.example",
    ))
    ENV_FILE = str(Path(os.environ.get("EVAL_ENV_FILE", REPO_ROOT / ".env")))

    INITIAL_PROMPT = args.prompt_file.read_text()


# -------- Main --------
def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--cell", required=True,
                    help="Cell name (e.g. 'arch-001-grilling-qwen-gpt55'). "
                         "Used as the output directory under EVAL_RESULTS_ROOT.")
    ap.add_argument("--rep", type=int, required=True,
                    help="Rep number within the cell.")
    ap.add_argument("--agent-model", required=True,
                    help="Agent model slug as opencode understands it "
                         "(e.g. 'openrouter/qwen/qwen3.6-27b').")
    ap.add_argument("--simulator-model", required=True,
                    help="Simulator model name (OpenAI-compatible, e.g. 'gpt-5.5').")
    ap.add_argument("--docker-image", required=True,
                    help="Docker image tag for the agent container "
                         "(must have opencode + the task codebase baked in).")
    ap.add_argument("--prompt-file", type=Path, required=True,
                    help="Path to the INITIAL_PROMPT text file (turn 1 the SIM "
                         "sends to the agent).")
    ap.add_argument("--persona", type=Path, default=None,
                    help="Persona prompt for the SIM. Overrides $EVAL_PERSONA_PATH.")
    ap.add_argument("--opencode-config", type=Path, default=None,
                    help="opencode.json for model registration inside the container. "
                         "Overrides $EVAL_OPENCODE_CONFIG.")
    ap.add_argument("--max-turns", type=int, default=15,
                    help="Max OpenEvals simulation turns (default: 15).")
    args = ap.parse_args()

    configure(args)

    global OPENROUTER_API_KEY
    OPENROUTER_API_KEY = load_env_var("OPENROUTER_API_KEY")
    # Reset closure-state so re-running main() within the same process is safe
    _session_id["value"] = None

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_DIR.exists():
        subprocess.run(["rm", "-rf", str(STATE_DIR)], check=False)
    STATE_DIR.mkdir()

    # Clean prior outputs
    for f in [RAW_EXPORT, RUN_LOG, TRANSCRIPT, TRAJECTORY, SIM_LOG]:
        if f.exists():
            f.unlink()
    (OUTPUT_DIR / "initial_prompt.txt").write_text(INITIAL_PROMPT)

    log(f"=== starting grilling run ({CELL}) - agent={AGENT_MODEL}, "
        f"sim={SIMULATOR_MODEL} ===")

    # Build the simulator client (uses the same .env as the agent).
    openai_key = load_env_var("OPENAI_API_KEY")
    sim_logger = SimulatorLogger(SIM_LOG)
    simulator_client = ChatOpenAI(
        model=SIMULATOR_MODEL,
        api_key=openai_key,
        max_tokens=2000,
        max_retries=4,   # catch transient APIConnectionError / 5xx
        request_timeout=60,
        callbacks=[sim_logger],
    )

    persona_prompt = PERSONA_FILE.read_text()

    # First "user" message is fixed to INITIAL_PROMPT so the agent sees the
    # candidate context. From turn 2 onward, the simulator generates dynamically.
    user_simulator = create_llm_simulated_user(
        system=persona_prompt,
        client=simulator_client,
        fixed_responses=[INITIAL_PROMPT],
    )

    log(f"simulator wired: {SIMULATOR_MODEL} (thinking disabled, "
        f"persona len={len(persona_prompt)} chars)")

    # Run the multi-turn simulation
    log(f"calling run_multiturn_simulation, max_turns={MAX_TURNS}")
    try:
        result = run_multiturn_simulation(
            app=opencode_app,
            user=user_simulator,
            max_turns=MAX_TURNS,
            stopping_condition=is_terminal,
        )
    except Exception as e:
        log(f"run_multiturn_simulation raised: {type(e).__name__}: {e}")
        # Try to capture what we have
        raise

    # Result is a dict with key "trajectory" (list of messages)
    trajectory_msgs = result.get("trajectory", []) if isinstance(result, dict) else []
    final_traj = {"messages": trajectory_msgs}

    n_messages = len(trajectory_msgs)
    log(f"simulation complete: {n_messages} messages in trajectory")

    if is_terminal(trajectory_msgs):
        reason = "interface_design_complete"
    elif n_messages >= MAX_TURNS * 2:
        reason = "max_turns"
    else:
        reason = "unknown"
    log(f"terminal: {reason}")

    write_transcript(final_traj, reason)
    log(f"=== done - transcript at {TRANSCRIPT} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
