#!/usr/bin/env python3
"""
Build a self-contained HTML viewer for a grilling-rep's data.

Assembles: trajectory + timeline + stats + sub-agents + extracted files +
run.log + initial prompt into a single HTML file with terminal-UI styling
(dark, mono, Cursor-ish). No JS framework, no external deps. Open with
double-click.

Usage:
    python3 build_grilling_viewer.py <rep_dir> [out.html]

Run extract_grilling_artifacts.py first so `subagents/`, `extracted_files/`,
`timeline.jsonl`, and `stats.json` exist.
"""
import json
import sys
from pathlib import Path
from html import escape


def read_text(p):
    return p.read_text() if p.exists() else ""


def load_jsonl(p):
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def main():
    rep_dir = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else rep_dir / "viewer.html"

    stats = json.loads(read_text(rep_dir / "stats.json") or "{}")
    timeline = load_jsonl(rep_dir / "timeline.jsonl")
    trajectory = json.loads(read_text(rep_dir / "trajectory.json") or '{"messages":[]}')
    initial_prompt = read_text(rep_dir / "initial_prompt.txt")
    run_log = read_text(rep_dir / "run.log")
    sim_log = load_jsonl(rep_dir / "simulator_log.jsonl")

    # Sub-agents
    sub_dir = rep_dir / "subagents"
    sub_files = sorted(sub_dir.glob("agent_*.md")) if sub_dir.exists() else []
    sub_agents = [{"name": p.name, "body": p.read_text()} for p in sub_files]

    # Extracted files
    files_dir = rep_dir / "extracted_files"
    extracted = []
    if files_dir.exists():
        for p in sorted(files_dir.iterdir()):
            if p.is_file():
                extracted.append({"name": p.name, "body": p.read_text()})

    rep_label = rep_dir.name
    parent_label = rep_dir.parent.name

    # Data bundle for client-side rendering of dynamic bits
    data = {
        "stats": stats,
        "timeline": timeline,
        "trajectory": trajectory,
        "initial_prompt": initial_prompt,
        "run_log": run_log,
        "sub_agents": sub_agents,
        "extracted_files": extracted,
        "simulator_log": sim_log,
        "rep_label": rep_label,
        "parent_label": parent_label,
    }
    data_json = json.dumps(data)

    title = f"{parent_label} / {rep_label}"

    html = HTML_TEMPLATE.replace("__TITLE__", escape(title)).replace(
        "__DATA__", data_json.replace("</", "<\\/")
    )
    out.write_text(html)
    print(f"wrote {out} ({len(html):,} bytes)")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  :root {
    --bg: #0b0d0e;
    --bg-2: #111416;
    --bg-3: #161a1d;
    --border: #1f2428;
    --border-2: #2a3137;
    --fg: #d6d9dc;
    --fg-dim: #8a8f95;
    --fg-faint: #5a5f65;
    --accent: #6aa9ff;
    --agent: #6aa9ff;
    --sim: #d19a66;
    --tool: #c678dd;
    --bash: #98c379;
    --err: #e06c75;
    --warn: #e5c07b;
    --link: #56b6c2;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--bg);
    color: var(--fg);
    font: 13px/1.55 ui-monospace, "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
  }
  a { color: var(--link); text-decoration: none; }
  a:hover { text-decoration: underline; }
  header {
    border-bottom: 1px solid var(--border);
    padding: 14px 22px;
    background: var(--bg-2);
    position: sticky; top: 0; z-index: 10;
    display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  }
  header h1 {
    margin: 0; font-size: 13px; font-weight: 600;
    color: var(--fg); letter-spacing: 0.02em;
  }
  header h1 .dim { color: var(--fg-dim); font-weight: 400; }
  header h1 .accent { color: var(--accent); }
  .stats { display: flex; gap: 18px; font-size: 12px; color: var(--fg-dim); }
  .stats b { color: var(--fg); font-weight: 600; }
  nav {
    display: flex; gap: 0;
    border-bottom: 1px solid var(--border);
    padding: 0 22px;
    background: var(--bg-2);
    position: sticky; top: 47px; z-index: 9;
  }
  nav button {
    appearance: none; background: none; border: none;
    color: var(--fg-dim); font: inherit; cursor: pointer;
    padding: 10px 16px; border-bottom: 2px solid transparent;
  }
  nav button:hover { color: var(--fg); }
  nav button.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }
  main { padding: 22px; max-width: 1200px; margin: 0 auto; }
  section { display: none; }
  section.active { display: block; }
  h2 {
    font-size: 12px; font-weight: 600; letter-spacing: 0.06em;
    color: var(--fg-dim); text-transform: uppercase;
    margin: 0 0 14px; padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }
  pre, code {
    font-family: inherit;
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .turn {
    border: 1px solid var(--border);
    border-radius: 6px;
    margin-bottom: 14px;
    background: var(--bg-2);
    overflow: hidden;
  }
  .turn-head {
    padding: 8px 14px;
    background: var(--bg-3);
    border-bottom: 1px solid var(--border);
    font-size: 11px;
    color: var(--fg-dim);
    display: flex; gap: 14px; align-items: center;
  }
  .turn-head .role {
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    font-size: 11px;
  }
  .turn-head .role.agent { color: var(--agent); }
  .turn-head .role.sim { color: var(--sim); }
  .turn-head .role.user { color: var(--accent); }
  .turn-body { padding: 14px 18px; }
  .turn-body pre { color: var(--fg); }

  /* Timeline */
  .tl-row {
    display: grid;
    grid-template-columns: 50px 100px 90px 1fr;
    gap: 14px;
    padding: 4px 8px;
    border-left: 2px solid transparent;
    font-size: 12px;
  }
  .tl-row:hover { background: var(--bg-2); }
  .tl-row .idx { color: var(--fg-faint); text-align: right; }
  .tl-row .type { color: var(--fg-dim); }
  .tl-row .tool { font-weight: 600; }
  .tl-row .tool.read { color: var(--link); }
  .tl-row .tool.bash { color: var(--bash); }
  .tl-row .tool.grep { color: var(--accent); }
  .tl-row .tool.write { color: var(--warn); }
  .tl-row .tool.task { color: var(--tool); }
  .tl-row .tool.skill { color: var(--err); }
  .tl-row.text { border-left-color: var(--agent); }
  .tl-row.step_finish { color: var(--fg-faint); }
  .tl-row.step_finish .info { color: var(--fg-faint); font-size: 11px; }
  .tl-row .info { color: var(--fg); }
  .tl-row .info .dim { color: var(--fg-dim); }

  details.collapse {
    border: 1px solid var(--border);
    border-radius: 6px;
    margin-bottom: 12px;
    background: var(--bg-2);
  }
  details.collapse > summary {
    padding: 10px 14px;
    cursor: pointer;
    list-style: none;
    color: var(--fg);
    display: flex; align-items: center; gap: 12px;
    font-size: 12px;
  }
  details.collapse > summary::-webkit-details-marker { display: none; }
  details.collapse > summary::before {
    content: "▸"; color: var(--fg-dim); width: 12px;
    transition: transform 0.15s;
  }
  details.collapse[open] > summary::before { transform: rotate(90deg); }
  details.collapse > summary .meta {
    margin-left: auto; color: var(--fg-faint); font-size: 11px;
  }
  details.collapse > .body {
    padding: 14px 18px;
    border-top: 1px solid var(--border);
    background: var(--bg);
    max-height: 600px;
    overflow: auto;
  }

  .meta-grid {
    display: grid;
    grid-template-columns: 220px 1fr;
    gap: 6px 18px;
    font-size: 12px;
    padding: 14px 18px;
    background: var(--bg-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    margin-bottom: 18px;
  }
  .meta-grid .k { color: var(--fg-dim); }
  .meta-grid .v { color: var(--fg); }
  .meta-grid .v b { color: var(--accent); font-weight: 600; }

  .pill {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 3px;
    background: var(--bg-3);
    color: var(--fg-dim);
    font-size: 11px;
    border: 1px solid var(--border-2);
  }

  .file-list { list-style: none; padding: 0; margin: 0; }
  .file-list li {
    padding: 6px 0;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
  }
  .file-list li:last-child { border: none; }
  .file-list .name { color: var(--accent); }
  .file-list .size { color: var(--fg-faint); margin-left: 12px; }

  .empty { color: var(--fg-faint); padding: 20px; text-align: center; font-style: italic; }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--border-2); border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--fg-faint); }
</style>
</head>
<body>
<header>
  <h1>
    <span class="accent">▣</span>
    <span class="dim">grilling-rep /</span> <span id="title">__TITLE__</span>
  </h1>
  <div class="stats" id="stats-header"></div>
</header>
<nav id="nav"></nav>
<main>
  <section id="overview" class="active"></section>
  <section id="conversation"></section>
  <section id="timeline"></section>
  <section id="subagents"></section>
  <section id="files"></section>
  <section id="simulator"></section>
  <section id="runlog"></section>
</main>

<script>
const DATA = __DATA__;

// -------- Header / stats --------
function fmt(n) { return new Intl.NumberFormat().format(n); }
function bytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1024*1024) return (n/1024).toFixed(1) + " KB";
  return (n/(1024*1024)).toFixed(1) + " MB";
}

const s = DATA.stats || {};
const tc = s.tool_counts || {};
document.getElementById("stats-header").innerHTML = `
  <span><b>${s.n_events ?? 0}</b> events</span>
  <span><b>${s.n_tool_uses ?? 0}</b> tools</span>
  <span><b>${s.subagent_count ?? 0}</b> sub-agents</span>
  <span><b>${s.files_written_count ?? 0}</b> files</span>
  <span>$<b>${(s.cost_usd ?? 0).toFixed(4)}</b></span>
  <span><b>${fmt(s.tokens_in ?? 0)}</b> in / <b>${fmt(s.tokens_out ?? 0)}</b> out</span>
`;

// -------- Nav --------
const TABS = [
  ["overview", "overview"],
  ["conversation", "conversation"],
  ["timeline", "timeline"],
  ["subagents", "sub-agents"],
  ["files", "files written"],
  ["simulator", "simulator log"],
  ["runlog", "run.log"],
];
const nav = document.getElementById("nav");
TABS.forEach(([id, label], i) => {
  const b = document.createElement("button");
  b.textContent = label;
  if (i === 0) b.classList.add("active");
  b.onclick = () => {
    document.querySelectorAll("nav button").forEach(x => x.classList.remove("active"));
    document.querySelectorAll("section").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    document.getElementById(id).classList.add("active");
  };
  nav.appendChild(b);
});

// -------- Overview --------
function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}
const toolRows = Object.entries(tc).sort((a,b) => b[1]-a[1])
  .map(([k,v]) => `<span class="pill">${esc(k)}:${v}</span>`).join(" ");
document.getElementById("overview").innerHTML = `
  <h2>run metadata</h2>
  <div class="meta-grid">
    <div class="k">rep dir</div><div class="v"><code>${esc(s.rep_dir ?? "")}</code></div>
    <div class="k">events</div><div class="v"><b>${s.n_events ?? 0}</b> (${s.n_text_events ?? 0} text, ${s.n_tool_uses ?? 0} tool_use, ${s.n_step_finishes ?? 0} step_finish)</div>
    <div class="k">tool counts</div><div class="v">${toolRows}</div>
    <div class="k">files written by agent</div><div class="v"><b>${s.files_written_count ?? 0}</b></div>
    <div class="k">sub-agents spawned</div><div class="v"><b>${s.subagent_count ?? 0}</b></div>
    <div class="k">cost (opencode self-report)</div><div class="v">$<b>${(s.cost_usd ?? 0).toFixed(4)}</b></div>
    <div class="k">tokens</div><div class="v"><b>${fmt(s.tokens_in ?? 0)}</b> in / <b>${fmt(s.tokens_out ?? 0)}</b> out</div>
  </div>

  <h2>initial prompt</h2>
  <div class="turn"><div class="turn-body"><pre>${esc(DATA.initial_prompt)}</pre></div></div>
`;

// -------- Conversation --------
const traj = DATA.trajectory.messages || [];
const convSec = document.getElementById("conversation");
if (!traj.length) {
  convSec.innerHTML = `<div class="empty">No trajectory messages.</div>`;
} else {
  convSec.innerHTML = `<h2>turn-by-turn conversation (${traj.length} messages)</h2>` +
    traj.map((m, i) => {
      const role = m.role === "assistant" ? "agent"
                 : m.role === "user" ? (i === 0 ? "user (fixed seed)" : "sim")
                 : m.role;
      const cls = role.startsWith("agent") ? "agent"
                : role.startsWith("sim") ? "sim"
                : "user";
      const content = typeof m.content === "string" ? m.content : JSON.stringify(m.content, null, 2);
      return `
        <div class="turn">
          <div class="turn-head">
            <span class="role ${cls}">${esc(role)}</span>
            <span>turn ${i+1}</span>
            <span style="margin-left:auto">${fmt(content.length)} chars</span>
          </div>
          <div class="turn-body"><pre>${esc(content)}</pre></div>
        </div>`;
    }).join("");
}

// -------- Timeline --------
const tl = DATA.timeline || [];
const tlSec = document.getElementById("timeline");
tlSec.innerHTML = `<h2>event timeline (${tl.length} events)</h2>` +
  tl.map((e, i) => {
    const t = e.type;
    let info = "";
    if (t === "text") {
      info = `<span class="dim">${fmt(e.text_len ?? 0)} chars</span> ${esc((e.text_preview ?? "").slice(0, 100))}`;
    } else if (t === "tool_use") {
      const parts = [];
      if (e.description) parts.push(`<span class="dim">desc:</span> ${esc(e.description)}`);
      if (e.filePath) parts.push(`<span class="dim">file:</span> ${esc(e.filePath)}`);
      if (e.command) parts.push(`<span class="dim">cmd:</span> ${esc(e.command)}`);
      info = parts.join(" &nbsp; ");
    } else if (t === "step_finish") {
      const tok = e.tokens || {};
      info = `<span class="dim">in</span> ${fmt(tok.input ?? 0)} <span class="dim">out</span> ${fmt(tok.output ?? 0)} <span class="dim">cache-r</span> ${fmt((tok.cache||{}).read ?? 0)} <span class="dim">cost</span> $${(e.cost ?? 0).toFixed(4)}`;
    }
    const tool = e.tool ? `<span class="tool ${e.tool}">${esc(e.tool)}</span>` : "";
    return `
      <div class="tl-row ${t}">
        <div class="idx">${i}</div>
        <div class="type">${esc(t)}</div>
        <div>${tool}</div>
        <div class="info">${info}</div>
      </div>`;
  }).join("");

// -------- Sub-agents --------
const sub = DATA.sub_agents || [];
const subSec = document.getElementById("subagents");
if (!sub.length) {
  subSec.innerHTML = `<div class="empty">No sub-agents extracted. Run extract_grilling_artifacts.py first.</div>`;
} else {
  subSec.innerHTML = `<h2>parallel sub-agents (${sub.length})</h2>` +
    sub.map(a => `
      <details class="collapse">
        <summary>
          <span>${esc(a.name)}</span>
          <span class="meta">${fmt(a.body.length)} chars</span>
        </summary>
        <div class="body"><pre>${esc(a.body)}</pre></div>
      </details>
    `).join("");
}

// -------- Files written --------
const files = DATA.extracted_files || [];
const filesSec = document.getElementById("files");
if (!files.length) {
  filesSec.innerHTML = `<div class="empty">No files written by agent (or extraction not run).</div>`;
} else {
  filesSec.innerHTML = `<h2>files written by agent (${files.length})</h2>` +
    files.map(f => `
      <details class="collapse" open>
        <summary>
          <span>${esc(f.name)}</span>
          <span class="meta">${bytes(f.body.length)}</span>
        </summary>
        <div class="body"><pre>${esc(f.body)}</pre></div>
      </details>
    `).join("");
}

// -------- Simulator log --------
const sim = DATA.simulator_log || [];
const simSec = document.getElementById("simulator");
if (!sim.length) {
  simSec.innerHTML = `<div class="empty">No simulator log captured for this rep.<br>(Added in orchestrator post-rep1 - future runs will populate this.)</div>`;
} else {
  simSec.innerHTML = `<h2>simulator API calls (${sim.length})</h2>` +
    sim.map((e, i) => `
      <details class="collapse">
        <summary>
          <span>call ${i+1} - ${esc(e.ts ?? "")}</span>
          <span class="meta">${(e.elapsed_s ?? "?")}s · in ${esc(((e.usage||{}).prompt_tokens) ?? "?")} / out ${esc(((e.usage||{}).completion_tokens) ?? "?")}</span>
        </summary>
        <div class="body"><pre>${esc(JSON.stringify(e, null, 2))}</pre></div>
      </details>
    `).join("");
}

// -------- run.log --------
document.getElementById("runlog").innerHTML = `
  <h2>orchestrator run.log</h2>
  <div class="turn"><div class="turn-body"><pre>${esc(DATA.run_log)}</pre></div></div>
`;
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
