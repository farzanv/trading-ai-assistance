# Handoff — 2026-08-29: the orchestrator project, from zero

**Rev-3 refresh 2026-08-31:** the independent Claude review and operator confirmations
supersede earlier landing/bookkeeping/CLI assumptions in this kickoff. This file now
reflects local-only V0 landing, separate bookkeeping, and cross-lane handoff.

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
review rounds plus three rounds repairing the approval record; Phase 8 ultimately required
ten revisions and is now Codex/operator approved, with bookkeeping directed separately.
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
4. **V0 runs one design phase at a time (Mode B).** After a phase converges, Assist locally
   lands the exact reviewed tree, prints completion, and stops before push and bookkeeping.
   Bookkeeping has its own prepared manifest and operator-started run.
   Claude prepares the next phase manifest separately after explicit authorization and
   against the new exact tip. Multi-phase Mode A and the decision inbox are future scope.
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
   V1 has no dedicated scanner. Shared agent/skill safety rules plus structural
   schema/path/type/size persistence controls remain, as does the reviewer checklist.
8. **Unknown provider/API contracts stop the loop.** If neither agent can answer a
   provider question from pinned docs or audited captures, guessing is prohibited; the
   orchestrator packages the residual question for the operator.
9. **Project Control Plane in this repository.** `projects/` is the high-level project
   manager: registry, per-project orientation/config/work index, project-specific agents,
   skills, policies and plans. Target technical authority and manifests stay in the target.
10. **Project-local state and one V1 worker.** Every ledger, projection, command,
    console/status event and recovery artifact lives below its project's gitignored `state/`.
    Multiple projects may be configured, but V1 executes one lane globally; monitors may
    run concurrently.
11. **Subscription-limit recovery is automatic.** ChatGPT Plus and Claude Max auth only;
    hourly limits use reported reset or 5h30 then 30-minute checks, weekly limits are
    checked every eight hours, outages hourly, and `assist retry-now` forces an immediate
    attempt. Every detection/check/clear/resume/outcome is printed and persisted for
    `assist status`/`assist watch`. There is no
    12-hour recoverable-pause cutoff or automatic API billing fallback.
12. **Machine-readable RVA first.** `trading-ai` must publish a versioned JSON gate result
    before any live Assist lane; the present binary exit code cannot route docs-only work.
13. **Privilege split.** Target tests/RVA run in a credential-free verify process. A
    separate hooks-disabled land process runs fixed Git plumbing only. Automatic push is
    V1 behavior after proof plus three surprise-free watched lanes.
14. **Bounded model consumption.** Ten review rounds and 40 spawned agent processes;
    retries after pauses count as invocations.
15. **Stuck repair handoff.** GUIDANCE contains no code. Persistent failure stops and may
    open a separately authorized, one-way Codex-author/Claude-reviewer dependent lane.

## 3. How one lane works — a walkthrough (design Phase 9 under the loop)

1. **Operator authorizes** `assist start --project trading-ai-engine --work-item
   ca_platform_design_phase9` (the authorization text is still recorded exactly). This
   command is illustrative until the Phase 9 manifest and T0 RVA JSON contract exist. The
   Project Control Plane resolves the repository, manifest root, project-specific
   agent/skill packages and policies, then opens a lane bound to
   `dil-engine/manifests/ca_platform_design_phase9.yaml` at the clean `development` tip.
2. **Author, rev 1.** The orchestrator creates a git worktree at that tip and runs Claude
   headlessly (`claude -p`) with a *lane brief* generated from the manifest, the phase's
   matrix row, and the approved predecessors' seam sections — plus a tool allowlist (file
   edit, git, pytest, pyflakes; no DB, no provider, no web). The manifest must already
   exist; Claude authors the doc and updates that authorized manifest, commits
   `CA Phase 9 design rev 1: …`, and returns the SHA.
3. **Verify.** The credential-free verify process checks: the commit exists and descends from the base; the
   range has no foreign commits; changed files are within `allowed_files` — by running
   `trading-ai`'s own `scripts/verify_release.py` (the RVA), never its own copy of the
   rule; structured artifacts satisfy the persistence-safety contract. Before this
   walkthrough is executable, the RVA must return a machine-readable
   `DOCS_INCONCLUSIVE_SCOPE_PASS` category; prose is never parsed.
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
   predicate** — or a counter trips (`max_rounds=10`, `max_agent_invocations=40`;
   a previously resolved finding reopened twice = ping-pong). Any trip is a STOP, never a forced
   acceptance.
7. **Land.** V0 locally fast-forwards the converged revision onto `development` and stops
   before push, only if its tree digest equals the one reviewed CLEAN. The operator pushes
   during the watched V0 period.
8. **Bookkeeping and next phase.** The phase run is complete, but bookkeeping is not.
   The operator authorizes Claude to prepare a bookkeeping manifest against the landed tip
   and separately starts that run. When Phase 10 is authorized, Claude prepares its manifest as
   a separate reviewed slice whose literal `scope_base` equals the landed Phase 9 tip.
   Assist and Codex never synthesize it. Then the operator starts a new Phase 10 run.
9. **Limits and outages.** A five-hour/weekly subscription limit or service outage enters
   `PAUSED_LIMIT`, persists the same step, prints/persists status immediately, and checks on the ruled
   schedule. `assist retry-now` wakes the current lane after a plan upgrade or credit
   purchase. No lane ID is needed because V1 has one active lane.
10. **Human Gate Brief** — every STOP or gate produces one page: why you are being asked
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

**Decided (see §2):** deterministic orchestrator; own repo; serial; one-phase Mode B V0;
implementation human gates; Dev RW for agents; no dependency gate; security = phase 2;
unknown contracts STOP; Project Control Plane; project-local state; project-specific
agents/skills; one V1 execution worker; subscription-only auth; durable limit/outage
recovery, console/status monitoring, and immediate retry; no token/total-lane budget;
P2 blocks; exact-tree local V0 landing; separate bookkeeping; no dedicated V1 scanner; structured artifacts
retained locally without raw transcripts; manual Windows resume.

**Open (design §10):** who owns build steps T0/V0-A/V0-B/V0-C/V1 and the template-database relocation on
the `trading-ai` side. Scanner selection remains a later security-phase decision, not a
V1 prerequisite.

**Not yet done (prerequisites, design §8):** `trading-ai` RVA JSON result; exact-child
Codex/Claude subscription and schema/sandbox/limit smoke runs; v2 artifact schemas;
the structured obligations added to `trading-ai`'s `AGENTS.md`/`CLAUDE.md`;
the operator's §1.2 amendment to the collaboration contract; the `trading_ai_agent` login.

## 6. Build plan (each step its own manifest + review)

| Step | Deliverable | Proves |
|---|---|---|
| T0 | `trading-ai` versioned RVA JSON result | docs-only and blocking outcomes are deterministic |
| V0-A | reduced reducer, four v2 schemas, one project, ledger, fake loop | the walking skeleton is deterministic |
| V0-B | real drivers, verify/land split, local landing, recovery/limits/console | untrusted execution cannot use landing privilege |
| V0-C | watched real phase + separate bookkeeping run | relay reduction — **the go/no-go** |
| V1 | automatic push after graduation; implementation human gates | safe unattended landing |

## 7. Where things are

| Artifact | Location |
|---|---|
| Design authority (rev 3, PROPOSED) | `docs/design/ORCHESTRATED_EXECUTION_DESIGN.md` |
| Security scanning options (phase 2, open) | `docs/design/SECURITY_SCANNING_OPTIONS.md` |
| Agent contract schemas (v1, DRAFT) | `schemas/review.schema.json`, `schemas/fold.schema.json` |
| Project Control Plane design and final layout | `docs/design/PROJECT_CONTROL_PLANE.md` |
| Legacy first-target proposal, pending V0-A migration | `targets/trading-ai.yaml` |
| Planned Engine project config | `projects/trading-ai-engine/project.yaml` |
| Binding invariants / Codex notes | `CLAUDE.md`, `AGENTS.md` |
| Work item for the scaffold | `manifests/repo_scaffold.yaml` |
| `trading-ai` side (parked) | branch `process/orchestrator-pointer-dev-ruling`: CLAUDE.md Dev-RW amendment, design pointer, manifest `orchestrator_pointer_dev_rw_ruling.yaml` |
| `trading-ai` state checked 2026-08-31 | `development` at `fb5f874`; Phase 8 closed/bookkept; Phase 9 handoff exists but `ca_platform_design_phase9.yaml` does not |

## 8. Glossary

- **Lane** — one authorized unit of work bound to one manifest (e.g. "Phase 9 design").
- **Manifest** — the per-work-item YAML in `trading-ai/dil-engine/manifests/` (`scope_base`,
  `allowed_files`, `dependencies`, `operator_actions`, `verified_slices`, …).
- **RVA** — Release Verification Agent, `trading-ai`'s deterministic gate
  (`scripts/verify_release.py`): scope check, dependency ancestry, tests, lint. T0 adds the
  required versioned JSON category/check/range result; the present exit code alone is not
  routable for docs-only work.
- **Slice / range** — the commits of one work item, `scope_base..sha`, which must be
  contiguous with no foreign commits so the RVA can judge exactly that diff.
- **Fold** — incorporating a review round's findings into the next revision.
- **Convergence** — `lens: gating` reviewer verdict CLEAN (no P0–P2) *and* machine gate
  category PASS or `DOCS_INCONCLUSIVE_SCOPE_PASS` on the exact range.
- **STOP** — the loop halts and writes a Human Gate Brief; nothing lands.
- **PAUSED_LIMIT** — an agent hit a subscription/rate limit or the provider is down; the
  loop prints/persists status, durably waits/checks on the ruled schedule, and resumes the *same* step.
  It is not a round or failure and has no 12-hour recoverable-pause cutoff.
- **Model / effort per role** — which model and reasoning effort each agent uses is set
  per lane kind in `projects/<project>/` agent/policy configuration (design §5.5), recorded in the ledger on every run.
- **Human gate** — a point where only the operator can act (ruling, migration, deployment,
  Test validation, external authorization), detected from the manifest and diff.
- **Mode A / Mode B** — future unattended chaining with a decision inbox / V0 one-phase
  execution that locally lands and stops; bookkeeping is a separate run.
- **Decision inbox** — the accumulated open decisions (each phase's §9) awaiting operator
  rulings, with the downstream phases that consumed each at PROPOSED.
- **Project Control Plane** — the AI-assist `projects/` tree that routes a project to its
  repository, manifests, agents, skills, policies, plans, and project-local state.
- **Ledger** — the orchestrator's append-only, digest-chained record of every invocation,
  artifact, transition and counter
  (`projects/<project>/state/runs/<run>/ledger.jsonl`).
- **P0–P3** — severity gate from the collaboration contract: P0/P1 must fix; P2 fix when
  it touches irreversible writes, auditability or operational correctness; P3 backlog.
- **CA** — corporate actions; the current 12-phase design epic on `trading-ai`.
- **Dev / Test** — `trading_ai_v4` (agents' RW validation DB, manual runs only) /
  `trading_ai_v4_test` (the operator's working system; agents read-only).
