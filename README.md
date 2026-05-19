# coding-agent-skill-eval

Working artifacts from an LLM evaluation methodology for coding-agent skills
on advisory tasks (architectural guidance, not code patches; no execution
oracle). Published as proof of work; not maintained for external reuse.

Two parts:

- **Part I - skill ablation**: pairs skill-on vs skill-off cells on the same
  advisory task, Docker-image-gated so the skill is genuinely *unavailable* in the
  off arm rather than just unprompted.
- **Part II - multi-turn agent ↔ sim**: replaces the engineer in the skill's
  grilling loop with a simulated SWE (LLM + codebase-grounded persona) and
  scores both the conversation and the resulting design artifact.

Methodology, not a benchmark. Agent, SIM, and judge each use any LLM,
independently. Worked example was `/improve-codebase-architecture`
(<https://github.com/mattpocock/skills>) against one anonymous Next.js
codebase at a pinned commit.

For the writeup, use this repository as the artifact bundle referenced by the
accompanying post.

## Repository layout

```
.
├── Dockerfile                         SKILL-ON arm image template
├── Dockerfile.noskills                SKILL-OFF arm image template (skill not installed)
├── requirements.txt                   Python dependencies for the helper scripts
├── config/
│   └── opencode.json.example          model registry for the agent harness
├── personas/
│   ├── grilling_persona.md            base SIM persona (Part II)
│   └── grilling_persona_hardened.md   anti-fabrication variant
├── rubrics/
│   ├── architecture-rubric.md         Part I /86 + /7 skill-adherence panel
│   └── grilling_di_seam_rubric.md     Part II must-address constraint checklist
├── schemas/
│   ├── score.json.example             Part I per-rep score schema
│   ├── eval_report.json.example       Part II per-rep eval report schema
│   └── simulator_log.jsonl.example    LangChain BaseCallbackHandler output shape
├── scripts/
│   ├── transcript_checks.py           Part I mechanical signals
│   ├── findings_recall.py             Part I atomic per-finding LLM judge
│   ├── tool_use.py                    trajectory observability panel
│   ├── reconcile_openrouter_costs.py  provider-CSV cost reconciliation
│   ├── run_grilling_openevals.py      Part II OpenEvals substrate
│   ├── run_no_grilling.py             Part II counterfactual control
│   ├── extract_grilling_artifacts.py  in-container artifact recovery
│   ├── build_grilling_viewer.py       per-rep HTML viewer
│   ├── grade_facts.py                 Part II Layer 1 (mechanical)
│   ├── grade_constraints.py           Part II Layer 2 (curator-anchored)
│   ├── grade_judge.py                 Part II Layer 3 (LLM judge A/B/C)
│   ├── grade_run.py                   Part II per-rep orchestrator
│   ├── compare_runs.py                Part II cross-rep ranking
│   └── model_names.py                 cell-label to model-name resolver
└── tasks/
    └── example-task/
        ├── task.md                    PUBLIC spec the agent sees
        ├── task.private.md.example    PRIVATE answer-key (reviewer only)
        ├── findings.json.example      curator-anchored catalog schema
        └── facts_config.json.example  ground-truth data for grade_facts.py
```

## Running the scripts

The scripts are plain Python entry points. Install the Python dependencies with:

```bash
python -m pip install -r requirements.txt
```

Most scripts operate on an existing per-rep results directory. LLM-backed
graders read API keys from `.env` or the relevant provider environment
variables; `.env` and real result artifacts are ignored by git.

## Methodology surface (what's in here, by purpose)

### Part I - skill ablation

**Environment**
- Docker, pinned commit, fresh container per rep
- `Dockerfile` vs `Dockerfile.noskills`: skill availability is set by the
  image, not by the prompt
- task dir split: public spec | private answer-key + `findings.json`

**Catalog (curator-anchored ground truth)**
- one verifiable claim per entry (FActScore precondition)
- fields: `atomic_claim`, `sub_claims`, `locations`, `evidence_quality`,
  `audit_log`, per-finding `audit_note`, `v1_attribution_caveat`
- negative entries: deliberately false claims; hedged phrasing still counts
  as a fall
- audit: hand against pinned commit + cross-family external reviewer

**Recall judge - `findings_recall.py`**
- one call per finding (no batching → no position bias)
- structured output: `surfaced`, `evidence_span`, `evidence_quality`,
  `confidence`, `reasoning`, explicit `"unknown"` exit
- routes: Anthropic, OpenAI, OpenRouter
- cross-family check on a subset; deltas reported as self-preference signal

**Rubric**
- /86 across 7 criteria (argument quality)
- /7 adherence panel for skill-on only, so skill-off isn't penalised for
  vocabulary it never had
- per-criterion: judge-eligible vs hand-only (calibration: n ≥ 30,
  judge-human Δ ≤ 4, κ ≥ 0.7)
- `scored_by` per rep: hand | hybrid | judge - bias profile declared
- blind rescore: fresh subagent receives only the rubric, catalog, and
  final text

**Cost reconciliation - `reconcile_openrouter_costs.py`**
- joins provider activity CSV to per-rep directories
- harness self-reports renamed `*_DEPRECATED_self_report` so they can never
  feed a derived metric
- `score.json` namespaced by source: `rubric`, `skill_adherence`,
  `transcript_checks_summary`, `findings_recall`, `tool_use`, `cost`

**Observability**
- `transcript_checks.py` - candidate count, gate, structure, forbidden
  vocab, type-sig leakage, citations, word count, prose-coherence
- `tool_use.py` - call count, by-tool, useful-call ratio, context-pollution,
  wall seconds, time-to-first-candidate, `synthesis_locus`, exploration
  convergence
- every metric tagged `manual`, `heuristic`, or `automatic` so the noise
  floor stays visible

**Repetition discipline**
- n = 3 reps per arm, 6 per cell; min / median / max reported, never mean
- failed reps retained as failures, never re-run
- novel-finding credit closes the catalog ceiling: agents surfacing verified
  defects outside the catalog earn credit instead of recall = 0;
  significance-weighted (+1 / +2 / +3 / +5) with verified-false penalty

### Part II - multi-turn agent ↔ sim

**Substrate - `run_grilling_openevals.py`**
- OpenEvals `run_multiturn_simulation` + opencode in a docker subprocess
- `fixed_responses=[INITIAL_PROMPT]`: deterministic turn 1, so the
  conversation that follows is the variable
- opencode session SQLite is volume-mounted (survives `docker --rm`)
- provider-error detection: OpenRouter 402, silent stalls
- SIM routing inferred from model name: `provider/model` → OpenRouter,
  bare name (e.g. `gpt-5.5`) → OpenAI direct
- pre-run safety: docker image vintage logged + overwrite guard on
  already-graded reps
- post-run validation: refuses to grade reps that didn't emit
  `SETTLED_DESIGN.md`; on success, auto-chains extract → grade → viewer
  → compare

**Persona**
- composite: generic SWE traits + codebase snippets at the pinned commit
- `grilling_persona.md` - base
- `grilling_persona_hardened.md` - 5 fabrication categories named, hedge
  phrases prescribed, "be opinionated" vs "don't fabricate" resolved in
  favour of the latter

**Terminal trigger + artifact recovery**
- terminal: agent writes `SETTLED_DESIGN.md` (an artifact, not a regex on
  conversation text)
- `extract_grilling_artifacts.py` replays in-container `write` / `edit`
  tool_uses to reconstruct files lost to `docker --rm`; extracts sub-agent
  prompts + outputs from `task` tool_uses; emits `timeline.jsonl` +
  `stats.json`
- LangChain `BaseCallbackHandler` → `simulator_log.jsonl` (prompts, tokens,
  latency)

**Scoring (three layers)**
- `grade_facts.py` - grep / count claims vs repo, no LLM
- `grade_constraints.py` - must-address checklist coverage
- `grade_judge.py` - A: SETTLED quality · B: per-turn SIM fidelity
  (GROUNDED / HEDGED / FABRICATED) · C: agent grilling quality
- `grade_run.py` orchestrates → `eval_report.{json,md}`
- composite = 0.30·facts + 0.25·coverage + 0.20·A + 0.15·C + 0.10·sim_fidelity
- judge validation gate: `|Δ| ≤ 1` on 1-5, qualitative match on
  fidelity classifications

**Validity tier (separate from composite)**
- 🟢 / 🟡 / 🔴 / ⚪ driven by SIM fidelity
- empirical motivation: smaller models in the SIM role were observed
  contaminating substance (invented backstory, numbers, opinions - not just
  stylistic drift)
- reps where the SIM invented input the agent built on are flagged as not
  citable as evidence about the agent, regardless of design score

**Counterfactual control - `run_no_grilling.py`**
- same agent, same candidate, same protocol, zero SIM
- graded by the same rubric, isolates loop contribution

## What is NOT included

By design - the published artifact is methodology, not findings:

- No `results/` directory, no `raw_export.jsonl`, no `score.json` with real numbers
- No `*_openrouter_activity_*.csv` and no reconciliation outputs
- The `tasks/example-task/findings.json.example` is schema-only with placeholder
  entries; the real catalog content from the worked example is not published
- The task spec is anonymized: codebase name, pinned commit, and per-task
  references are deliberately stripped from the public files

## References

- Liu et al., EMNLP 2023 - [G-Eval](https://arxiv.org/abs/2303.16634)
- Min et al., 2023 - [FActScore](https://arxiv.org/abs/2305.14251)
- Zheng et al., NeurIPS 2023 - [Judging LLM-as-a-Judge](https://arxiv.org/abs/2306.05685)
- Yao et al., 2024 - [τ-bench](https://arxiv.org/abs/2406.12045)
  (extended in [τ²-bench](https://arxiv.org/abs/2506.07982), 2025)
- Wang et al., ICLR 2024 - [MINT](https://arxiv.org/abs/2309.10691)
- Samuel et al., 2024 - [PersonaGym](https://arxiv.org/abs/2407.18416)
- Chopra et al., 2026 - [Beyond Cooperative Simulators (PPol)](https://arxiv.org/abs/2605.12894)
- LangChain - [OpenEvals](https://github.com/langchain-ai/openevals)
- 2026 - [Lost in Simulation](https://arxiv.org/abs/2601.17087)
- Anthropic engineering, 2026 - [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- Park et al., 2025 - [Play Favorites](https://arxiv.org/abs/2508.06709)
- Jimenez et al., 2024 - [SWE-bench / Verified](https://www.swebench.com/verified.html)
  (succeeded by SWE-bench Pro, 2026; cited as contrast for the no-execution case)

## License

MIT.
