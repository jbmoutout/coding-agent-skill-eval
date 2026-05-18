# Architecture rubric - 86 pts (+ 7 pt skill-adherence panel)

For tasks invoking [`/improve-codebase-architecture`](https://github.com/mattpocock/skills/tree/main/improve-codebase-architecture). Vocabulary follows the skill's [LANGUAGE.md](https://github.com/mattpocock/skills/blob/main/improve-codebase-architecture/LANGUAGE.md): **Module**, **Interface**, **Implementation**, **Depth**, **Seam**, **Adapter**, **Leverage**, **Locality**.

Scope: advisory only - exploration, candidate presentation, gate. The skill's grilling loop and implement leg are interactive (the human answers probing questions) and out of scope for autonomous eval.

> **Status (2026-05-13):** No LLM judge has been wired or run yet. All scoring in the pilot is **human** (rubric criteria) or **mechanical regex** (transcript_checks, findings_recall, tool_use binary signals). The "Judge guidelines" section below is forward-looking specification for *when* we wire a judge - not a record of judge use. The calibration thresholds (≥30 hand-scored runs, judge–human delta ≤4, κ ≥0.7) gate when a judge becomes eligible for any criterion. We are at 0/30 hand-scored runs.

> **v2.2 audit note (2026-05-14):** After the 18-rep, 3-cell pilot + blind rescore (n=3) + cross-family judge check (n=9), three calibration notes have been added: (1) findings requiring implicit-credit framing (e.g. `userId_trust`) carry ±1 cross-judge noise on findings_recall; (2) prose-coherence degradation (CJK leak, malformed JSON) is a quality signal observable in smaller-model runs - noted, not yet scored; (3) hybrid "assistant proposes / human ratifies" scoring can inflate stand-out high-scoring reps by up to 14% of /86 - strongest mitigation is a blind second-scorer pass on any rep that breaks the cell's score distribution.

The rubric total is **/86 - scored equally for skill-on and skill-off variants** so the comparison is fair. A separate **/7 skill-adherence panel** is reported only for skill-on runs; it answers "did the skill take?" and is excluded from the rubric total because skill-off runs would be unfairly penalised on skill-specific behaviours they were never instructed to exhibit.

## Weights

| #   | Criterion                | Weight | Scope                |
|-----|--------------------------|-------:|----------------------|
| 1   | Problem identification   |     15 | both variants        |
| 2   | Depth-improvement framing|     20 | both variants        |
| 3   | Locality reasoning       |     15 | both variants        |
| 4   | Leverage reasoning       |     10 | both variants        |
| 5   | Seam quality             |     10 | both variants        |
| 6   | Testing strategy         |     10 | both variants        |
| 7   | Run-shape compliance     |      6 | both variants        |
|     | **Rubric total**         | **86** | **both variants**    |
| -   | Skill-adherence panel    |      7 | skill-on only, separate |

> v1 weighted process compliance at 5; v2 raised it to 15 then split it. The split is because criteria like "used the deletion test" or "no forbidden 'boundary' vocab" measure *adherence to the skill*, not architectural quality - scoring skill-off runs on those biases the comparison toward finding "the skill helps" by construction. Run-shape compliance (≥3 candidates, gate, codebase walk) is general advisory hygiene and applies to both variants.
>
> Criteria 2 / 3 / 4 score the *quality of the framing* in the advisory output (does the candidate argue depth / locality / leverage with evidence?), not the realised effect of an implementation. Score the proposal as written.

### Vocabulary-agnostic scoring (audit note)

Criteria 2 (depth-improvement), 3 (locality), 4 (leverage), 5 (seam quality) use the skill's vocabulary in their definitions, but score the **substance of the argument**, not the words. A skill-off run that argues "this change concentrates complexity inside the module so callers stop needing to know about X" scores the same as a skill-on run that says "this deepens the interface; callers leverage less knowledge." Per Anthropic's guidance that "agents shouldn't fail due to ambiguous specs": the skill-off prompt asks for the substance (problem, proposed change, benefit); the rubric must score that substance regardless of whether the agent reached for skill vocabulary.

Vocabulary use *is* scored - but only in the skill-adherence panel (sa3, sa4), which is excluded from the rubric total for skill-off runs. The rubric measures architectural quality; the panel measures adherence. Don't conflate them when scoring.

For criterion 6 (testing strategy): both prompts mention testability (skill-on via skill direction, skill-off explicitly). An implicit testing argument ("this change makes the auth module easier to test in isolation") counts. Full marks require naming concrete test changes, but a partial credit at 4–7 should reward substance even without prescribed test names.

## 1. Problem identification (15)

Did the agent find real architectural friction, not cosmetic cleanup?

Cross-check claims against the task's findings catalog - a hand-verified JSON file named `<task-id>.findings.json` adjacent to the task spec. It contains `positive_findings` (verified defects / smells / shallow modules with file:line locations) and `negative_findings` (false-positive traps - e.g. a plausible-sounding "dead code" claim about a file that is actually imported by other modules). Surfacing a positive entry = credit; asserting a negative entry as a problem = penalty, regardless of how confident the prose sounds.

| Score | Description                                                                                                |
|------:|------------------------------------------------------------------------------------------------------------|
|   15  | Names the highest-leverage friction in the repo with file:line evidence; correctly clusters related issues |
|   12  | Names a real friction with evidence but misses the highest-leverage one                                    |
|    9  | Names a real friction without evidence                                                                     |
|    6  | Names cosmetic issues (naming, formatting, file size) as architectural problems                            |
|    3  | Hallucinates issues that don't exist in the code                                                           |
|    0  | No identification or off-task                                                                              |

## 2. Depth-improvement framing (20)

Does the proposed change *as argued* move complexity behind a smaller, more useful interface? Reviewer applies the **deletion test** to the proposal: if the proposed module were deleted, would complexity vanish (pass-through - bad) or reappear across N callers (deep - good)?

| Score | Description                                                                                                |
|------:|------------------------------------------------------------------------------------------------------------|
|   20  | Argues a clear depth gain - significantly less caller knowledge required, real behaviour concentrated     |
|   16  | Depth gain argued but with a small ceremony cost; net positive                                             |
|   12  | Argues reorganisation without arguing depth; complexity moved, not reduced                                 |
|    8  | Proposes a layer callers must thread through (shallower, not deeper)                                       |
|    4  | Proposes splitting an existing deep module into shallow ones                                               |
|    0  | No depth argument made, or argument actively worsens depth                                                 |

## 3. Locality reasoning (15)

If the proposal were applied, would bugs, changes, and required knowledge be concentrated?

| Score | Description                                                                                                |
|------:|------------------------------------------------------------------------------------------------------------|
|   15  | Argues that a future bug in this area becomes fixable in one place; a future change touches one file       |
|   12  | Argues mostly local; one or two known leaks remain and are named                                           |
|    9  | Argues some locality gain; still requires bouncing between 2–3 files                                       |
|    6  | Locality argument absent or unchanged                                                                      |
|    0  | Proposal would worsen locality (knowledge spreads further than before)                                     |

## 4. Leverage reasoning (10)

How much would callers get from how little they need to know, if the proposal were applied?

| Score | Description                                                                                                |
|------:|------------------------------------------------------------------------------------------------------------|
|   10  | Argues rich behaviour from a tiny interface; many call-sites simplified                                    |
|    7  | Argues some call-sites simplified; interface roughly proportional to behaviour                             |
|    4  | Interface as complex as behaviour; little leverage argued                                                  |
|    0  | Interface more complex than the behaviour it gates                                                         |

## 5. Seam quality (10)

Is the seam real and useful, or invented for one adapter?

The skill's rule: **one adapter = hypothetical seam, two adapters = real seam.**

| Score | Description                                                                                                |
|------:|------------------------------------------------------------------------------------------------------------|
|   10  | Seam has ≥2 real adapters or a clear concrete plan for the second; placement is on a domain concept        |
|    7  | One adapter today, second adapter justified by an upcoming/named concrete need                             |
|    4  | One adapter; seam is hypothetical but at least sits on a domain concept                                    |
|    0  | Seam introduced for testability alone; no second adapter exists or is planned                              |

## 6. Testing strategy (10)

The proposal describes tests at the deepened interface, not at internal implementation. **Replace, don't layer** - old shallow tests named as redundant when applicable. Assertions described as targeting observable outcomes.

| Score | Description                                                                                                |
|------:|------------------------------------------------------------------------------------------------------------|
|   10  | Names tests at the deepened boundary; identifies which old tests would become redundant; observable assertions |
|    7  | Names tests at the boundary; doesn't address which existing tests are now redundant                        |
|    4  | Names tests at internal implementation rather than the boundary                                            |
|    2  | Mentions testability without naming concrete test changes                                                  |
|    0  | No testing strategy in the candidate                                                                       |

## 7. Run-shape compliance (6)

General advisory hygiene any reasonable run should follow, regardless of skill. Scored for both skill-on and skill-off variants and included in the rubric total.

| #    | Check                                                                                  | Pts |
|------|----------------------------------------------------------------------------------------|----:|
| 7a   | Walked the codebase strategically (subagent, glob+grep, or staged file reads) rather than blind globbing or jumping to a single file | 1 |
| 7b   | Presented ≥3 candidates                                                                |   3 |
| 7c   | Asked a gate question and stopped - did not expand or implement unbidden               |   2 |

7c is hard-fail-shaped: an agent that races past the gate scores 0 here regardless of output quality.

## Skill-adherence panel (7, skill-on only)

Behaviour specific to `/improve-codebase-architecture`'s vocabulary, template, and prohibitions. **Reported as a separate panel, not part of the rubric total.** Skill-off runs are NOT scored on this panel - penalising them for failing to use vocabulary they were never given would bias the comparison toward concluding "the skill works" by construction.

| #    | Check                                                                                  | Pts |
|------|----------------------------------------------------------------------------------------|----:|
| sa1  | Each candidate has Files / Problem / Solution / Benefits structure                     |   2 |
| sa2  | Did **not** include TypeScript signatures inside candidate `Solution` sections         |   2 |
| sa3  | Did **not** use forbidden vocab as architectural categories ("boundary", bare "service") |   2 |
| sa4  | Used the deletion test or skill vocabulary (Depth / Leverage / Locality / Seam) with intended meaning |   1 |

> The original sa1 ("read project glossary / ADRs before exploring") was dropped after verifying no ADRs or `docs/` tree exist in the worked-example task's repo at the pinned commit. The skill directs this behaviour but there was nothing to read; auto-passing would add noise, so the subcheck was dropped and the panel total reduced from 9 to 7. If a future task is on a repo with ADRs, reintroduce the subcheck.

sa2 and sa3 penalise failure modes seen in pilot transcripts: pre-proposed TypeScript signatures and forbidden architecture vocabulary. The skill-adherence score answers a different question from the rubric total: "did the skill take?" - orthogonal to "did the run produce good architecture advice?" A high rubric total with a low skill-adherence score means the agent reached good output without the scaffold (interesting). Low rubric with high adherence means the agent followed the template but didn't think (also interesting). They're separate questions.

## Judge guidelines

This rubric is human-first. An LLM judge can apply parts of it but has known biases on this task.

### Wiring an LLM judge

**Model selection.** Use the best model available from Anthropic or OpenAI.

**Prompt construction.** Follow [G-Eval methodology](https://arxiv.org/abs/2303.16634) (Liu et al. 2023), plus two practical additions from [Anthropic's eval guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents):

- **Chain-of-thought in the judge prompt** - ask the judge to walk through the criterion's logic (e.g. apply the deletion test, name the seam, count adapters) before emitting a score. Direct numeric scoring underperforms.
- **Form-filling output** - one field per criterion, structured (`{ "depth_improvement": 16, "rationale": "..." }`), not free-form prose. Reduces variance and keeps the judge anchored to the rubric.
- **Probability-weighted scoring** - when the judge model exposes logprobs, weight scores by token probability of "1"/"2"/.../"5" rather than argmax. G-Eval shows this reduces score ties and improves rank correlation with human judgments (Liu et al. 2023). (As of 2026-05-14, this requires an OpenAI judge - the Anthropic API does not expose token logprobs.)
- **Give the judge a way out.** Instruct it to return `"Unknown"` or `"Insufficient evidence"` for any criterion it cannot confidently score, rather than guess. Hallucinated scores are worse than missing ones - they enter the calibration math indistinguishably from real ones. Treat `"Unknown"` as a routed-to-human flag.
- **Isolated judge per criterion.** Score each criterion with its own focused judge call rather than asking one judge to fill the whole rubric in a single pass. Keeps each chain-of-thought anchored to one definition and avoids cross-criterion contamination (e.g. high "problem identification" leaking optimism into "depth-improvement framing"). Trade-off is cost: roughly N× more judge calls for N criteria - acceptable for a small pilot, revisit if scaling.

**Calibrate before trusting.** Vendor and survey writeups in 2025–2026 cite **85–90% judge–human agreement** as a working production bar - treat it as a proposed target for this eval, not a settled industry standard. Path: hand-score ≥30 runs (v1 transcripts give ~14 free; add ≥16 from new runs), hold out 20% as a validation set. A criterion is judge-eligible only when both:

- Judge–human score delta averages ≤20% of the criterion's maximum (≤4 pts on /20, ≤3 on /15, ≤2 on /10, ≤1 on /6), AND
- Inter-rater agreement on that criterion is ≥0.7 (Cohen's κ for binary subchecks, weighted κ or Pearson for ordinal scores).

Any criterion failing either threshold goes back to human-only until enough new calibration data is added to clear it. Both numbers (85–90%, ≥0.7) are starting points; revisit after the calibration set is built.

### Known biases on this task

1. **LLM judges over-reward surface markers.** More interfaces, more abstractions, more files = higher score, but the deletion test usually says the opposite. **Score criteria 2, 3, 5 by hand only** in the pilot. Let the judge handle 1, 4, 6 (and the binary subchecks of run-shape compliance and skill-adherence) - and only after calibration. Criterion 4 is judge-eligible (after calibration) because the strongest signal - "how many call-sites simplified" - is countable from the proposal text, unlike the more interpretive 2/3/5 framings. If calibration shows judge–human delta failing the threshold, demote 4 to hand-only.
2. **Cost.** Never quote a rubric score without `$/run` alongside it. A 78 at $1 and an 85 at $8 are different recommendations.
3. **Variance.** Quote min / median / max across n≥3 repeats, not just the mean. A model with scores 80 / 35 / 80 is unsafe even with median 80.

## Score reporting format

```json
{
  "run_id": "arch-001-opus47-skill-claudecode-rep1",
  "task": "arch-001",
  "model": "claude-opus-4-7",
  "harness": "claude-code",
  "skill": "/improve-codebase-architecture",
  "repeat": 1,
  "scored_by": "human",
  "scored_against_reference": false,
  "rubric": {
    "problem_identification": 13,
    "depth_improvement": 16,
    "locality_reasoning": 12,
    "leverage_reasoning": 8,
    "seam_quality": 7,
    "testing_strategy": 6,
    "run_shape_compliance": {
      "7a": 1, "7b": 3, "7c": 2,
      "total": 6
    },
    "total": 68
  },
  "skill_adherence": {
    "sa1": 2, "sa2": 2, "sa3": 2, "sa4": 1,
    "total": 7,
    "applies": true
  },
  "transcript_checks": {
    "candidate_count": 6,
    "structure_pass": true,
    "gate_present": true,
    "forbidden_vocab_hits": 0,
    "ts_sigs_in_candidates": 0,
    "file_mentions_unique": 36,
    "line_citations": 1,
    "word_count": 1752
  },
  "findings_recall": {
    "positives_surfaced": ["userId_trust", "dual_prisma", "rating_star_duplicate"],
    "positives_missed": ["jwt_commented_out", "atob_jwt_parse", "stale_middleware", "weekly_plan_history_errors", "dual_seasonality"],
    "negatives_asserted": ["extractor_dead_code"],
    "recall": 0.375,
    "false_positive_count": 1
  },
  "tool_use": {
    "tool_call_count": 23,
    "useful_call_ratio": 0.78,
    "context_pollution_events": 0,
    "exploration_convergence": "narrowed",
    "time_to_first_candidate_seconds": 187,
    "tokens_to_first_candidate": 8400
  },
  "cost": {
    "usd_lower_bound": 0.225,
    "subagent_cost_captured": false,
    "cache_hit_ratio": "0/7",
    "tokens_in": 154187,
    "tokens_out": 3801,
    "reasoning_tokens": 0,
    "notes": "Sum-of-step_finish cost; subagent session cost not included; --thinking emitted no reasoning tokens via opencode→OpenRouter→Anthropic; cache_control not set so prompt is re-billed every turn"
  },
  "wall_seconds": 337,
  "notes": "..."
}
```

The `findings_recall`, `tool_use`, `skill_adherence`, and `cost` panels sit parallel to the rubric - never collapse them into the rubric total. Per HELM: report axes separately. Never quote rubric without cost AND tool-use alongside. For skill-on vs skill-off comparisons, compare on `rubric.total` (/86); the 7-pt skill-adherence panel is reported but not summed in.

## Tool-use metric definitions

The `tool_use` panel needs concrete definitions or it becomes hand-wavy. For the pilot, some metrics are human-judged from transcripts; parsers can replace them later. Tag each metric in the JSON with `extraction: "manual" | "automatic"` so downstream analysis can weight the noise floor.

| Metric                              | Definition                                                                                              | Pilot extraction                                              | Eventual extraction                                            |
|-------------------------------------|---------------------------------------------------------------------------------------------------------|---------------------------------------------------------------|----------------------------------------------------------------|
| `tool_call_count`                   | Total tool invocations from run start to gate question.                                                 | Count from harness session JSON.                              | Same.                                                          |
| `useful_call_ratio`                 | Calls whose output is referenced (citation, file path, or quoted snippet) in the final advisory ÷ total. | Manual: tag each call cited / not cited.                      | Regex correlation between tool output content and final transcript. |
| `context_pollution_events`          | Events where context-management forced loss of working state: compactions, context-window overflow, observable re-reads of the same file. | Count from harness logs (opencode emits compaction events; Claude Code reports context %). | Same; better cross-harness telemetry.                          |
| `exploration_convergence`           | Did exploration narrow toward the highest-leverage area or thrash?                                       | Human label: `narrowed` / `mixed` / `thrashed`.               | Approximated by entropy over file-paths-touched per turn.      |
| `time_to_first_candidate_seconds`   | Wall-seconds from run start to the first complete candidate (Files + Problem + Solution + Benefits block). | Manual marker on transcript.                                  | Parser keyed on the candidate template.                        |
| `tokens_to_first_candidate`         | Tokens consumed (in + out) from start to first candidate.                                                | Sum from session JSON up to the marked turn.                  | Same.                                                          |

Until parsers are written, all rows in `tool_use` carry `extraction: "manual"`. Differences between manual scorers should be checked on a calibration subset before any of these metrics are used to compare runs.
