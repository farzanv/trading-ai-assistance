# Handoff — 2026-08-29: the orchestrator project, from zero

**Audience:** Codex (first), and any future Claude thread. Read this *before* the design
document — it explains what we are trying to do and why, in plain language, with one worked
example. The design (`design/ORCHESTRATED_EXECUTION_DESIGN.md`) is the contract; this is the
story. A glossary is at the end.

## 1. The problem we are solving (one paragraph)

On `trading-ai`, every design phase and every code slice goes through the same loop: Claude
authors a revision → the operator asks Codex to review it → the operator pastes Codex's
findings to Claude → Claude folds them into the next revision and commits → the operator
asks Codex to review again → … until Codex reports no P0–P2 findings → the operator
approves and rules the open decisions → Claude records the approval. Phase 7 took five
review rounds plus three rounds repairing the approval record; Phase 8 is at rev 9 today.
The *judgement* in that loop (authoring, red-teaming, rulings) is well spent. The *relay* —
the operator hand-carrying outputs between two agents and retyping SHAs into three
bookkeeping places — is not, and it is where a lot of the repair rounds come from.

**Goal:** remove the relay, keep the judgement. Once the operator authorizes a lane, the
loop runs by itself to a convergence condition, and the operator is called in only at
named human gates the system identifies on its own.

## 2. What the operator decided today (rulings — not up for re-litigation)

1. **The orchestrator is a deterministic program, not a third LLM.** A third model would
   add a third opinion that can soften a P1, declare "looks fine" on its own judgement,
   and be steered by whatever text passes through it. A state machine passes findings
   verbatim and decides transitions only from structured inputs.
2. **It lives in its own repository** — this one — as a *human-assistance tool*. It never
   contaminates a `trading-ai` slice range with its own commits, dependencies or tests,
   and it holds none of `trading-ai`'s credentials.
3. **Strictly serial.** One author, one reviewer, one orchestrator, one lane at a time. No
   parallel agents in this version (the parallel-execution idea was reviewed and set aside).
4. **Design phases run unattended ("Mode A").** After a phase converges, the next phase
   starts immediately; the operator's rulings on open decisions accumulate in a *decision
   inbox* and can be made any time. Tripwire: if the reviewer marks an open decision as
   load-bearing for the next phase's seam (`blocks_downstream: true`), the loop pauses for
   that ruling.
5. **Implementation phases always have a human gate**: the agents implement, review, and
   exercise the code themselves; the operator deploys to Test and validates, per phase.
6. **Dev (`trading_ai_v4`) becomes the agents' shared read/write validation database.**
   Both agents can run `--dry-run` and real executions there, inspect the same data, and
   validate each other's work before anything goes to Test. Manual execution only — no job
   or timer ever runs on Dev; Dev data is never treated as truth. This reverses the
   2026-08-12/14 "Dev holds no data" rulings; `trading-ai`'s CLAUDE.md amendment is parked
   on branch `process/orchestrator-pointer-dev-ruling` awaiting review.
7. **No dependency gate.** Agents add whatever libraries the work needs (pinned, named in
   the review and the operator brief). A human gate on every new library would break the
   loop for minor issues. Library vetting and all security scanning are **phase 2**, done
   outside the loop — approach undecided, options in `design/SECURITY_SCANNING_OPTIONS.md`.
   Two guardrails stay inside the loop: a secret scan (hard STOP) and a short security
   checklist the reviewer answers.
8. **Unknown provider/API contracts stop the loop.** If neither agent can answer a
   provider question from pinned docs or audited captures, guessing is prohibited; the
   orchestrator packages the residual question for the operator.

## 3. How one lane works — a walkthrough (design Phase 9 under the loop)

1. **Operator authorizes** "Phase 9 design authoring" (exactly as today: a sentence, quoted
   into the manifest header). The orchestrator opens a lane bound to
   `dil-engine/manifests/ca_platform_design_phase9.yaml` at the clean `development` tip.
2. **Author, rev 1.** The orchestrator creates a git worktree at that tip and runs Claude
   headlessly (`claude -p`) with a *lane brief* generated from the manifest, the phase's
   matrix row, and the approved predecessors' seam sections — plus a tool allowlist (file
   edit, git, pytest, pyflakes; no DB, no provider, no web). Claude authors the doc and
   manifest, commits `CA Phase 9 design rev 1: …`, and returns the SHA.
3. **Verify.** The orchestrator checks: the commit exists and descends from the base; the
   range has no foreign commits; changed files are within `allowed_files` — by running
   `trading-ai`'s own `scripts/verify_release.py` (the RVA), never its own copy of the
   rule; secret scan clean. A docs-only slice makes the RVA say INCONCLUSIVE-with-scope-
   PASS; that is the expected outcome for design and is never upgraded to PASS.
4. **Review, round 1.** The orchestrator runs Codex headlessly (`codex exec`) in a fresh
   worktree at that exact SHA, **read-only sandbox**, with the lane's review brief. Codex
   writes its prose review as today *and* a `review.json` (`schemas/review.schema.json`):
   verdict, findings with stable ids and severities, flags (`requires_ruling`,
   `earlier_phase_gap`, `blocks_downstream`, `unknown_contract`), scope observations, open
   decisions for the inbox. The orchestrator validates the JSON against the schema; a
   malformed file is retried once, then the lane STOPs.
5. **Fold.** Findings (prose + JSON) go to Claude verbatim — the same session, resumed, so
   the 2,000-line doc is not re-read from scratch. Claude folds every P0–P2 as rev 2,
   records the round in the doc's §11.n and the manifest `status`, commits `CA Phase 9 rev
   2: fold Codex rev-1 review (a P1 + b P2) - …`, and returns `fold.json`: each finding id
   → `FOLDED` or `REJECTED_WITH_REASON`. A rejection is not settled by the orchestrator —
   it goes back to Codex verbatim next round; two consecutive rejections of one id is a
   STOP for the operator to adjudicate (exactly the operator's role today).
6. **Repeat** 3 → 4 → 5 until Codex's verdict is `CLEAN` (no P0–P2) — the **convergence
   predicate** — or a counter trips (`max_rounds`, proposed 5; token or wall-clock budget;
   a finding reopened after two folds = ping-pong). Any trip is a STOP, never a forced
   acceptance.
7. **Land.** The converged revision is fast-forwarded onto `development` and pushed (no
   merge commits — the range discipline depends on linear history), only if its tree
   digest equals the one Codex reviewed CLEAN. Bookkeeping SHAs/ranges/`verified_slices`
   are rendered from the orchestrator's ledger rather than retyped.
8. **Next phase.** Under Mode A, Phase 10 opens immediately on the converged text with
   Phase 9's open decisions consumed at PROPOSED, and those decisions appear in the
   operator's decision inbox. If Codex flagged one as `blocks_downstream`, the loop pauses
   here instead and sends the operator a **Human Gate Brief**.
9. **Human Gate Brief** — every STOP or gate produces one page: why you are being asked
   (the rule that fired, in one sentence), what the lane did (the logic, not the diff), a
   glossary of every abbreviation used, the exact ask with options and a recommendation,
   evidence pointers (range, RVA header, verdict, ledger line), and what answering does
   *not* authorize.

For an **implementation** lane the loop is the same through step 6, with two differences:
the author tool allowlist includes the Dev database and the provider env file (ruling 6),
and "ready" additionally requires the migrations applied on Dev, `--dry-run` proven
zero-write, one real run, a re-run that converges, and the non-authoring agent's
inspection of the resulting rows — after which the operator gate (deploy to Test, dry-run,
real run, RVA pre/post, approve) is mandatory and detected from the manifest and diff
(design §3.3), not remembered.

## 4. What changes for Codex

**Today (until the loop is live):** nothing in how reviews are done. Codex is asked to
review this repository like any `trading-ai` slice: findings-first, P0–P3, bounded rounds.
The specific asks are in `STATUS.md` → "Review requests outstanding".

**Under the loop:** Codex is invoked headlessly, in a read-only sandbox, and must produce
`review.json` in addition to its prose. Non-authoring review becomes a property of the
sandbox rather than a rule. The prose still matters — it is relayed to Claude verbatim —
but only the JSON drives the state machine, so a finding that is not in the JSON does not
exist to the loop. The security checklist block is required on code slices. Stable finding
ids across rounds are what let the orchestrator detect ping-pong.

Codex is also the **first reviewer of the orchestrator itself**: the schemas, the target
config, and later the state machine code, judged for determinism, fail-closed behaviour,
ledger completeness and privilege minimalism (see `AGENTS.md` here).

## 5. What is decided, what is open

**Decided (see §2):** deterministic orchestrator; own repo; serial; Mode A + tripwire;
implementation human gates; Dev RW for agents; no dependency gate; security = phase 2;
unknown contracts STOP.

**Open (design §10):** `max_rounds` and budgets; whether P2 findings on *design* docs are
fold-now (current practice) or backlog (the contract's letter); land-on-convergence vs
land-on-approval; who owns build steps M0–M3; the template-database relocation on the
`trading-ai` side (Dev can no longer be the clone template); the phase-2 scanning approach.

**Not yet done (prerequisites, design §8):** Codex CLI (`codex exec`) is not installed —
only the desktop app; `claude -p` smoke run with `--resume` and a restricted allowlist;
the `review.json`/`fold.json` obligations added to `trading-ai`'s `AGENTS.md`/`CLAUDE.md`;
the operator's §1.2 amendment to the collaboration contract; the `trading_ai_agent` login.

## 6. Build plan (each step its own manifest + review)

| Step | Deliverable | Proves |
|---|---|---|
| M0 | this repo (done), CLI install, two headless smoke runs on a throwaway branch, schemas reviewed, `trading-ai` obligations slice | both agents are driveable and sandboxed from outside the target |
| M1 | single-phase design loop, Mode B (operator approves at the end), run on **Phase 9** while the operator watches | the loop converges on a real phase without a relay — **the go/no-go** |
| M2 | multi-phase chaining, decision inbox, Mode A + tripwire; Phases 10–12 | unattended design completion |
| M3 | implementation-lane mode with gate detection, briefs, Dev execution discipline | human gates found by the system |

## 7. Where things are

| Artifact | Location |
|---|---|
| Design authority (rev 1, PROPOSED) | `docs/design/ORCHESTRATED_EXECUTION_DESIGN.md` |
| Security scanning options (phase 2, open) | `docs/design/SECURITY_SCANNING_OPTIONS.md` |
| Agent contract schemas (v1, DRAFT) | `schemas/review.schema.json`, `schemas/fold.schema.json` |
| First target config (PROPOSED) | `targets/trading-ai.yaml` |
| Binding invariants / Codex notes | `CLAUDE.md`, `AGENTS.md` |
| Work item for the scaffold | `manifests/repo_scaffold.yaml` |
| `trading-ai` side (parked) | branch `process/orchestrator-pointer-dev-ruling`: CLAUDE.md Dev-RW amendment, design pointer, manifest `orchestrator_pointer_dev_rw_ruling.yaml` |
| `trading-ai` state at handoff | `development` at `d2a3881` (CA Phase 8 rev 9, mid-slice — do not interleave) |

## 8. Glossary

- **Lane** — one authorized unit of work bound to one manifest (e.g. "Phase 9 design").
- **Manifest** — the per-work-item YAML in `trading-ai/dil-engine/manifests/` (`scope_base`,
  `allowed_files`, `dependencies`, `operator_actions`, `verified_slices`, …).
- **RVA** — Release Verification Agent, `trading-ai`'s deterministic gate
  (`scripts/verify_release.py`): scope check, dependency ancestry, tests, lint; PASS /
  BLOCKED / INCONCLUSIVE, fail-closed.
- **Slice / range** — the commits of one work item, `scope_base..sha`, which must be
  contiguous with no foreign commits so the RVA can judge exactly that diff.
- **Fold** — incorporating a review round's findings into the next revision.
- **Convergence** — reviewer verdict CLEAN (no P0–P2) *and* gate PASS (or docs-only
  INCONCLUSIVE with scope PASS).
- **STOP** — the loop halts and writes a Human Gate Brief; nothing lands.
- **Human gate** — a point where only the operator can act (ruling, migration, deployment,
  Test validation, external authorization), detected from the manifest and diff.
- **Mode A / Mode B** — unattended chaining of design phases with a decision inbox / a
  per-phase operator checkpoint.
- **Decision inbox** — the accumulated open decisions (each phase's §9) awaiting operator
  rulings, with the downstream phases that consumed each at PROPOSED.
- **Ledger** — the orchestrator's append-only, digest-chained record of every invocation,
  artifact, transition and counter (`runs/<target>/<lane>.jsonl`).
- **P0–P3** — severity gate from the collaboration contract: P0/P1 must fix; P2 fix when
  it touches irreversible writes, auditability or operational correctness; P3 backlog.
- **CA** — corporate actions; the current 12-phase design epic on `trading-ai`.
- **Dev / Test** — `trading_ai_v4` (agents' RW validation DB, manual runs only) /
  `trading_ai_v4_test` (the operator's working system; agents read-only).
