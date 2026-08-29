# CLAUDE.md — trading-ai-assistance

> Invariants only. Where the project *is* lives in `STATUS.md`; what you may not break lives here.

## What this repository is

A **human-assistance tool**: a deterministic orchestrator that drives the Claude ↔ Codex
author/review loop for a *target* repository (first target: `c:\Repos\trading-ai`) until a
convergence condition holds, and reports to the operator at named human gates. It is **not a
third LLM**. It has **no model access, no database credentials, no provider keys**, and it
never edits target-repository content. Design authority: `docs/design/ORCHESTRATED_EXECUTION_DESIGN.md`
(read it before any change; its rulings are operator rulings, not suggestions).

The target repository's own rules bind every lane the orchestrator runs — for `trading-ai`
that is its `CLAUDE.md`, `AGENTS.md`, `dil-engine/docs/process/COLLABORATION_CONTRACT.md`
and `DECISIONS.md`. This repository never overrides a target's rules; it enforces them.

## Assistant role (Claude on this project)

Principal software architect and developer for a **process-safety tool**. Review every
change for: determinism (same inputs → same transition), fail-closed behaviour (silence,
malformed input, missing evidence, tripped counter → STOP, never PASS), auditability (every
transition and its predicate inputs in the ledger), and privilege minimalism (the tool
holds nothing an agent session should hold instead). Trading-domain judgement is the
*target* repo's concern; here the domain is orchestration correctness.

## Ways of working (binding)

Same operating model as the target: `trading-ai`'s collaboration contract applies here
verbatim — one author, one non-authoring reviewer, work-item manifest per change, P0–P3
severity gate, bounded rounds, deterministic verification. Claude authors by default;
Codex reviews (see `AGENTS.md`). The orchestrator may eventually run its own loop on this
repository; until then the operator relays as today.

## Architecture invariants

- **Deterministic state machine.** Transitions are pure functions of (state, RVA verdict,
  schema-validated `review.json` / `fold.json`, git facts, counters). No LLM call decides a
  transition. No prose is interpreted — structured artifacts only.
- **Convergence is a predicate**, never a judgement: reviewer verdict `CLEAN` (no P0–P2)
  on the current revision AND the target's deterministic gate PASS (code slice) or
  INCONCLUSIVE-with-scope-PASS (docs-only slice — never upgraded to PASS here).
- **Fail closed.** `max_rounds` exhaustion, ping-pong (a finding id reopened after two
  folds), two consecutive `REJECTED_WITH_REASON` on one finding, malformed artifacts after
  one retry, `UNKNOWN_CONTRACT`, `earlier_phase_gap`, `requires_ruling`, dirty/unexpected
  base, foreign commits in range, secret-scan hit, budget trip → STOP with a Human Gate
  Brief. Nothing lands on a STOP.
- **Verify, never trust.** After every agent return: commit exists and descends from the
  lane base; range contiguous with no foreign commits; changed files ⊆ `allowed_files`
  via the target's RVA (never a re-implementation); working tree clean; secret scan clean.
- **Human gates are detected, not remembered** — from the target manifest and diff (design
  §3.3). The orchestrator never performs an operator action (migrations, deployment, Test
  runs, external authorization); it prepares the runbook and waits.
- **Landing = fast-forward onto the target's integration branch, then push.** No merge
  commits, no squash; the target's range discipline depends on linear history. Land only
  converged slices whose tree digest equals the digest reviewed CLEAN.
- **Ledger is append-only** (`runs/<target>/<lane>.jsonl`), digest-chained, and records
  every invocation, artifact digest, transition with predicate inputs, and counter. Resume
  names the ledger line it resumes from — never "latest".
- **Reviewer runs in a read-only sandbox; author runs under the lane's tool allowlist.**
  Non-authoring review and design-only boundaries are structural, not instructions.
- **Secrets.** The orchestrator process holds only what git push needs. Agent env files
  are mounted into agent sessions per lane grant and never printed, logged, or persisted;
  neither agent's CLI config is ever read into a log (both contain connection strings).
- **One lane at a time.** No worktree exists for a lane that is not running.
- **Bookkeeping is rendered from the ledger**, not retyped: SHAs, ranges, round counts,
  `verified_slices`. Prose stays agent-authored.

## Environment

Python 3.10+ (3.14 locally). No checked-in venv:

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1; pip install -r requirements.txt -r requirements-dev.txt
```

External CLIs the orchestrator drives (must be on PATH; never vendored):
- `claude` (Claude Code) — headless via `claude -p … --output-format json`, `--resume`.
- `codex` (Codex CLI, `@openai/codex`) — headless via `codex exec`, read-only sandbox for
  review. **Not yet installed on the operator's machine (only the desktop app)** — M0 prereq.

Target repositories are configured in `targets/<name>.yaml` (path, integration branch,
manifest dir, gate command). No target path is hard-coded in source.

## Tests

```powershell
python -m pytest -q --tb=short                # full suite (fast; no network, no CLIs)
python -m pytest tests/test_state_machine.py -q --tb=short
```

Strict pytest (`filterwarnings=error`, `xfail_strict`). Tests never invoke real `claude`/`codex`
or touch a real target repo — drivers are behind interfaces with fake implementations.

## Testing discipline (CRITICAL — identical to the target's)

A failing test is fixed in production code, or you STOP and ask before touching the test.
Forbidden without explicit approval: skip/xfail to bypass, loosening assertions, mocking
the component under test, swallowing failures, commenting out or deleting tests. Report
scoped pytest output; never claim "all pass" without it.

## Conventions

`from __future__ import annotations`; type hints always; `@dataclass(frozen=True)` for
config/state; stdlib → third-party → local imports; `_log = logging.getLogger(__name__)`
with `%s` formatting; comments only for a non-obvious WHY; pyflakes clean. JSON schemas in
`schemas/` are the published contract — a schema change is a versioned change (bump the
`/v<N>/` path segment in `$id` and `schema_version`), never a silent edit.

## Don'ts

- Don't add an LLM call to decide anything the state machine decides.
- Don't re-implement a target's gate check (scope, tests, lint) — call the target's RVA.
- Don't widen a target manifest's `allowed_files` or rewrite its `scope_base` to make a
  landing pass; a foreign file in range is a STOP for the owner.
- Don't create a second lane, worktree, or nested authoring agent.
- Don't write anything into a target repository except by landing a converged slice.
