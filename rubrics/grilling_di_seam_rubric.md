# Grilling DI-seam SETTLED_DESIGN.md rubric

Hand-authored Layer 2 checklist for grading SETTLED_DESIGN.md outputs against
the DI-seam candidate's load-bearing constraints. Each item has:
- A short id (used in the grader's output)
- The constraint statement
- Search terms / patterns the grader looks for (text-search hint)
- Verification question for the small LLM call ("substantively addressed?")

Derived from:
- The persona's flagged constraints (`personas/grilling_persona.md`)
- The DI-seam candidate's INITIAL_PROMPT (`scripts/run_grilling_openevals.py`)
- Facts verified against the anonymous target codebase at the pinned commit
- Patterns surfaced while comparing pilot SETTLED designs

n = 11 constraints. Severity column reflects how load-bearing each is:
- **critical**: design is broken / unimplementable if missing
- **important**: design ships but has a real defect
- **nice-to-have**: design is more polished if addressed

---

## 1. prisma_dedup - Single canonical Prisma client

**severity**: critical
**search_terms**: `lib/prisma.ts`, `app/lib/prisma.ts`, `duplicate`, `dedup`, `consolidate`, `canonical`
**question**: Does the design specify which of the two Prisma module paths is canonical and explicitly delete the duplicate?

The persona shows two Prisma singletons (`lib/prisma.ts` no logging, `app/lib/prisma.ts` with `log: ['query']`). Any DI seam design must collapse these to one.

## 2. disconnect_removal - Remove `prisma.$disconnect()` from route handlers

**severity**: critical
**search_terms**: `$disconnect`, `disconnect`, `finally`, `singleton`, `lifecycle`
**question**: Does the design state that `$disconnect()` calls in route `finally` blocks must be removed, with rationale (singleton connection pool)?

Verified in repo: present in 21 places. Disconnecting the singleton on per-request basis is the documented anti-pattern.

## 3. anthropic_lazy - Anthropic client lazy construction

**severity**: critical
**search_terms**: `lazy`, `ANTHROPIC_API_KEY`, `module load`, `import time`, `on first call`, `Adapter`, `defer`, `instantiat`, `at construction`, `wraps new Anthropic`
**question**: Does the design defer `new Anthropic(...)` instantiation away from module-load so non-LLM routes / tests don't crash when `ANTHROPIC_API_KEY` is unset?

The persona flags this: `app/lib/anthropic.ts` is a module-level singleton, any module importing it crashes in tests without the key.

## 4. jwt_consolidation - JWT verification consolidation

**severity**: important
**search_terms**: `jwtVerify`, `jose`, `requireUser`, `auth`, `JWT_SECRET`, `middleware`
**question**: Does the design propose a single auth helper (e.g. `requireUser(request)`) to replace inline `jwtVerify` calls scattered across route files?

Persona: most routes do their own JWT verification inline. Middleware only covers 5 paths.

## 5. userid_trust_gap - Fix `userId` trust gap

**severity**: critical (security)
**search_terms**: `userId`, `request.json`, `searchParams`, `trust`, `unverified`, `weekly-plan-history`, `weekly-planner/reset`
**question**: Does the design identify routes that accept `userId` from request body or query params (instead of from verified JWT) and require them to receive `userId` only from the verified auth context?

Verified in repo:
- `app/api/weekly-plan-history/route.ts:6` → `const { userId, recipeId, status } = await request.json()`
- `app/api/weekly-planner/reset/route.ts:37` → `const userId = searchParams.get("userId")`

Both are real auth-bypass vulnerabilities. Catching this is the highest-quality move a SETTLED can make.

## 6. model_name_centralization - Centralize Anthropic model name

**severity**: important
**search_terms**: `claude-sonnet-4`, `model:`, `model name`, `constant`, `centraliz`, `LLM_MODEL`
**question**: Does the design specify that the Anthropic model name (currently `claude-sonnet-4-20250514`) is held in a single constant inside the DI seam, NOT passed by the caller for each invocation?

Persona: model name hardcoded in 5–6 files. Passing `model` as a per-call parameter on a port interface re-opens this drift.

## 7. temperature_split - Handle weekly-planner temperature 0.3 vs 0.1 split

**severity**: important
**search_terms**: `temperature`, `0.3`, `0.1`, `weekly-planner`, `alternatives`, `default`
**question**: Does the design address the existing temperature divergence (weekly-planner uses 0.1, weekly-planner/alternatives uses 0.3) - either by carrying temperature through the port, defining named operations with their own defaults, or explicitly flagging the divergence?

Persona: "weekly-planner/alternatives route uses temperature 0.3 vs the main weekly-planner route's 0.1, without a clear reason."

## 8. test_seam - Test seam shape

**severity**: critical
**search_terms**: `test`, `mock`, `vitest`, `jest`, `inject`, `createTestContext`, `mockDeep`
**question**: Does the design specify a concrete test pattern that lets a SWE call route logic with a mock PrismaClient and a fake AiClient, without needing real API keys or a real database?

The persona: "no test suite ... want to make the code testable so I can finally add tests." The whole point of the DI seam.

## 9. wph_module_coupling - `lib/weekly-plan-history.ts` module-level prisma coupling

**severity**: important
**search_terms**: `weekly-plan-history`, `weekly-plan-history.ts`, `$executeRaw`, `$queryRaw`, `module-level`, `lib/weekly`
**question**: Does the design address `lib/weekly-plan-history.ts` - a non-route module that imports `prisma` at module top-level and uses `$executeRaw`/`$queryRaw` - either by migrating it to the DI seam pattern or explicitly deferring it with reason?

Verified in repo: `lib/weekly-plan-history.ts` has `import { prisma } from "@/lib/prisma"` + 4 `$executeRaw`/`$queryRaw` calls. None of the 5 designs we've seen addressed this. Worth scoring.

## 10. middleware_cleanup - `middleware.ts` stale matcher

**severity**: nice-to-have
**search_terms**: `middleware`, `middleware.ts`, `matcher`, `generate-shopping-list`, `stale`
**question**: Does the design address `middleware.ts` - specifically the stale matcher entry for `/api/generate-shopping-list` (a route that no longer exists) - either by deleting middleware entirely (auth moves into the seam) or by fixing the matcher?

Verified in repo: `middleware.ts` matcher includes `/api/generate-shopping-list` which has no route file.

## 11. migration_safety - Migration sequencing

**severity**: important
**search_terms**: `PR 1`, `PR 2`, `PR 3`, `migration`, `sequence`, `compile`, `tracer bullet`, `batch`
**question**: Does the design describe a PR sequence where the codebase compiles at every commit (e.g. additive first, route migration in batches, deletes last) - not a single big-bang PR that leaves intermediate broken states?

A design without this is dangerous to ship even if architecturally sound. Several of the produced designs got this right; one (deepseek-qwen-hardened) had a step-7 internal contradiction.

---

## Scoring

Per constraint, the grader emits one of:
- `ADDRESSED_CLEANLY` (1.0): explicit, substantive, with rationale
- `ADDRESSED_WEAKLY` (0.5): mentioned but not substantively (passing reference, hand-wave, or partial answer)
- `NOT_ADDRESSED` (0.0): no mention or contradicted

Per-rep `coverage_score` = sum of weighted item scores / max possible, where weights are:
- critical: 1.0
- important: 0.7
- nice-to-have: 0.4

Max possible = sum of weights. Score in [0, 1].

Reported alongside the breakdown so we can see WHICH items the design misses, not just an aggregate.
