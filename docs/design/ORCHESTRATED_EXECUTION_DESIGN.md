# Orchestrated Execution — design (rev 3, PROPOSED, design-only)

**Status:** PROPOSED 2026-08-31 — independent Claude review folded: machine-readable RVA
prerequisite, walking-skeleton V0, separate bookkeeping runs, local-only V0 landing,
privilege-separated verification/landing, invocation cap, and cross-lane authorship handoff.
Nothing here is built,
nothing is authorized by this document, and the collaboration contract is unchanged
until the operator amends it (§9).

**Problem.** Today the operator is the message bus of the Claude ↔ Codex loop: start a
phase, ask Codex to review the slice, paste Codex's findings to Claude, wait for the
fold, ask Codex to re-review, repeat until CLEAN, then approve. Phase 7 took five review
rounds plus three approval-repair rounds; Phase 8 ultimately required ten revisions before
Codex/operator approval. Every
one of those hops is a human relay of mechanical steps. The judgement (authoring,
red-teaming, rulings, deployment) is not what costs the operator time — the relay is.

**Goal.** Remove the relay, keep the judgement. Once the operator authorizes a lane, the
loop runs unattended to a **convergence condition** (`lens: gating` reviewer CLEAN + an
accepted machine-readable target-gate category) or a **stop condition**, and the operator is called in only at **named human
gates** that the system can identify by itself.

The concrete Python component architecture, action vocabulary, persistence, recovery, and
monitoring flow are specified in `DETERMINISTIC_PYTHON_APPLICATION_ARCHITECTURE.md`. The
finding lifecycle, cross-lane handoff, dead-loop controls, and test-integrity rules are
specified in `REVIEW_REPAIR_CONVERGENCE_PROTOCOL.md`. Multi-project discovery, configuration,
project-specific agent/skill packages, and project-local state are specified in
`PROJECT_CONTROL_PLANE.md`.

**Non-goals.** Parallel agents (this design is strictly serial — one author, one reviewer,
one orchestrator); any relaxation of single-writer, non-authoring review, exact external
authorization, or the range/manifest discipline; any DB, provider, or deployment action
by the orchestrator.

---

## 1. The central design choice: the orchestrator is NOT a third LLM

The operator asked for "a third agent that passes the outputs between these two". The
recommendation is that this third agent be a **deterministic state machine** — a Python
CLI in the same family as the Release Verification Agent — not a third model.

Why:

| A third LLM as orchestrator | A deterministic orchestrator |
|---|---|
| Adds a third opinion that can override, soften, or "summarize away" a P1 | Passes findings verbatim; cannot change their content |
| Non-reproducible: the same slice may be routed differently twice | Every transition is a pure function of (state, gate result, review verdict) |
| Tempted to declare convergence ("looks fine now") | Convergence is a predicate: `review.verdict == CLEAN and gate.verdict in allowed` |
| Its token cost scales with every artifact it reads | Reads only small structured artifacts (review JSON, gate report, manifest) |
| A prompt-injected review could steer it | Structured inputs are schema-validated; prose is never interpreted |

Everything the operator does *by hand* today between the two agents is mechanical:
choose the next step, invoke the right agent with the right inputs, verify the commit
range, run the gate, decide "again or done", record. That is a loop with a predicate,
which is a program. The two places that require judgement — **authoring** and
**adversarial review** — stay exactly where they are (Claude and Codex), and the two
places that require **authority** — rulings and deployment — stay with the operator.

The coordinator therefore has **no model access, no credentials for the database or
providers, and no ability to edit repository content**. It can create isolated worktrees,
invoke credential-free agent/verification children, read git, validate JSON, request a
local fast-forward from a separate deterministic landing process, write its ledger, and
print/persist operator-visible status events. Automatic push is a V1 graduation, not a V0
capability (§7.2).

## 2. Roles (unchanged agents, one new process)

| Role | Who | Runs as | May write |
|---|---|---|---|
| Author / implementor | Claude (`claude -p`, headless) | worktree at the phase's base, tool allowlist per lane | the slice's `allowed_files` only |
| Reviewer | Codex (`codex exec`, headless) | worktree at the exact SHA under review, **read-only sandbox** | `review.json` outside the repo — nothing in-tree |
| Coordinator | deterministic CLI in its own repository `trading-ai-assistance` (§2.1) | operator's machine; no push, DB, or provider credential intentionally available | its ledger and project-local state |
| Verify worker | deterministic child process | restricted environment; no git credentials or integration-checkout write; no network/DB unless explicitly granted | temporary verification output only |
| Land worker | separate deterministic process; never runs target/model-authored code | V0 local-only; V1 receives narrowly scoped push capability only after §7.2 graduation | exact validated local fast-forward; later exact push |
| Operator | human | — | rulings, approvals, deployments, authorizations |

The read-only sandbox for the reviewer turns "non-authoring review" from a rule into a
structural property. The tool allowlist for the author turns "design-only: no DB, no
provider traffic" from a manifest sentence into a property of the session (no MCP
servers, no network tools unless the lane grants them).

### 2.1 The orchestrator is its own repository (operator ruling 2026-08-29)

The orchestrator lives **outside `trading-ai`**, in its own repository (working name
`trading-ai-assistance`, now created at `c:\Repos\trading-ai-assistance`). It is a
**human-assistance tool**: it drives the agent CLIs,
runs the target repository's own gates, and reports to the operator. It has its own
`requirements.txt`, its own `CLAUDE.md`/`AGENTS.md`, its own tests, and its own release
discipline — none of which touch the trading system.

Why a separate repository is the right boundary:

- **No self-contamination.** Orchestrator commits never enter a `trading-ai` slice range;
  its dependencies never enter `trading-ai`'s `requirements.txt`; its tests never inflate
  the suite count the RVA reports. The thing that polices the range discipline cannot be
  inside the range.
- **No privilege inheritance.** `trading-ai`'s env file, MCP configs, and provider keys are
  not the orchestrator's; it mounts what a lane grants into the *agent* session and holds
  none of it itself (§6).
- **Reusable.** The same orchestrator can drive `trading-ai`, `trading-ai-ui`, or any
  future repository that adopts the manifest + RVA + `review.json` contract — the target
  is a parameter, not an assumption.
- **Independent evolution.** The loop can be improved, reviewed, and released on its own
  cadence without opening a `trading-ai` work item.

**Interface contract — what the orchestrator needs from a target repository, and nothing
else:**

| Provided by the target repo | Used for |
|---|---|
| `manifests/<work_item>.yaml` (existing schema: `scope_base`, `allowed_files`, `dependencies`, `focused_tests`, `operator_actions`, `expected_predeploy/postapply`, `verified_slices`) | lane definition, human-gate detection (§3.3), scope |
| `scripts/verify_release.py` (the RVA) and its versioned JSON result contract | the deterministic gate; the orchestrator never re-implements or parses a prose check |
| `CLAUDE.md` / `AGENTS.md` with the `review.json` + `fold.json` obligations (§5) | agent contracts; the orchestrator supplies the schema, the target repo binds its agents to it |
| Design-doc conventions (round record §11.n, commit-message form) | rendered bookkeeping (§5.4) |
| Git remote + branch policy (`development` integration target) | V0 local landing; later graduated push |

**What the orchestrator repository owns:** the state machine and CLI, the `review.json`
and `fold.json` JSON schemas (published; the target repo vendors or references them),
lane briefs and Human Gate Brief templates, the ledger/status formats, console monitoring, the
worktree/session drivers for `claude -p` and `codex exec`, and this design document once
the repository exists (this file then becomes a pointer plus the `trading-ai`-side
contract: the §1.2 amendment and the agent obligations).

**What stays in `trading-ai`:** manifests, the RVA, `DECISIONS.md`, design docs, the
collaboration contract, and the `review.json`/`fold.json` obligations in `AGENTS.md` and
`CLAUDE.md`. `trading-ai` never imports orchestrator code.

### 2.2 Project control plane (operator direction 2026-08-30)

The orchestrator is a multi-project control plane even though V1 executes only one lane
globally. High-level routing lives under `projects/` in this repository:

```text
projects/
  INDEX.md
  registry.yaml
  trading-ai-engine/
    INDEX.md
    project.yaml
    work-index.json
    agents/
    skills/
    policies/
    plans/
    state/                  # generated, gitignored, project-local
      current.json
      project-index.json
      project.lock
      runs/<run-id>/
  trading-ai-ui/
    ... same boundary ...
```

`project.yaml` defines the repository path, integration branch, manifest root, target gate,
and exact agent/skill/policy packages. `work-index.json` names high-level work items and
manifest references but never duplicates manifest scope. Target manifests remain in their
original repositories. The full final layout, authority table, start/resume routing, and
state invariants are bindingly specified in `PROJECT_CONTROL_PLANE.md`.

All state for a project — ledgers, projections, commands, console/status events, briefs, and
recovery evidence — stays under that project's `state/`. There is no shared top-level
runtime `runs/` store. V1 rejects a second executing worker globally while allowing
concurrent read-only monitoring commands.

## 3. The unit of work: one lane = one manifest = one state machine

A **lane** is what the operator authorizes today ("Lane A: begin Phase 8 design
authoring"). It is identified within one registered project and bound to one work-item
manifest and one `scope_base`. The orchestrator runs one lane at a time to completion.
Lane IDs remain internal audit identities; because V1 has one active lane globally, normal
`status`, `retry-now`, and `resume` commands do not require one.

### 3.1 Design-lane state machine (per phase)

```
AUTHORIZED ──author(rev 1)──▶ GATE ──PASS/DOCS_INCONCLUSIVE_SCOPE_PASS──▶ REVIEW(round n)
                                 │                                  │
                                 └─BLOCKED──▶ STOP                  ├─ CLEAN ────────────▶ CONVERGED
                                                                    ├─ P0/P1/P2 findings ─▶ FOLD(rev n+1) ──▶ GATE
                                                                    ├─ P3-only ──────────▶ CONVERGED (P3s → state artifact)
                                                                    ├─ EARLIER_PHASE_GAP ─▶ STOP (human: amendment slice)
                                                                    └─ malformed ────────▶ retry once, then STOP
CONVERGED ──▶ LOCAL LAND ──▶ COMPLETED ──▶ STOP
                                  (bookkeeping is a separately authorized run)
```

Transitions are decided only from: the schema-valid target-gate result (stable category +
per-check results + resolved range), the
schema-validated `review.json`, git facts (commit exists, range is contiguous, no
foreign commits, files ⊆ `allowed_files`), and counters (round and finding attempts).

**Convergence** = `lens: gating` reviewer verdict `CLEAN` (no P0–P2) on revision *r*
**and** the gate on `scope_base..sha(r)` reports stable category `PASS` (code slice) or
`DOCS_INCONCLUSIVE_SCOPE_PASS` (docs-only slice). No exit-code/prose combination is
upgraded by the orchestrator.

**Work bounds.** `max_rounds` per design/implementation lane is 10 and
`max_agent_invocations` is 40. Every spawned `claude` or `codex` process counts when it is
started, including a retry after a subscription pause; deterministic git/gate processes do
not. Exhaustion is a STOP, never forced acceptance. Ping-pong means a finding previously
marked `VERIFIED_RESOLVED` is later marked `REOPENED` twice; it is a STOP.

**Authorship handoff is cross-lane, never an in-place role flip.** GUIDANCE remains
non-authoring and contains no patch or replacement code. If the same finding survives the
guided repair, the Claude-owned lane stops `HANDOFF_REQUIRED` at its verified tip and does
not land independently. After explicit authorization, Claude prepares the handoff manifest
as its own manually initiated/reviewed slice rooted at that tip. The Codex-owned repair
lane's literal `scope_base` is the accepted manifest-preparation commit; its manifest also
records the stopped tip and transferred finding IDs. Owner is Codex, reviewer is Claude,
and scope is limited to the repair and its tests. The swap is one-way for the work item.
The dependent review covers its complete new range; the original Codex review and
transferred stable finding IDs remain evidence for the earlier range. Only when the
dependent lane is CLEAN, every transferred finding is `VERIFIED_RESOLVED`, all three
ranges' gates pass, and the combined final tree digest matches may the three contiguous
ranges be locally fast-forwarded as one handoff chain. Codex may issue a non-gating `lens: advisory`
verification of its repair, but the reducer rejects that lens as CLEAN evidence.

### 3.2 Implementation-lane state machine (per phase)

```
AUTHORIZED ──implement──▶ GATE(static: scope, tests, lint, migration files) ──▶ CODE_REVIEW(round n)
                                                                                 ├─ CLEAN ──▶ HUMAN_GATE?
                                                                                 └─ findings ─▶ FIX ──▶ GATE
HUMAN_GATE? ──(manifest says operator action needed)──▶ WAIT_OPERATOR ──▶ [operator: migrate/deploy/run/RVA pre+post] ──▶ APPROVED
            └─(no operator action needed)────────────▶ APPROVED (docs-only or pure-refactor slices)
```

The orchestrator **never** performs the operator's steps; it prepares the exact runbook
(already a deliverable per the contract §8) and waits.

### 3.3 How the system decides a human must step in (deterministic, from the manifest)

A human gate is **required** when any of these hold for the slice — all are machine-
checkable from the manifest and the diff, none require interpretation:

1. `operator_actions` is non-empty and contains anything other than "review/approve".
2. `expected_predeploy` or `expected_postapply` is non-empty (an RVA pre/post straddle
   exists, therefore an operator mutation exists).
3. The diff matches the project's configured migration surface (application is
   operator-only; parity rule).
4. The diff matches the project's configured deployment/operational surface.
5. The manifest declares any external grant (DB write, provider traffic, capture, incident
   write, deployment) — these are executed only under the exact operator grant, never by
   the orchestrator.
6. A reviewer finding carries `requires_ruling: true` or `earlier_phase_gap: true`.
7. Any work counter trips: `max_rounds`, `max_agent_invocations`, or unexpected base. V1 has no token budget or
   total lane wall-clock budget. A child-process hang is a recoverable process failure,
   while subscription limits and provider outages use the durable pause policy
   of §5.6 and do not consume these counters. Account/authentication failures remain a
   human gate after their bounded retries.
8. **Unknown provider / API contract (operator addition 2026-08-29).** Either agent
   reports that a provider endpoint, parameter, limit, paging rule, payload shape,
   entitlement, or coverage behaviour is **not established** by the pinned public docs,
   the provider reference/FAQ, or an audited capture — and neither agent has a solid
   answer. **Guessing is prohibited.** The author emits `UNKNOWN_CONTRACT` (endpoint,
   the exact question, what was consulted, what would settle it); the reviewer emits the
   same as a finding with `requires_ruling: true`. The orchestrator STOPs the lane and
   packages the residual question for the operator (support ticket text or an
   authorized audited probe request), exactly as CLAUDE.md's FMP/Alpaca rules require.
   The lane resumes only on an operator-supplied answer or an explicitly authorized
   probe whose capture is recorded under `ca_provider_evidence/captures/`.
9. ~~New dependency~~ — **not a gate (operator ruling 2026-08-29).** A new library is
   allowed whenever the work needs it; it is pinned in `requirements.txt`, named by the
   reviewer in `review.json`, and listed in the next Human Gate Brief so the operator sees
   it — but it never stops the loop. Whether a library is safe is decided by the
   out-of-scope security scanning (§7.3), not by the orchestrator.

A slice with none of the above (a design document; a docs-only bookkeeping slice; a
pure unit-tested refactor with no deployment surface) can proceed to the next lane
without a human in the loop — that is the whole of what "automated" means here.

### 3.4 The Human Gate Brief (what the operator receives at every gate or STOP)

Every human gate and every STOP produces one document, generated by the orchestrator
from its ledger and completed by Claude in plain language, so the operator is on the same
page as the agents without reading a transcript. Fixed shape, one page where possible:

1. **Why you are being asked** — the gate rule (§3.3 item number) or stop condition that
   fired, in one sentence, no jargon.
2. **What the lane did** — phase/slice, what was built or designed, what changed and why
   (the *logic*, not the diff), rounds run, what Codex found and how it was folded.
3. **Glossary** — every abbreviation and internal term used in the brief, spelled out
   (RVA, PRM, SM, CA, HON, `scope_base`, store numbers, D-nnn ids, …). Terms are
   introduced once, in the brief, never assumed.
4. **What is being asked of you** — the exact decision or action, with the options and
   the agents' recommendation and its reasoning.
5. **Evidence** — pointers, not prose: the reviewed range, the RVA report header, the
   `review.json` verdict, test counts, the ledger line, the runbook if deployment follows.
6. **Risks and what is NOT authorized by answering** — the exact-authorization rule:
   answering this brief grants nothing beyond what it names.

The brief is kept in the orchestrator repository beside the lane's ledger
(`projects/<project>/state/runs/<run>/lanes/<lane>/briefs/`) so it is part of the audit trail without entering the
target repo's slice range, and its path is printed by `assist status`. The target repo's
own bookkeeping slice (§5.4) cites the brief by digest.

## 4. Where the human gates sit — design vs implementation

### 4.1 Design (Phases 9–12 now; the pattern generalizes)

The long-term preference remains unattended multi-phase execution, but V1 is deliberately
**one phase per run**. This removes dynamic future-base, run-plan, and cross-phase recovery
complexity from the first implementation.

Every phase doc ends with an **open-decisions section that only the operator rules**, and
approval today folds those rulings operative in the approval slice (D-057…D-065 came
from Phases 6–7). Downstream phases consume those decisions. The modes remain useful
terminology:

- **Mode A — operator-at-end (the operator's ask).** Codex-CLEAN counts as
  *provisionally converged*; the next phase is authored against the converged text with
  every open decision consumed **at PROPOSED** (exactly how D-051 was consumed by Phase 8).
  At the end the operator reviews all converged docs and rules the accumulated decisions
  in one sitting. **Risk:** a late ruling that contradicts a proposal several phases
  upstream cascades an amendment sub-loop through every consumer (the matrix reopening
  rule). Mitigation: the orchestrator maintains a **decision inbox** (`§9`-extracted open
  decisions per phase, with the list of downstream phases that consumed each one). The
  operator may rule any item at any time; a ruling that changes a consumed proposal
  triggers an amendment lane automatically (§3.1 with the amendment as the slice), and
  the next phase does not start until it converges. Ruling early is cheap; ruling late is
  allowed but priced honestly.
- **Mode B — per-phase checkpoint.** Convergence locally lands the exact CLEAN-reviewed
  slice, prints completion, and stops. Bookkeeping and the next phase are separate runs;
  each starts only after its own manifest is prepared against the then-current exact tip
  and the operator authorizes it.

**V1 ruling (operator, 2026-08-31): Mode B, one phase per run.** Mode A, multi-phase
run plans, automatic next-manifest selection, and the decision inbox are deferred. A
future version may implement Mode A only after the one-phase loop proves reliable and the
manifest/RVA predecessor contract is explicitly approved.

### 4.2 Implementation (per phase)

Per-phase human gates are **not optional** and the orchestrator will find them by §3.3:
every CA implementation phase will carry migrations (Phase 12 inventory), deployment
surface, and RVA pre/post straddles. The unattended stretch is *implement → gate →
review → fix → CLEAN*; the operator then applies migrations (Dev, Test), deploys, runs
`--dry-run` then a real run, runs RVA pre/post, and approves. V1 then stops; a later
implementation phase is a separately started run.

Definition of Done #2 (validated on Test) is therefore always a human gate — by design,
not by omission.

### 4.3 Agent validation environment for implementation lanes (operator addition 2026-08-29)

The operator's intent: during an implementation lane both agents can **execute the
scripts themselves — `--dry-run` and real runs — against a database they fully own, with
the provider keys, review the results, and iterate until the slice is ready for Test**,
where the operator takes over deployment, testing, and validation.

**Operator ruling 2026-08-29 (supersedes the "Dev holds no data" portion of the
2026-08-12/14 rulings):** `trading_ai_v4` (Dev) becomes the agents' **shared
read/write validation database**. One database, not one per agent or per lane — the
point is that both agents execute against, inspect, and validate the *same* data, so each
can check the other's work and bugs are found and fixed before anything is published or
deployed to Test. The rule that survives unchanged: **manual execution only — no job, no
systemd unit, no timer, no scheduled task ever runs on Dev.**

- **Access.** Both agents hold full read/write on Dev, including DDL for a lane's own
  migration files, which the agents apply there first (schema-install proof) and the
  operator applies again on Test at the gate. Access is through a dedicated login
  (`trading_ai_agent`) so Dev activity is attributable to the agents and separable from
  the app role in `pg_stat_activity` and logs. Every execution is announced in the ledger
  first (command, `--asof`, dry-run or real), with the run id and row-count deltas after —
  the "announce, then execute, then evidence" discipline that already governs Test writes.
- **Provider keys are available to the agent sessions of an implementation lane** (env
  file mounted into the worktree session, never printed, never in prompts or the ledger).
  Provider traffic is **metered per lane**: the manifest declares the endpoints and the
  request/date-range budget the lane may spend, the orchestrator counts every call from
  the job's own fetch accounting, and exceeding the budget is a STOP. Paper-trading keys
  only; no live-trading credential ever enters an agent session.
- **What "ready for Test" means, machine-checked before the human gate:** the lane's
  migrations applied cleanly on Dev; every changed job ran `--dry-run` (zero writes
  proven by row counts before/after) and then one real run on Dev; re-run converged
  (idempotency); the non-authoring agent independently inspected the resulting rows and
  recorded its verdict in `review.json`; RVA `static` PASS; the runbook generated. The
  Human Gate Brief (§3.4) carries the run ids and counters.
- **Dev data is never truth.** It is accumulated by manual runs for the purpose of
  exercising code; it is never a source of record, never quoted as system state, and may
  be reset by the operator at any time. Validation claims about *real* data are still
  made only on Test (CLAUDE.md's three validation kinds otherwise stand).
- **Test stays operator-only.** Nothing in this section extends any agent's access to
  `trading_ai_v4_test` beyond the existing read-only assistant role; deployment, test
  and final validation remain the operator's.

**Consequence the ruling carries — the clone template moves.** Dev was also the
`TEMPLATE` for disposable validation databases, a role that depends on it being
pristine. Once Dev holds agent-run data it cannot serve as the template. Proposed
replacement: a new schema-only database `trading_ai_v4_template` that receives every
`V0NN` migration in lockstep (the parity role, unchanged in substance, just relocated)
and nothing else; disposable databases clone from it. Alternatively the template can be
a schema-only dump regenerated after each migration. Either way CLAUDE.md's environment
table and the "Dev = schema parity, nothing else" section must be amended in a dedicated
doc slice so the binding text matches the ruling — the design cannot leave the two in
contradiction.

## 5. Agent contracts (what makes the loop machine-driveable)

### 5.1 Reviewer output — `review.json` (schema-validated; prose is not consumed)

```json
{
  "reviewed_range": "5bfdc46..afd2a87",
  "manifest": "dil-engine/manifests/ca_platform_design_phase8.yaml",
  "review_kind": "design",                // design | code | security | bookkeeping
  "lens": "gating",                       // gating | guidance | advisory
  "verdict": "FINDINGS",                   // CLEAN | FINDINGS
  "findings": [
    {
      "id": "P8-R4-01",                    // stable across rounds when reopened
      "severity": "P1",                    // P0 | P1 | P2 | P3
      "section": "§3.2",
      "title": "…",
      "description": "…",
      "required_change": "…",
      "requires_ruling": false,
      "earlier_phase_gap": null,           // e.g. "phase7:§3.3" → STOP for amendment
      "blocks_downstream": false
    }
  ],
  "scope_observations": [],                // foreign files, undeclared changes — any entry is a STOP
  "reviewer": "codex", "model": "…", "tokens": {"input": 0, "output": 0}
}
```

Codex still writes its human-readable review; the JSON is *in addition*, and the
orchestrator relays the prose verbatim to the author alongside it. A review that fails
schema validation is retried once with the validation error, then STOPs.

### 5.2 Author input and obligations (Claude)

The orchestrator hands Claude: the lane brief (generated from the manifest + the phase's
row in the epic matrix + the approved predecessor docs' §"seam" sections — no free-form
summarization), the reviewer's prose and `review.json`, and the binding fold rules
already in force: fold every P0–P2 as a versioned revision, record the round in the
doc's review-round section (§11.n pattern), update the manifest `status` block, keep
`scope_base` unchanged, commit with the established message form
(`CA Phase N rev M: fold Codex rev-(M-1) review (a P1 + b P2) - …`), and **stop**.
Claude returns the commit SHA and a `fold.json` mapping each finding id → `FOLDED |
REJECTED_WITH_REASON`. A `REJECTED_WITH_REASON` is not resolved by the orchestrator: it
goes back to Codex in the next round verbatim; two consecutive rejections of the same id
is a STOP (human adjudication — exactly the operator's role today).

Session continuity: rounds within one phase reuse the author session (`--resume`) so
the 2,000-line doc is not re-read from scratch each round; a new phase starts a fresh
session from the brief.

### 5.3 Orchestrator obligations

- **Verify, never trust.** After every agent return: commit exists, is a descendant of
  the lane base, the range is contiguous with no foreign commits, changed files ⊆
  `allowed_files` (via the RVA, not a re-implementation), working tree clean, secret
  material is absent from structured artifacts, and bounded diagnostic persistence passes
  the built-in field/path/size safety rules. A dedicated repository secret scanner is
  deferred to the security phase.
- **Lease the integration tip.** Before every agent invocation, verify effect, and land
  effect, compare the configured local/remote integration tip with the authorized value.
  Movement STOPs before the next expensive call; audited ranges are never silently
  rebased. While a lane is active the operator keeps that branch off-limits to manual
  authoring. An advisory marker in target Git metadata is checked by target Claude/Codex
  instructions but never substitutes for the deterministic tip check.
- **Land only converged slices.** V0 performs an exact local fast-forward onto
  `development` and stops before push. V1 automatic push is disabled until the privilege
  and watched-lane graduation criteria in §7.2 pass. No merge commits or squash—the range
  discipline depends on the audited commits.
- **Ledger.** Append-only, kept in the orchestrator repository under
  `projects/<project>/state/runs/<run>/ledger.jsonl`
  (never inside the target repo's slice ranges): every invocation
  (agent, worktree, base, prompt digest), every artifact digest (`review.json`,
  `fold.json`, RVA report), every transition with its predicate inputs, every counter
  (rounds, finding attempts, every spawned agent process, active invocation timing, and subscription pauses). This is the evidence
  the operator reads at the human gate instead of a chat transcript.
- **Print and persist status events** for limit/outage detection, every scheduled or
  manual retry, recovery, completion, every STOP, and every human gate. Console output and
  `assist status`/`assist watch` are the only V1 transports. No email, SMTP, desktop
  notification, webhook, or transport configuration is required.
- **Kill switch.** A `STOP` file in the run directory, or Ctrl-C, halts before the next
  transition; nothing is landed on a halt. Resume re-reads the ledger and continues from
  the last verified state. The V1 short command may omit IDs only after discovering
  exactly one active lane and validating the complete chain; explicit recovery can still
  name the project/run/lane/event.

### 5.4 Bookkeeping is generated but runs separately

Today every approval is recorded in three places by hand — the doc header `Status:`
line, the manifest `status:` block + `verified_slices`, and the STATUS.md / handoff
rollups — and the history shows that this sync is its own defect class (the Phase 6/7
"approval-record repair" commits `3b40b5e`, `5404e84`, `17bdd82`, `9396448`, `ab7b08e`).
The orchestrator's ledger already holds every fact those records restate (ranges,
round counts, severities, SHAs, dates). After a phase locally lands, that phase run prints
completion and stops; it does **not** claim that bookkeeping is complete and does not create
a bookkeeping manifest. The operator separately authorizes Claude to prepare a bookkeeping
manifest against the landed tip, then separately starts the bookkeeping run. The
orchestrator renders immutable facts from the completed phase ledger for that run; Claude
authors the prose and commit, and Codex reviews it as today.

The honest V0 operator price for one phase is therefore four structured authorizations:
(1) prepare the phase manifest, (2) run the phase, (3) prepare the bookkeeping manifest,
and (4) run bookkeeping. This is still intended to replace the many mechanical review/fold
relay hops. Pre-authorized post-landing manifest preparation may be considered later, but
is not part of V0 because it would add a post-completion effect to the phase lane.

### 5.5 Agent selection — model and effort per role, per lane (operator request 2026-08-29)

Which model and how much reasoning effort each agent uses is **lane configuration**, not
something baked into the orchestrator. The registered project's configuration declares, per lane kind and per
role, the agent, model, and effort; a lane's manifest may override for that lane only.
Every invocation records the resolved (agent, model, effort) in the ledger, and the
reviewer's `review.json` / author's `fold.json` echo the model that actually ran, so a
review is always attributable to a specific model at a specific effort.

```yaml
lanes:
  design:
    author:   { agent: claude, model: claude-fable-5, effort: high }
    reviewer: { agent: codex,  model: gpt-5.6-sol,    effort: high }
  implementation:
    author:   { agent: claude, model: claude-fable-5, effort: high }
    reviewer: { agent: codex,  model: gpt-5.6-sol,    effort: high }
  bookkeeping:
    author:   { agent: claude, model: claude-sonnet-5, effort: medium }   # rendered tables + prose only
    reviewer: { agent: codex,  model: gpt-5.6-sol,    effort: medium }
```

How it reaches each CLI (flag presence verified locally; behavior remains subject to the
exact-child-environment V0 smoke run):

| Agent | Model | Effort |
|---|---|---|
| Claude Code `claude -p` | `--model <full model id>` | `--effort low\|medium\|high\|xhigh\|max` |
| Codex CLI 0.151.0 `codex exec` | `-m/--model` | `-c model_reasoning_effort=<value>`; also exposes `-s read-only`, `--json`, `--output-schema`, `-o`, `-p`, `--ephemeral`, and `resume` |

V1 has no token-cost budget and no total lane wall-clock budget. Both CLIs use the
operator's monthly subscriptions. The process adapter still needs a configurable
no-progress timeout/heartbeat contract so a hung child is reconciled rather than allowed
to remain forever; that is process liveness, not a lane budget and never weakens the
durable subscription-limit pause policy.

Rules: effort and model are never lowered *during* a round to make it converge — a change
takes effect only at the next lane start and is recorded; the reviewer's model/effort is
never lower than the author's for the same lane kind (a weaker reviewer is a weaker gate);
bookkeeping lanes may use a cheaper model because their content is rendered from the ledger
and the prose is short.

### 5.6 Limits and outages — a PAUSE is not a STOP (operator request 2026-08-29)

Agent CLIs hit hourly/weekly usage limits, per-minute rate limits (HTTP 429), and provider
overloads. These are **not defects in the slice**, so they must not be reported as review
failures or trip `max_rounds`; and they must not be silent. The state machine gets a
distinct state:

```
… any agent invocation ──limit/outage detected──▶ PAUSED_LIMIT ──limit clears──▶ resume the SAME step
```

Codex and Claude are authenticated through the operator's existing ChatGPT Plus and
Claude Max subscriptions. `assist doctor` verifies subscription authentication and the
child launchers fail closed against an API-key billing fallback. The orchestrator never
buys credits, enables paid overage, changes models to evade a limit, or switches to API
billing. An operator may independently upgrade a plan or buy credits and then request an
immediate retry.

**Detection (deterministic, from the CLI's own signals):**

- Claude Code: in `--output-format stream-json`, `system/api_retry` events carry an
  `error` category — `rate_limit`, `overloaded`, `billing_error`, `authentication_failed`,
  `server_error`, … — plus `attempt`, `max_retries`, `retry_delay_ms`. The orchestrator
  reads these live; a final non-zero exit whose last error category is `rate_limit` /
  `overloaded` / `billing_error` is classified `LIMIT`, `OUTAGE`, or `ACCOUNT` respectively.
  The subscription hourly/weekly usage-limit message ("limit reached … resets at …") is
  matched on the result text and classified `LIMIT` with the reset time when present —
  the exact surface is **confirmed in the V0 smoke run**, never assumed.
- Codex: exit code + stderr/`--json` event text — patterns established in the V0 smoke run
  and pinned in `projects/<project>/policies/limits.yaml` under `limit_signals`; an unrecognised failure is
  `UNKNOWN_FAILURE` → STOP, never silently retried.
- The classification is one of `HOURLY_LIMIT`, `WEEKLY_LIMIT`, `UNKNOWN_LIMIT`,
  `OUTAGE`, or `ACCOUNT`. The raw category, reset time (if any), usage-window anchor,
  attempt count, and exact persisted step are appended to the project-local ledger.
- A reset timer is used only after its exact CLI surface and timezone behavior are pinned
  by V0. Absolute times are normalized to UTC; relative times are calculated from the
  timestamp of the received event. Ambiguous text is never interpreted as a timestamp.

**Behaviour:**

1. Increment `max_agent_invocations` immediately before every spawned `claude` or `codex`
   process, including a retry after a pause, then reconcile the in-flight effect using its
   recorded pre-invocation Git/tree state and invocation ID. Nothing lands and no review
   round is counted. A partial author worktree is not blindly accepted or blindly rerun.
2. **Report immediately** on the running console and persist the event in project state. The message
   names project, work item, lane, step, agent, classification, detected time, reported
   reset, next check, and the ready-to-run `assist retry-now` command.
3. Persist `next_retry_at` before waiting. Waiting is interruptible by a typed command and
   survives process recovery through ledger replay.
4. Apply these exact schedules:

   | Classification | First automatic check | Repeated check while still blocked |
   |---|---|---|
   | `HOURLY_LIMIT` | parsed reset + 1 minute; otherwise that agent's run-start usage anchor + 5 hours 30 minutes | every 30 minutes |
   | `WEEKLY_LIMIT` | every 8 hours, or an earlier parsed reset + 1 minute | every 8 hours |
   | `UNKNOWN_LIMIT` | 30 minutes | every 30 minutes |
   | `OUTAGE` | 1 hour | every 1 hour |
   | `ACCOUNT` | one bounded confirmation retry | STOP/WAIT_OPERATOR if still invalid |

   A parsed reset time takes priority for an hourly limit. Weekly checks still occur every
   eight hours so a plan upgrade or purchased credits can be detected before the original
   weekly reset; if the reported reset is sooner, it is checked then. Recoverable limits
   and outages have no obsolete 12-hour total pause cutoff.
5. Every scheduled retry is ledgered and printed. Clearing, resuming, failed resume,
   STOP, and eventual completion are also printed and remain queryable through
   `assist status` and `assist watch`. V1 has no external delivery transport.
6. Resume **the same persisted action** and, where valid, the same Claude session. A pause
   does not increment review/finding counters, but every newly spawned agent process counts
   against the lane's 40-invocation ceiling because subscription capacity is what it protects.
7. `assist retry-now` is valid when the sole active lane is recoverably paused. It appends
   `IMMEDIATE_RETRY_REQUESTED`, interrupts the timer within the command-poll interval, and
   retries immediately. If the worker is not running, `assist resume --retry-now` first
   validates the sole project-local ledger and reconciles unfinished effects. Explicit
   project/lane/event forms remain available for diagnostics but are not normal operator
   input.
8. A `STOP` command or Ctrl-C during a pause ends the worker cleanly; the exact state
   remains resumable. Closing the process or rebooting the machine does not itself resume
   work: V1 requires `assist resume`, and the operator machine must remain awake for
   unattended waiting. A Windows service or scheduled restart is deferred.

Console output is the V1 monitoring contract: every state transition, pause, scheduled
check, manual retry, clear, resume, and final outcome prints one line. The same persisted
status event contains the identity and recovery command, so the operator never has to
discover a lane ID.

### 5.7 Per-phase artifacts and current state (operator clarification 2026-08-31)

Every V1 phase run keeps files under its project's `state/runs/<run-id>/`. JSON is used
for typed artifacts and projections; JSON Lines is used for the authoritative append-only
ledger; Markdown is used only for human briefs; bounded text is used only for diagnostics.

Persisted evidence includes `run.json`, `ledger.jsonl`, `status.json`, lane policy,
invocation requests/results, author-result, review, fold, guidance and handoff evidence,
target-gate/test results, commands/acknowledgements, finding history, completion/human-gate
briefs, SHAs, tree/artifact digests, and changed-file lists.

`ledger.jsonl` is the authoritative inner state of the phase. `status.json` is an atomic,
rebuildable projection answering the current actor/action, revision/review round, SHA,
open findings, last verified event, next action, and any pause/reset/next-check time.
Project `current.json` points to the active run/lane. `assist status` validates these
projections; `assist resume` reconstructs from the ledger rather than trusting them.

Structured evidence and briefs are retained locally without automatic expiry in V1.
Full raw CLI transcripts are never persisted. Diagnostic output is capped at 1 MiB per
invocation and stored only when needed; environment dumps, auth files, tokens, credential
values, and CLI authentication caches are never artifacts. Temporary worktrees are not
evidence and are removed after safe completion/reconciliation.

## 6. Hard boundaries the orchestrator enforces by construction

- The coordinator and every agent/verify child lack push credentials, git credential
  helpers, integration-checkout write access, and ungranted network/DB access. Agent
  subscription authentication is exposed only through the exact restricted launcher
  profile needed by that CLI; auth files and values are never loaded into evidence.
- The separate land worker accepts only validated repository identity, expected current
  tip, exact candidate SHA/tree, hooks policy, and action. It executes fixed git plumbing,
  never target code, tests, hooks, agent output, or a command supplied by an artifact.
- Reviewer sandbox read-only; author tool allowlist from the lane manifest (design lane:
  file edit + git + pytest/pyflakes; no MCP DB, no Alpaca MCP, no web).
- The orchestrator cannot approve. `APPROVED` is written only from an operator action
  (a signed approval file or CLI invocation by the operator naming the SHA), and the
  approval-record commit is still authored by Claude as today so the record shape is
  unchanged.
- Many projects may be registered, but V1 has one executing worker and one active lane
  globally. A second execution attempt fails closed; read-only monitor commands may run
  concurrently. No worktree exists for a lane that is not running.
- Project configuration, agent/skill packages, policies, plans, and all runtime state are
  isolated under `projects/<project>/`; no project runtime writes another project's tree.
- Every counter trip is a STOP. Silence is never a PASS (the RVA rule, applied to the
  loop).

## 7. Code integrity and supply-chain security (operator addition 2026-08-29)

**Operator ruling 2026-08-29: no dependency gate; scanning out of scope.** The loop must
not break on minor needs and pull the operator back in. Agents install and add whatever
libraries the work requires; whether a library is acceptable is decided by security
scanning **outside this design** — GitHub's push-time scanning (Dependabot alerts, secret
scanning, CodeQL) or a local open-source scanner the operator chooses — which flags a
problem library for a follow-up slice without ever having blocked the loop.

### 7.1 V1 safety guardrails without a dedicated scanner

The shared safety role package and every project-selected agent/skill package instruct
both agents never to inspect, emit, or persist environment dumps, authentication files,
tokens, or credentials. Prompts are not the only control: the orchestrator never reads
auth/env files into evidence, accepts only schema-defined structured fields, rejects
obvious credential-named fields, bounds diagnostic text, and never persists full raw CLI
transcripts.

| Guardrail | What it does | On failure |
|---|---|---|
| **Dependency visibility** — a new or changed entry in `requirements*.txt` (pinned version) is named by the reviewer in `review.json` and listed in the next Human Gate Brief | the operator always *sees* what was added and why; nothing is hidden, nothing is blocked | none — informational |
| **Structured persistence boundary** — schema-defined artifacts only; no env/auth-file capture; obvious credential fields rejected; diagnostic output bounded to 1 MiB per invocation; no full raw transcript | prevents the evidence store from becoming an uncontrolled CLI/session dump | malformed/forbidden structured artifact = STOP; oversized diagnostic is truncated with its digest and reason |
| **Security checklist in `review.json`** — a short fixed list the reviewer answers per code slice: parameterised SQL only (`ANY(%(ids)s)` discipline); no credentials or environment dumps in logs; least-privilege for new DB objects (owner `postgres`, DML grants to `trading_ai_app`); idempotent/atomic/dry-run-safe per the Job Robustness Contract; provider calls bounded and documented at the call site; new dependencies pinned and named; no dynamic code execution | the repo's own known failure classes are checked by the non-authoring agent every time | unfilled item = malformed review (§5.1); a failed item is an ordinary finding at the reviewer's severity |

### 7.2 "Same code" — binding review to what runs

- `review.json` names `reviewed_range` **and the tree digest** of the reviewed SHA; the
  orchestrator lands only a SHA whose tree digest equals the one reviewed CLEAN — a
  post-review commit, however small, is a new round.
- The release tag the operator deploys (`scripts/release/create_release_tag.sh`) is cut
  at the landed SHA; the Human Gate Brief states that SHA; `deploy_tag_to_test.sh`
  deploys the tag, not a branch. The reviewed SHA, the landed SHA, the tagged SHA, and
  the deployed SHA are one value, and the ledger records each step's evidence.
- Target gates, repository tests, and any other candidate-controlled code run only in the
  verify worker. That process has no push credentials/helper, no integration-checkout
  write access, and no network/DB access unless the manifest grants the exact action.
- The land worker is a different process and executes only fixed git plumbing. Every land
  command overrides `core.hooksPath` to an orchestrator-owned empty directory; preflight
  snapshots the target common Git hooks directory and STOPs if a lane created or changed a
  hook. The land worker never invokes the RVA, tests, hooks, or an artifact command.
- **V0 stops before push.** Automatic push may be enabled for V1 only after tests prove
  every agent/verify child lacks push capability in its exact launch environment, the land
  process cannot execute model-authored code, and three consecutive watched lanes locally
  land with zero scope, integration-tip, tree-digest, or hooks surprises. The graduation
  decision and evidence are ledgered and operator-approved; it is not inferred from time.

### 7.3 Out of scope — PHASE 2 (operator ruling 2026-08-29; options in `SECURITY_SCANNING_OPTIONS.md`)

Library vetting and vulnerability scanning of dependencies (GitHub Dependabot or a local
open-source scanner such as `pip-audit`), repository/diff secret scanning (`gitleaks` or
equivalent), static security lint (`bandit`), hashed requirements, egress blocking in test
runs, and a dedicated second security-lens review.
These run on push or on the operator's schedule by other tooling; a finding there opens a
normal follow-up work item. They are not part of the orchestrator and never gate the loop.
The choice between GitHub scanning, local scanners, or a hybrid is **open for phase 2** and
analysed in `docs/design/SECURITY_SCANNING_OPTIONS.md` (Codex input requested).

## 8. Prerequisites (what is missing on this machine today)

1. **Machine-readable target gate (blocking cross-repository prerequisite).** Before any
   Assist lane, `trading-ai` must deliver and independently review a versioned RVA JSON
   result containing verdict, stable category, per-check results, and resolved
   `base..sha`. It must distinguish `DOCS_INCONCLUSIVE_SCOPE_PASS` from blocking or unknown
   inconclusive results without prose parsing. This is a separately authorized
   `trading-ai` work item; the present binary exit code cannot support a design lane.
2. **Codex CLI and exact-child subscription auth.** Codex CLI 0.151.0 is installed and its
   help exposes the required model, read-only sandbox, JSON/schema output, profile,
   ephemeral, output-file, and resume surfaces. Authentication observations differ by
   environment, so `assist doctor` must run `codex login status` inside the exact child
   environment, with the same allowlist and `CODEX_HOME`, and require ChatGPT subscription
   authentication. Run an end-to-end schema/output/read-only smoke test; no API-key
   fallback is allowed.
3. **Claude headless and subscription auth.** `claude -p --output-format json` is available (2.1.163). Confirm
   `--resume`, tool allowlisting via settings, and that a worktree session cannot reach
   MCP servers the lane does not grant. Confirm it uses the operator's Claude Max login;
   the launcher removes `ANTHROPIC_API_KEY` from the child environment without deleting
   any user credential stored for unrelated work.
4. **Structured review output.** Codex's review instructions gain the v2 `review.json`
   requirement (an AGENTS.md addition — process slice, Codex-owned or Claude-owned with
   the other reviewing, per the contract's symmetric rule).
5. **Contract amendment.** COLLABORATION_CONTRACT.md is LOCKED; a §1.2 "orchestrated
   serial execution" amendment by the operator is the authority this design needs. The
   still-unapproved §1.1 parallel protocol is orthogonal and is not a dependency.
6. **Dev RW access + login (§4.3).** A `trading_ai_agent` login with full DML + DDL on
   `trading_ai_v4`, both agents' MCP/CLI configs pointed at it for implementation lanes,
   the provider env file for those sessions, and the relocated schema-only template
   database. CLAUDE.md amendment slice to match the ruling.
7. **Project control plane.** Versioned registry/project/work-index schemas, final
   `projects/` layout, project-local state/lock implementation, and migration of the
   proposed `targets/trading-ai.yaml` settings into
   `projects/trading-ai-engine/project.yaml` plus project policies.

## 9. Build plan — walking skeleton first

| Step | Deliverable | Human involvement | Proves |
|---|---|---|---|
| T0 (target repo first) | Versioned machine-readable RVA contract from §8.1 | operator authorizes its `trading-ai` manifest; Claude/Codex follow the locked target process | a docs-only result is routable without prose or a fail-open exception |
| V0-A | One project under `projects/trading-ai-engine/`; four v2 external schemas (author-result, fold, review, gate-result); pure reducer; JSONL ledger; fake drivers | ordinary manifest/review | the smallest complete loop is deterministic and replayable |
| V0-B | Claude/Codex/verify drivers; exact-child auth checks; `start/status/stop/resume/retry-now`; local-only landing process with hooks disabled | operator completes subscription login/smoke tests | agents are driveable and untrusted execution cannot reach the landing privilege |
| V0-C | One watched real design lane, then its separately authorized bookkeeping run | four structured authorizations (§5.4); operator watches and pushes | relay removal works on real work before automatic push is enabled |
| V1 | Enable automatic push only after §7.2 graduation; add implementation-lane human gates | operator approves graduation and retains deployment actions | reviewed code equals pushed code without exposing push privilege to model-authored execution |
| Future | Multi-phase chaining, decision inbox, executable challenge sandbox, mutation testing, detached worker, prompt evaluation/change control, automatic retention, and additional project activation | separately authorized later | useful extensions do not delay the trusted walking skeleton |

V0-C is the go/no-go. It records operator interventions, wall-clock duration, every agent
invocation, review rounds, pauses, and outcome against the Phase 7/8 manual baseline. If a
real phase does not reduce relay work, the tool has not met its purpose.

## 10. Open decisions for the operator

1. ~~Mode A vs Mode B~~ — **V1 RULED: Mode B, one phase per run** (operator,
   2026-08-31; §4.1). Mode A is future scope.
2. ~~Token/wall-clock budgets~~ — **RULED: none in V1**; monthly-plan limits are handled
   by PAUSED_LIMIT, while a no-progress child timeout is process recovery, not a lane
   budget. `max_rounds=10` and `max_agent_invocations=40`; every spawned Claude/Codex
   process, including a post-pause retry, counts. Exhaustion never forces acceptance.
3. ~~P2 on design~~ — **RULED: blocking and fold-now**; CLEAN requires no P0-P2.
4. ~~Landing policy~~ — **RULED (rev 3): V0 locally fast-forwards only the exact
   CLEAN-reviewed, gate-accepted tree and stops before push. Automatic push is the V1
   behavior only after the concrete §7.2 privilege-isolation and three-watched-lane
   graduation gate. Bookkeeping is a separate operator-started run with its own manifest.**
5. Who owns T0/V0-A/V0-B/V0-C/V1 as work items (default: Claude authors, Codex reviews; the orchestrator
   is code in its own repository and follows the same contract there, including §7.1).
   Residual from the §2.1 ruling: repository name/location and whether it is a private
   GitHub remote (recommended, so push-time scanning applies to it as well).
6. ~~Agent validation environment~~ — **RULED (operator, 2026-08-29): Dev is the agents'
   shared RW validation database, manual execution only, no jobs or timers ever** (§4.3;
   CLAUDE.md amended the same day). Residual: template relocation (`trading_ai_v4_template`
   vs schema-only dump).
7. ~~Security scope~~ — **RULED (operator, updated 2026-08-31): no dependency gate or
   dedicated secret scanner in V1. Shared safety prompts plus structural schema/path/size
   persistence controls stay (§7.1); dependency, repository secret, and broader scanning
   are deferred to the later security phase (§7.3).**
8. ~~Project/state layout and execution concurrency~~ — **RULED (operator, 2026-08-30):
   Project Control Plane under `projects/`; project-specific agent and skill packages;
   project-local gitignored state; many registered projects but one globally active V1
   execution worker; read-only monitors may run concurrently** (§2.2 and
   `PROJECT_CONTROL_PLANE.md`).
9. ~~Subscription-limit recovery~~ — **RULED (operator, 2026-08-30): subscription-only
   authentication; no paid API fallback; hourly 5h30 anchor then 30-minute checks; weekly
   eight-hour checks; outage hourly checks; immediate `retry-now`; console/state event on
   every detection/check/clear/resume/outcome; no external V1 notification transport and
   no 12-hour recoverable-pause cutoff** (§5.6).
10. ~~Artifact retention/redaction~~ — **RULED (operator, 2026-08-31): retain structured
    JSON/JSONL evidence and human-readable briefs locally without automatic expiry; never
    persist full raw CLI transcripts; cap diagnostic text at 1 MiB per invocation; never
    persist env dumps, auth files, tokens, or credentials.**
11. ~~Host restart~~ — **RULED (operator, 2026-08-31): no Windows service or automatic
    startup in V1; recover with `assist resume` or `assist resume --retry-now`.**
12. ~~Persistent-repair authorship~~ — **RULED (operator, 2026-08-31): GUIDANCE explains
    the failed repair and required outcome but supplies no code. If Claude still cannot
    resolve the finding, the lane cannot swap roles in place. It stops `HANDOFF_REQUIRED`;
    the operator authorizes Claude's separate handoff-manifest preparation slice rooted at
    the stopped tip, then starts the dependent lane with owner Codex, reviewer Claude, and
    literal base at the accepted preparation commit. The swap is one-way. Codex advisory
    verification uses `lens: advisory` and can never produce the reducer's CLEAN input.**
13. ~~Bookkeeping execution~~ — **RULED (operator, 2026-08-31): phase-manifest preparation,
    phase execution, bookkeeping-manifest preparation, and bookkeeping execution are four
    explicit operator authorizations in V0. Assist and Codex synthesize none of them.**
