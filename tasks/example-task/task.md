# example-task - what the agent sees

This file is the **public spec** the runner pipes to the agent. The reviewer-only
answer key lives in `task.private.md.example` in this public repo, and the
structured findings catalog is represented by `findings.json.example`. In a real
task, copy those examples to private, non-example files and do not include them
in the agent's prompt.

## Repo state

| Field         | Value                                       |
|---------------|---------------------------------------------|
| Repo          | _(path to the codebase under evaluation)_   |
| Pinned commit | _(SHA - runners check out this exact rev)_  |
| Branch tip    | _(may have drifted past the pinned commit)_ |

Runners must check out the pinned commit directly, not the branch tip.

## Mode - advisory only

The skill's exploration + candidate-presentation legs are evaluated. The grilling
loop and implement leg are interactive by design (the engineer answers probing
questions) and cannot be evaluated autonomously without distorting the loop.

The agent produces, and the run ends here:

1. A short diagnosis of architectural friction in the relevant modules.
2. A ranked list of deepening-opportunity candidates with `Files`, `Problem`,
   `Solution`, `Benefits` for each.
3. A gate question (`Which would you like to explore?` or equivalent).

No interface is proposed and no code is edited. If the agent ignores the gate
and starts implementing, that is a run-shape compliance failure (subcheck 7c,
hard-fail-shaped) scored on the rubric, not a separate "patch mode" deliverable.

## Prompt

### Skill-on (treatment)

```
Use /improve-codebase-architecture on this repo. Surface the highest-leverage
deepening opportunities you can find. Follow the skill's process: explore,
present candidates with full structure (Files / Problem / Solution / Benefits),
then ask the user which candidate to expand. Stop at the gate question.
```

### Skill-off (control)

```
Walk through this repo and identify where the architecture is hurting
testability and locality. Present a ranked list of refactor candidates,
each with files, the problem, a proposed change, and the benefit. Ask
which one I want to expand on. Don't expand or implement until I answer.
```

The control deliberately omits the skill's vocabulary (Module, Interface, Depth,
Seam, Adapter, Leverage, Locality), the deletion test, and the prescriptive
`Files / Problem / Solution / Benefits` template. It keeps the same shape
(candidates → gate, no implement) so the comparison is fair: same task, same
stop condition, only the skill scaffold differs. Whether the agent reaches
similar quality without this scaffold is the variable under test.

## Constraints

- No code edits. The agent stops at the gate question.
- Tests in candidate `Solution` sections should target the deepened interface,
  not internal state - but as written prose, not executed tests.
- Existing ADRs respected unless the agent explicitly justifies overriding them.

## Acceptance criteria (machine-checkable on transcript)

| Check                     | Tool                          | Notes                                                            |
|---------------------------|-------------------------------|------------------------------------------------------------------|
| Candidate count           | `transcript_checks.py`        | >=3 candidates expected; <3 is a process-compliance failure      |
| Structure markers         | `transcript_checks.py`        | each candidate has Files / Problem / Solution / Benefits         |
| Gate present              | `transcript_checks.py`        | "Which would you like to explore?" or equivalent                 |
| Forbidden vocab           | `transcript_checks.py`        | "boundary" / "service" used as architectural terms - penalty     |
| TS-sigs in candidate body | `transcript_checks.py`        | Skill forbids during candidate stage - penalty                   |
| File mentions (unique)    | `transcript_checks.py`        | Distinct files referenced; high = grounded in real structure     |
| Line-precision citations  | `transcript_checks.py`        | Accepts `file.ts:N` and `file.ts (lines N-M)` styles             |
| Word count                | `transcript_checks.py`        | Reported alongside cost; signals waffle vs density               |

## Acceptance criteria (rubric)

See [`../../rubrics/architecture-rubric.md`](../../rubrics/architecture-rubric.md)
(86 pts rubric total + 7 pt skill-adherence panel for skill-on runs only).

## Public/private split

This file is public - it is what a runner pipes to the agent (or a reviewer
reads when constructing the prompt). The reviewer's answer key is split out so
it cannot leak through the runner:

- **`task.private.md`** - known architectural pressures, scoring guidance
  (published here only as `task.private.md.example`).
  **Do not feed this file to the agent.**
- **`findings.json`** - structured catalog of verified positive findings
  (published here only as `findings.json.example`)
  (defects/smells with file:line locations) and negative traps (claims that
  look right but are false). Used by rubric criterion 1 and the
  `findings_recall` panel.

## Repeats

n=3 per arm per cell at minimum. Variance across repeats is itself a signal:
a model that scores 75 / 40 / 80 is differently useful than one that scores
65 / 65 / 65.

## Cost & time

Record per run: tokens_in, tokens_out, wall_seconds, and reconciled USD from
the inference provider's activity export (not the harness's self-report).
No score is comparable across cost tiers without this.
