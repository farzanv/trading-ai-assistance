# Orchestrated Execution — design (rev 1, PROPOSED, design-only)

**Status:** PROPOSED 2026-08-29 — for operator + Codex review. Nothing here is built,
nothing is authorized by this document, and the collaboration contract is unchanged
until the operator amends it (§9).

**Problem.** Today the operator is the message bus of the Claude ↔ Codex loop: start a
phase, ask Codex to review the slice, paste Codex's findings to Claude, wait for the
fold, ask Codex to re-review, repeat until CLEAN, then approve. Phase 7 took five review
rounds plus three approval-repair rounds; Phase 8 is at rev 4 after three rounds. Every
one of those hops is a human relay of mechanical steps. The judgement (authoring,
red-teaming, rulings, deployment) is not what costs the operator time — the relay is.

**Goal.** Remove the relay, keep the judgement. Once the operator authorizes a lane, the
loop runs unattended to a **convergence condition** (reviewer CLEAN + deterministic gate
PASS) or a **stop condition**, and the operator is called in only at **named human
gates** that the system can identify by itself.

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

The orchestrator therefore has **no model access, no credentials for the database or
providers, and no ability to edit repository content**. It can: create worktrees, invoke
the two agent CLIs, run the RVA, read git, validate JSON, fast-forward `development`,
push, write its own ledger, and send the operator a notification.

## 2. Roles (unchanged agents, one new process)

| Role | Who | Runs as | May write |
|---|---|---|---|
| Author / implementor | Claude (`claude -p`, headless) | worktree at the phase's base, tool allowlist per lane | the slice's `allowed_files` only |
| Reviewer | Codex (`codex exec`, headless) | worktree at the exact SHA under review, **read-only sandbox** | `review.json` outside the repo — nothing in-tree |
| Orchestrator | deterministic CLI in its own repository `trading-ai-orchestrator` (§2.1) | operator's machine, no secrets loaded | its ledger; target repo `development` fast-forward + push |
| Operator | human | — | rulings, approvals, deployments, authorizations |

The read-only sandbox for the reviewer turns "non-authoring review" from a rule into a
structural property. The tool allowlist for the author turns "design-only: no DB, no
provider traffic" from a manifest sentence into a property of the session (no MCP
servers, no network tools unless the lane grants them).

### 2.1 The orchestrator is its own repository (operator ruling 2026-08-29)

The orchestrator lives **outside `trading-ai`**, in its own repository (working name
`trading-ai-orchestrator`, location the operator's choice, e.g. `c:\Repos\
trading-ai-orchestrator`). It is a **human-assistance tool**: it drives the agent CLIs,
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
| `scripts/verify_release.py` (the RVA) and its exit-code contract | the deterministic gate; the orchestrator never re-implements a check |
| `CLAUDE.md` / `AGENTS.md` with the `review.json` + `fold.json` obligations (§5) | agent contracts; the orchestrator supplies the schema, the target repo binds its agents to it |
| Design-doc conventions (round record §11.n, commit-message form) | rendered bookkeeping (§5.4) |
| Git remote + branch policy (`development` integration target) | landing + push |

**What the orchestrator repository owns:** the state machine and CLI, the `review.json`
and `fold.json` JSON schemas (published; the target repo vendors or references them),
lane briefs and Human Gate Brief templates, the ledger format, notification, the
worktree/session drivers for `claude -p` and `codex exec`, and this design document once
the repository exists (this file then becomes a pointer plus the `trading-ai`-side
contract: the §1.2 amendment and the agent obligations).

**What stays in `trading-ai`:** manifests, the RVA, `DECISIONS.md`, design docs, the
collaboration contract, and the `review.json`/`fold.json` obligations in `AGENTS.md` and
`CLAUDE.md`. `trading-ai` never imports orchestrator code.

## 3. The unit of work: one lane = one manifest = one state machine

A **lane** is what the operator authorizes today ("Lane A: begin Phase 8 design
authoring"). It is bound to one work-item manifest and one `scope_base`. The orchestrator
runs one lane at a time to completion.

### 3.1 Design-lane state machine (per phase)

```
AUTHORIZED ──author(rev 1)──▶ GATE ──PASS/DOCS-INCONCLUSIVE──▶ REVIEW(round n)
                                 │                                  │
                                 └─BLOCKED──▶ STOP                  ├─ CLEAN ────────────▶ CONVERGED
                                                                    ├─ P0/P1/P2 findings ─▶ FOLD(rev n+1) ──▶ GATE
                                                                    ├─ P3-only ──────────▶ CONVERGED (P3s → backlog file)
                                                                    ├─ EARLIER_PHASE_GAP ─▶ STOP (human: amendment slice)
                                                                    └─ malformed ────────▶ retry once, then STOP
CONVERGED ──▶ next phase AUTHORIZED (mode A) │ or OPERATOR_GATE (mode B)   — see §4
```

Transitions are decided only from: the RVA report (verdict + scope check), the
schema-validated `review.json`, git facts (commit exists, range is contiguous, no
foreign commits, files ⊆ `allowed_files`), and counters (round, tokens, wall-clock).

**Convergence** = reviewer verdict `CLEAN` (no P0–P2) on revision *r* **and** the gate on
`scope_base..sha(r)` is PASS (code slice) or INCONCLUSIVE-with-scope-PASS (docs-only slice,
which the RVA reports by design — never upgraded to PASS by the orchestrator).

**Round bound.** `max_rounds` per phase (proposed 5, from observed Phase 7/8 history).
Exhaustion is a STOP, never a forced acceptance. Ping-pong (a finding reopened after
being folded twice) is detected by finding-id recurrence and is a STOP.

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
3. The diff adds a file under `docs/operations/sql/migrations/` (Dev + Test application
   is operator-only; parity rule).
4. The diff touches `scripts/run_*.sh`, `install_env_runtime.sh`, or `ops/` (deployment
   surface; `chmod` on the Linux box is operator-only).
5. The manifest declares any external grant (DB write, provider traffic, capture, incident
   write, deployment) — these are executed only under the exact operator grant, never by
   the orchestrator.
6. A reviewer finding carries `requires_ruling: true` or `earlier_phase_gap: true`.
7. Any counter trips: `max_rounds`, token budget, wall-clock budget, unexpected base.
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
(`runs/<target>/<lane>/briefs/`) so it is part of the audit trail without entering the
target repo's slice range, and it is what the notification links to. The target repo's
own bookkeeping slice (§5.4) cites the brief by digest.

## 4. Where the human gates sit — design vs implementation

### 4.1 Design (Phases 9–12 now; the pattern generalizes)

The operator's stated preference is **all remaining design phases unattended, one human
review at the end**. That is feasible, with one real trade-off to rule on.

Every phase doc ends with an **open-decisions section that only the operator rules**, and
approval today folds those rulings operative in the approval slice (D-057…D-065 came
from Phases 6–7). Downstream phases consume those decisions. Two modes:

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
- **Mode B — per-phase asynchronous checkpoint.** Convergence sends the operator a
  notification; the next phase starts only on approval. This is today's process minus
  the relay.

**Ruled (operator, 2026-08-29): Mode A** with the decision inbox, plus the tripwire: if a
phase's open decisions are marked by the reviewer as **load-bearing for the next phase's
seam** (`blocks_downstream: true` in `review.json`), the orchestrator pauses at that phase
for the ruling. The operator gets an unattended run by default and is interrupted only
when the design itself says a ruling is needed to proceed soundly.

### 4.2 Implementation (per phase)

Per-phase human gates are **not optional** and the orchestrator will find them by §3.3:
every CA implementation phase will carry migrations (Phase 12 inventory), deployment
surface, and RVA pre/post straddles. The unattended stretch is *implement → gate →
review → fix → CLEAN*; the operator then applies migrations (Dev, Test), deploys, runs
`--dry-run` then a real run, runs RVA pre/post, and approves. Then the next phase's
implementation lane opens automatically.

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
  scan of the diff clean.
- **Land only converged slices,** by fast-forward onto `development`, then push (the
  operator's standing preference for finished green slices). No merge commits, no
  squash — the range discipline depends on it.
- **Ledger.** Append-only, kept in the orchestrator repository under `runs/<target>/<lane>.jsonl`
  (never inside the target repo's slice ranges): every invocation
  (agent, worktree, base, prompt digest), every artifact digest (`review.json`,
  `fold.json`, RVA report), every transition with its predicate inputs, every counter
  (rounds, tokens per agent as reported by each CLI, wall-clock). This is the evidence
  the operator reads at the human gate instead of a chat transcript.
- **Notify** at every STOP and every human gate (existing SMTP alert channel; the
  message names the lane, state, and the ledger line to read).
- **Kill switch.** A `STOP` file in the run directory, or Ctrl-C, halts before the next
  transition; nothing is landed on a halt. Resume re-reads the ledger and continues from
  the last verified state (the RVA-style "never infer latest": the resume names the
  ledger line it resumes from).

### 5.4 Bookkeeping becomes generated, not hand-written

Today every approval is recorded in three places by hand — the doc header `Status:`
line, the manifest `status:` block + `verified_slices`, and the STATUS.md / handoff
rollups — and the history shows that this sync is its own defect class (the Phase 6/7
"approval-record repair" commits `3b40b5e`, `5404e84`, `17bdd82`, `9396448`, `ab7b08e`).
The orchestrator's ledger already holds every fact those records restate (ranges,
round counts, severities, SHAs, dates). The manifest `verified_slices` list and the
STATUS.md phase row are therefore **rendered from the ledger** by the orchestrator into a
bookkeeping slice that Claude commits and Codex reviews as today — the prose stays
agent-authored, the SHAs and ranges stop being retyped. Handoff documents remain
Claude-authored; the orchestrator supplies their §2/§5 tables.

## 6. Hard boundaries the orchestrator enforces by construction

- No credentials in its process environment beyond git push (`DATABASE_URL`, provider
  keys, `ANTHROPIC_API_KEY`/Codex auth stay with the agent CLIs' own configs; the
  orchestrator never prints or persists either agent's config, which on this machine
  contains an MCP connection string with a password).
- Reviewer sandbox read-only; author tool allowlist from the lane manifest (design lane:
  file edit + git + pytest/pyflakes; no MCP DB, no Alpaca MCP, no web).
- The orchestrator cannot approve. `APPROVED` is written only from an operator action
  (a signed approval file or CLI invocation by the operator naming the SHA), and the
  approval-record commit is still authored by Claude as today so the record shape is
  unchanged.
- One lane at a time; no worktree exists for a lane that is not the running one.
- Every counter trip is a STOP. Silence is never a PASS (the RVA rule, applied to the
  loop).

## 7. Code integrity and supply-chain security (operator addition 2026-08-29)

**Operator ruling 2026-08-29: no dependency gate; scanning out of scope.** The loop must
not break on minor needs and pull the operator back in. Agents install and add whatever
libraries the work requires; whether a library is acceptable is decided by security
scanning **outside this design** — GitHub's push-time scanning (Dependabot alerts, secret
scanning, CodeQL) or a local open-source scanner the operator chooses — which flags a
problem library for a follow-up slice without ever having blocked the loop.

### 7.1 Guardrails inside the loop (cheap, never a gate on their own except the secret scan)

| Guardrail | What it does | On failure |
|---|---|---|
| **Dependency visibility** — a new or changed entry in `requirements*.txt` (pinned version) is named by the reviewer in `review.json` and listed in the next Human Gate Brief | the operator always *sees* what was added and why; nothing is hidden, nothing is blocked | none — informational |
| **Secret scan** of the diff and of every agent artifact (review, fold, brief) | no credential leaves the env file | STOP (the one guardrail that stays hard: a leaked key is not a minor issue) |
| **Security checklist in `review.json`** — a short fixed list the reviewer answers per code slice: parameterised SQL only (`ANY(%(ids)s)` discipline); no credentials or environment dumps in logs; least-privilege for new DB objects (owner `postgres`, DML grants to `trading_ai_app`); idempotent/atomic/dry-run-safe per the Job Robustness Contract; provider calls bounded and documented at the call site; new dependencies pinned and named; no dynamic code execution | the repo's own known failure classes are checked by the non-authoring agent every time | unfilled item = malformed review (§5.1); a failed item is an ordinary finding at the reviewer's severity |

### 7.2 "Same code" — binding review to what runs

- `review.json` names `reviewed_range` **and the tree digest** of the reviewed SHA; the
  orchestrator lands only a SHA whose tree digest equals the one reviewed CLEAN — a
  post-review commit, however small, is a new round.
- The release tag the operator deploys (`scripts/release/create_release_tag.sh`) is cut
  at the landed SHA; the Human Gate Brief states that SHA; `deploy_tag_to_test.sh`
  deploys the tag, not a branch. The reviewed SHA, the landed SHA, the tagged SHA, and
  the deployed SHA are one value, and the ledger records each step's evidence.

### 7.3 Out of scope — PHASE 2 (operator ruling 2026-08-29; options in `SECURITY_SCANNING_OPTIONS.md`)

Library vetting and vulnerability scanning of dependencies (GitHub Dependabot or a local
open-source scanner such as `pip-audit`), static security lint (`bandit`), hashed
requirements, egress blocking in test runs, and a dedicated second security-lens review.
These run on push or on the operator's schedule by other tooling; a finding there opens a
normal follow-up work item. They are not part of the orchestrator and never gate the loop.
The choice between GitHub scanning, local scanners, or a hybrid is **open for phase 2** and
analysed in `docs/design/SECURITY_SCANNING_OPTIONS.md` (Codex input requested).

## 8. Prerequisites (what is missing on this machine today)

1. **Codex CLI.** Only the Codex desktop app is installed (no `codex` on PATH). The
   headless entry point is the `@openai/codex` CLI's `codex exec`; it shares
   `~/.codex/auth.json` and `config.toml`. Install and confirm a read-only-sandbox
   `codex exec` run in a worktree returns a file and touches nothing in-tree.
2. **Claude headless.** `claude -p --output-format json` is available (2.1.163). Confirm
   `--resume`, tool allowlisting via settings, and that a worktree session cannot reach
   MCP servers the lane does not grant.
3. **Structured review output.** Codex's review instructions gain the `review.json`
   requirement (an AGENTS.md addition — process slice, Codex-owned or Claude-owned with
   the other reviewing, per the contract's symmetric rule).
4. **Contract amendment.** COLLABORATION_CONTRACT.md is LOCKED; a §1.2 "orchestrated
   serial execution" amendment by the operator is the authority this design needs. The
   still-unapproved §1.1 parallel protocol is orthogonal and is not a dependency.
5. **Dev RW access + login (§4.3).** A `trading_ai_agent` login with full DML + DDL on
   `trading_ai_v4`, both agents' MCP/CLI configs pointed at it for implementation lanes,
   the provider env file for those sessions, and the relocated schema-only template
   database. CLAUDE.md amendment slice to match the ruling.
6. **GitHub security scanning** enabled on the remote by the operator (Dependabot alerts,
   secret scanning; CodeQL optional) — outside the orchestrator, no build step.

## 9. Build plan (small, each step its own manifest + review)

| Step | Deliverable | Human involvement | Proves |
|---|---|---|---|
| M0 | New repository `trading-ai-orchestrator` (own `CLAUDE.md`/`AGENTS.md`, requirements, tests; this design moves there); prereqs §8: CLI install, two headless smoke runs (docs-only scratch lane on a throwaway `trading-ai` branch), `review.json`/`fold.json` schemas + the `trading-ai` AGENTS.md/CLAUDE.md obligations slice | operator creates the repo and installs the CLI; Codex/Claude cross-review the schemas | both agents are driveable and sandboxed from outside the target repo |
| M1 | Single-phase design loop (§3.1) with Mode B gating, target = `trading-ai`; run on **Phase 9** while the operator watches | operator kicks off the lane, approves at the end | the loop converges on a real phase without a relay |
| M2 | Multi-phase chaining, decision inbox, Mode A + tripwire (§4.1); run Phases 10–12 | operator rules inbox items at leisure; final review of 9–12 | unattended design completion |
| M3 | Implementation-lane mode (§3.2, §3.3 human-gate detection, §3.4 briefs, §7.1 guardrails, §7.2 digest binding, §4.3 Dev execution discipline); run on the first CA implementation phase | per-phase Test deployment + RVA pre/post | human gates are found by the system, not remembered by a person; reviewed code = deployed code |

M1 is the go/no-go: if a real phase does not converge under the loop, the fix is in the
agent contracts (§5), not in adding a third model.

## 10. Open decisions for the operator

1. ~~Mode A vs Mode B~~ — **RULED: Mode A + tripwire** (operator, 2026-08-29; §4.1).
2. `max_rounds` per phase (proposed 5) and the per-lane token/wall-clock budgets.
3. Whether P2 findings on **design** docs are fold-now (current practice) or backlog
   (contract letter); the orchestrator needs one rule.
4. Landing policy: fast-forward + push on convergence (proposed), or land only after
   operator approval.
5. Who owns M0–M3 as work items (default: Claude authors, Codex reviews; the orchestrator
   is code in its own repository and follows the same contract there, including §7.1).
   Residual from the §2.1 ruling: repository name/location and whether it is a private
   GitHub remote (recommended, so push-time scanning applies to it as well).
6. ~~Agent validation environment~~ — **RULED (operator, 2026-08-29): Dev is the agents'
   shared RW validation database, manual execution only, no jobs or timers ever** (§4.3;
   CLAUDE.md amended the same day). Residual: template relocation (`trading_ai_v4_template`
   vs schema-only dump).
7. ~~Security scope~~ — **RULED (operator, 2026-08-29): no dependency gate — agents add what
   the work requires; secret scan + reviewer checklist stay (§7.1); library vetting and all
   scanning deferred to GitHub push-time scanning / local open-source tooling (§7.3).**
