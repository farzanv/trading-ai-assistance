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
- **Convergence is a predicate**, never a judgement: `lens: gating` reviewer verdict
  `CLEAN` (no P0–P2) on the current revision AND an accepted machine-readable target-gate
  category. Human RVA prose and `lens: advisory` can never supply convergence.
- **Fail closed.** `max_rounds=10`, `max_agent_invocations=40`, ping-pong (a previously
  `VERIFIED_RESOLVED` finding later `REOPENED` twice), two consecutive
  `REJECTED_WITH_REASON` on one finding, malformed artifacts after
  one retry, `UNKNOWN_CONTRACT`, `earlier_phase_gap`, `requires_ruling`, dirty/unexpected
  base, foreign commits in range, forbidden/oversized persisted artifact, or round/finding
  bound trip → STOP with a Human Gate
  Brief. Nothing lands on a STOP.
- **Verify, never trust.** After every agent return: commit exists and descends from the
  lane base; range contiguous with no foreign commits; changed files ⊆ `allowed_files`
  via the target's RVA (never a re-implementation); working tree clean; structured
  artifacts and optional diagnostics satisfy the persistence-safety contract.
- **Human gates are detected, not remembered** — from the target manifest and diff (design
  §3.3). The orchestrator never performs an operator action (migrations, deployment, Test
  runs, external authorization); it prepares the runbook and waits.
- **V0 landing is local-only.** No merge commits or squash. Locally fast-forward only a
  converged exact tree, then stop before push and before bookkeeping. Automatic V1 push
  requires the documented privilege-isolation proof and three surprise-free watched lanes.
- **Ledger is append-only**
  (`projects/<project>/state/runs/<run>/ledger.jsonl`), digest-chained, and records
  every spawned agent process, artifact digest, transition with predicate inputs, and counter. Resume
  names the ledger line it resumes from — never "latest".
- **Reviewer runs in a read-only sandbox; author runs under the lane's tool allowlist.**
  Non-authoring review and design-only boundaries are structural, not instructions.
- **Privilege split.** Agent and verify children have no push credential/helper,
  integration-checkout write, or ungranted network/DB access. A separate V0 land worker
  runs fixed local Git plumbing only, overrides `core.hooksPath` to an owned empty
  directory, and never executes target/model code. Agent env files
  are mounted into agent sessions per lane grant and never printed, logged, or persisted;
  neither agent's CLI config is ever read into a log (both contain connection strings).
  Every project explicitly binds the shared safety role/skill package. V1 has no dedicated
  scanner: only schema-defined structured artifacts and optional 1-MiB diagnostic text
  persist; full raw transcripts, environment dumps, auth files, tokens, and credentials do not.
- **Project Control Plane.** Every managed project has its own `INDEX.md`, configuration,
  work index, agent/skill packages, policies, plans, and gitignored state under
  `projects/<project>/`. No project runtime file is stored globally or in another project.
- **One executing lane globally in V1.** Many projects may be registered; a second
  execution worker is refused while read-only monitoring commands may run concurrently.
  No worktree exists for a lane that is not running.
- **Durable subscription pauses.** Hourly/weekly limits and outages pause the same step,
  never consume a review round, follow the governed retry schedule, print/persist every
  material status event, and accept `assist retry-now`. Every newly spawned CLI process,
  including a retry, counts against 40. No API-key billing fallback or obsolete 12-hour
  pause cutoff is allowed.
- **One phase per V1 run.** A selected manifest must already exist. Assist/Codex never
  synthesize it; Claude may prepare the next manifest only as a separately authorized and
  reviewed slice after the predecessor lands. Multi-phase plans are future scope.
- **Bookkeeping is a separate operator-started run.** Its manifest is prepared against the
  landed phase tip. Facts are rendered from the phase ledger; prose stays agent-authored.
  A phase run must not claim bookkeeping completion.
- **GUIDANCE contains no code.** If the guided repair still fails, stop
  `HANDOFF_REQUIRED`. Claude first prepares the handoff manifest in a separate reviewed
  slice rooted at the stopped tip; the dependent lane uses that preparation commit as its
  literal base, owner Codex, reviewer Claude, and a one-way swap. Codex self-review
  is `lens: advisory` and structurally cannot gate landing.

## Environment

Python 3.10+ (3.14 locally). No checked-in venv:

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1; pip install -r requirements.txt -r requirements-dev.txt
```

External CLIs the orchestrator drives (must be on PATH; never vendored):
- `claude` (Claude Code) — headless via `claude -p … --output-format json`, `--resume`.
- `codex` (Codex CLI, `@openai/codex`) — headless via `codex exec`, read-only sandbox for
  review. Version 0.151.0 is installed; subscription auth must still be proved inside the
  exact driver environment/`CODEX_HOME`, not inferred from the interactive shell.

Target repositories are registered through `projects/registry.yaml` and configured in
`projects/<project>/project.yaml` (path, integration branch, manifest root, gate command,
agent/skill/policy references). Runtime state stays under that project. No target path is
hard-coded in source. The present `targets/trading-ai.yaml` is a legacy proposed scaffold
pending the V0-A Project Control Plane migration and must not become a second authority.

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
- Don't swap author/reviewer roles inside a lane or let an advisory review produce CLEAN.
- Don't execute reviewer-supplied code/commands or mutation tests in V0.
- Don't place a project's state, command, console/status event, or artifact outside that
  project's `state/` root; don't treat a generated projection as ledger authority.
- Don't infer a work item by scanning a manifest directory and don't create a missing
  manifest. Start from an explicitly selected work item or authorized run plan.
- Don't write anything into a target repository except by landing a converged slice.
