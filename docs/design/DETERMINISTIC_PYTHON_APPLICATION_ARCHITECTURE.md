# Deterministic Python Orchestrator — Application Architecture

**Status:** PROPOSED rev 3, 2026-08-31; independent review and operator confirmations
folded: walking-skeleton V0, machine-readable RVA prerequisite, separate bookkeeping,
local-only landing, split verify/land privilege, bounded invocations, and cross-lane
authorship handoff. This document turns the operator-ruled execution
design and the review/repair protocol into an implementable Python application. It does
not authorize a live lane. The governing documents remain
`ORCHESTRATED_EXECUTION_DESIGN.md`, `REVIEW_REPAIR_CONVERGENCE_PROTOCOL.md`, and
`PROJECT_CONTROL_PLANE.md`.

## 1. Architectural decision

Build a small, local-first foreground Python CLI named `assist`.
Do not begin with a Windows service, web application, queue, or orchestration model.

The executable has three responsibilities:

1. execute a deterministic lane state machine;
2. invoke Claude and Codex through typed CLI drivers; and
3. persist enough immutable evidence to monitor, stop, and recover the lane safely.

The orchestrator calls the agents. Claude and Codex never call each other. They exchange
schema-validated artifacts through the orchestrator, and no model decides which action
runs next.

Only four model actions exist in V0. Actions bind to roles, not permanently to vendors,
so a separately authorized handoff lane can swap owner/reviewer without mutating a lane:

| Actor | Action | Purpose |
|---|---|---|
| Lane author | `AUTHOR` | Create the first revision for the authorized work item. |
| Lane author | `REPAIR` | Resolve gate failures or review findings. |
| Lane reviewer | `REVIEW` | Review the complete current range and reconcile every historical finding. |
| Lane reviewer | `GUIDANCE` | Explain why a repair failed and the required approach, without supplying code. |

Initial review, re-review, rejection assessment, and final review are all `REVIEW`.
If guidance cannot unblock the author, the lane stops for a separately manifested,
one-way authorship-handoff lane; no in-place role mutation or reference-patch action exists.

## 2. Runtime topology

```text
operator shell
    |
    +-- assist start --project <id> --work-item <id>
    |       |
    |       `-- one lane worker process
    |               |
    |               +-- project registry/config/work-index resolver
    |               +-- state reducer (pure Python)
    |               +-- author/reviewer CLI children (role-selected, restricted)
    |               +-- verify worker process (target RVA/tests; no push privilege)
    |               +-- land worker process (fixed Git plumbing; no target code)
    |               `-- append-only project-local run directory
    |
    +-- assist status / watch / findings
    |       `-- reads derived run data; never controls transitions
    |
    `-- assist stop / retry-now / resume / gate respond
            `-- writes a typed operator command consumed by the lane worker
```

Many projects may be registered. There is one executing worker and one active lane
globally in the first release. A small global coordinator lock enforces that V1
cardinality but stores no project state. A project lock and target-branch lock protect
the selected project and integration branch. Monitoring commands are read-only and may
run concurrently; control commands append typed requests to the active project's command
inbox.

Detached execution and a Windows service are future scope. V0 remains attached to the
operator's session and recovers through the ledger after process or machine restart.

## 3. Separation of deterministic control and side effects

The application uses a functional-core / imperative-shell design.

### 3.1 Functional core

The core accepts only typed state, validated events, and immutable policy. It returns a
decision and performs no I/O:

```python
TransitionDecision = reduce(
    state: LaneState,
    event: DomainEvent,
    policy: LanePolicy,
)
```

A decision contains the next state, the next command if any, a stable reason code, all
predicate inputs used by the decision, and counter/finding-lifecycle updates.

The same inputs must always produce the same decision. Free-form prose, timestamps,
filesystem ordering, and agent session memory are never reducer inputs.

### 3.2 Imperative shell

The coordinator executes commands through ports, validates the result, appends a ledger
event, then calls the reducer again. Agent drivers, Git, the RVA, artifact-persistence
safety, console/status projection, the clock, and process control are adapters behind interfaces and use
fakes in unit tests.

This split prevents subprocess details or prompt output from becoming transition logic.

## 4. Domain model

### 4.1 Lane identity and immutable policy

A lane is identified by `(project_id, lane_id)` and is permanently bound at authorization to:

- project registry entry, `INDEX.md`, configuration, and work-index digests;
- target repository path and integration branch;
- manifest path and snapshot digest;
- authorization text and digest;
- `scope_base` full SHA;
- lane kind (`design`, `implementation`, or `bookkeeping`);
- manifest-declared scope, dependencies, and operator actions;
- agent/model/effort configuration;
- project-selected role-package, skill, prompt-template, and schema versions plus digests;
- round, 40-agent-invocation, finding-attempt, provider, and account-retry bounds plus recoverable-limit/outage
  schedules and child-process liveness policy;
- human-gate, local-only V0 landing, and later automatic-push graduation policy; and
- target-gate, limit-signal, console/status, artifact-size, and persistence-safety contract versions.

These values cannot be silently changed after authorization. A permitted change is a
typed operator amendment event. Scope-base or authorization changes create a new lane;
they never mutate an existing lane's history.

### 4.2 States

Persist stable states, not transient function names:

```text
AUTHORIZED
AUTHORING
VERIFYING
REVIEWING
REPAIRING
PAUSED_LIMIT
WAIT_OPERATOR
LANDING
COMPLETED
STOPPED
RECOVERY_REQUIRED
```

`COMPLETED` and `STOPPED` are terminal. `PAUSED_LIMIT` preserves the interrupted command,
classification, and `next_retry_at` without incrementing a review round.
`RECOVERY_REQUIRED` means the process crashed around
a side effect and deterministic reconciliation could not prove its outcome.

### 4.3 Commands and events

Commands request effects, for example `InvokeAuthor`, `VerifyRevision`, `InvokeReviewer`,
`InvokeGuidance`, `LandRevision`, and `OpenHumanGate`. Events record observed facts, for
example `AuthorResultAccepted`, `RevisionVerified`, `ReviewAccepted`, `InvocationLimited`,
`HumanResponseAccepted`, and `LandingReconciled`.

An event is accepted only after its source artifact has passed schema, digest, identity,
and state-precondition validation.

### 4.4 Single-phase runs and manifest preparation

A V0/V1 run executes one authorized phase/work item and then stops. It never infers or opens
the next phase from filenames, prose, Git history, work-index order, or an agent
recommendation. Multi-phase run plans and the decision inbox are future scope.

The selected target manifest must exist before `assist start`. A missing manifest is a
hard preflight rejection: Assist and Codex never synthesize it and the lane does not
bootstrap its own scope contract. This does **not** prohibit Claude from creating the next
phase manifest. The intended sequence is:

1. the operator authorizes manifest preparation for the next phase;
2. Claude authors it as a separate preparation slice against the current exact
   integration tip;
3. Codex reviews/finalizes that scope contract; and
4. the operator starts Assist with the now-existing work item.

The run records run ID, project/config/work-index digests, authorization, one manifest
path/snapshot, resolved agent/skill/policy/schema digests, exact `scope_base`, and landing
policy. On convergence V0 locally lands only the exact CLEAN-reviewed, gate-accepted tree,
marks the phase run `COMPLETED`, and stops before push. It does not perform or claim
bookkeeping. The operator then authorizes Claude to prepare a bookkeeping manifest against
the landed tip and separately starts that bookkeeping run. Thus V0 has four explicit
authorizations per phase: phase-manifest preparation, phase run, bookkeeping-manifest
preparation, and bookkeeping run.

The project's `work-index.json` may declare future expected work and show `MISSING`, but
that is routing/status metadata, not authorization. Phase 10-12 manifests are not created
in advance because their literal bases are unknown. Symbolic predecessor resolution and
multi-phase chaining require a separately approved future manifest/RVA contract.

## 5. Deterministic transition flow

### 5.1 Main path

```text
AUTHORIZED
   |
   v
lane author AUTHOR -> verify Git/range/scope/persistence-safety/target gate
   | valid
   v
lane reviewer REVIEW complete current range
   |-- CLEAN and convergence predicate true ---> LANDING or WAIT_OPERATOR
   `-- blocking findings ----------------------> lane author REPAIR
                                                     |
                                                     v
                                             verify full revision
                                                     |
                                                     v
                                             lane reviewer REVIEW again
```

Every review uses a fresh reviewer process and the complete current lane range. Session
resume is an optimization only; every author call receives the complete current evidence
package.

### 5.2 Transition table

| Validated condition | Next action/state |
|---|---|
| Authorized new lane | lane author `AUTHOR` |
| Author or repair result accepted | `VERIFYING` |
| Verification succeeds | lane reviewer `REVIEW` |
| Fixable target-gate failure with structured evidence | lane author `REPAIR` with a stable system finding |
| Scope/base/dependency/foreign-commit failure | `STOPPED` |
| Review has new or ordinarily open P0-P2 findings | lane author `REPAIR` |
| Author asks for guidance | lane reviewer `GUIDANCE` (no code) |
| Same finding remains after the first repair | lane reviewer `GUIDANCE` (no code) |
| Same finding remains after a guided repair | `STOPPED/HANDOFF_REQUIRED`; no in-place role change |
| Author rejects a finding with evidence | lane reviewer `REVIEW` assesses the rejection |
| Reviewer accepts rejection | Finding becomes `CLOSED_NOT_A_DEFECT` |
| Reviewer disagrees first time | lane author `REPAIR` receives technical clarification |
| Second reasoned disagreement persists | `WAIT_OPERATOR` for adjudication |
| A `VERIFIED_RESOLVED` finding is later `REOPENED` twice | `WAIT_OPERATOR` for ping-pong adjudication |
| Unknown contract, required scope expansion, or operator-only action | `WAIT_OPERATOR` |
| Review is CLEAN but historical blockers or evidence remain incomplete | reject artifact once, then `STOPPED` |
| Converged and human gate required | `WAIT_OPERATOR` |
| Converged and landing policy permits | `LANDING` |
| Exact reviewed tree locally lands in V0 | `COMPLETED`; print that bookkeeping remains separate |

The reducer uses enum values, stable IDs, counts, and booleans. It never searches prose
for phrases such as "still broken" or "looks clean".

### 5.3 Finding-level escalation

Escalation is per finding, not per lane revision:

```text
attempt 0: REVIEW finding -> lane author REPAIR
attempt 1 still present: reviewer GUIDANCE (no code) -> author REPAIR
guided repair still present: STOP/HANDOFF_REQUIRED
```

An authorized handoff first creates a separate Claude-authored/Codex-reviewed manifest-
preparation slice rooted at the stopped verified tip. The dependent repair lane then uses
that preparation commit as literal base, records the stopped tip/finding transfer, and has
owner Codex/reviewer Claude. The lane
policy is immutable, the swap is one-way, and Codex's optional self-check is
`lens: advisory`, which the reducer cannot consume as CLEAN. The original lane never lands
alone; after the dependent lane is CLEAN and transferred findings are verified resolved,
the three contiguous, independently reviewed ranges locally land as one audited handoff
chain. No actor reviews a range containing its own commits.

A lane may legitimately reach revision 10 while different findings make progress. The
lane revision cap is only a final circuit breaker; it must not replace per-finding
stagnation, rejection, regression, oscillation, and ping-pong detection.

### 5.4 Gate failure classification

The current target RVA exit code is not rich enough to route failures safely. Before the
orchestrator automatically asks Claude to fix a failed gate, the target must publish a
machine-readable result with stable categories:

- `FIXABLE_TEST`, `FIXABLE_LINT`, `FIXABLE_STATIC_CHECK` -> `REPAIR`;
- `DOCS_INCONCLUSIVE_SCOPE_PASS` -> accepted for a docs-only lane;
- `SCOPE`, `BASE`, `DEPENDENCY`, `FOREIGN_COMMIT` -> STOP;
- `UNKNOWN` or malformed output -> STOP.

Until this contract exists, a non-accepted RVA result must STOP. Parsing human-oriented
RVA prose to infer a category is prohibited.

## 6. Agent invocation and role packages

### 6.1 Who invokes what

The `LaneCoordinator` selects an action from the reducer decision. The corresponding
driver builds an argument array and directly starts `claude` or `codex` with
`shell=False`. Neither the monitor nor either model selects the next role or mode.

Each invocation is represented by an immutable `InvocationSpec`:

```json
{
  "invocation_id": "LANE-042-I017",
  "actor": "codex",
  "role": "reviewer",
  "action": "GUIDANCE",
  "review_context": "AFTER_FAILED_GUIDANCE",
  "round": 5,
  "scope_base": "<full-sha>",
  "current_sha": "<full-sha>",
  "tree_digest": "<git-tree-id>",
  "manifest_digest": "<sha256>",
  "evidence_package": "inputs/I017.json",
  "role_package": "codex-reviewer/v1",
  "role_package_digest": "<sha256>",
  "output_schema": "review/v2",
  "timeout_seconds": 3600
}
```

The dynamic prompt contains only the invocation envelope and evidence pointers. Static
review/repair behavior lives in versioned role packages and skills.

Role packages and skills are resolved from the registered project folder. Shared bases
may be referenced explicitly, but there is no implicit global agent or skill selection.
The invocation records project ID plus the exact project `INDEX.md`, role, skill, policy,
and schema digests. This lets Engine and UI work use different domain rules while the
deterministic action vocabulary remains shared.

### 6.2 Agent/profile versus skill

Use the same separation on both sides:

- the agent profile/configuration controls model, effort, sandbox, tools, permissions,
  and working directory;
- the skill controls the reusable `AUTHOR`, `REPAIR`, `REVIEW`, or `GUIDANCE` protocol;
- the invocation envelope supplies lane-specific facts.

Skill selection must be explicit. Do not depend on Claude or Codex implicitly deciding
that a skill is relevant. The exact supported CLI mechanism is established and pinned by
V0 capability tests; the domain layer is independent of vendor-specific flags.

A direct Codex CLI review should not create a parent model session merely to spawn a
review subagent. If the installed Codex CLI cannot directly select a custom agent, the
driver uses a pinned reviewer configuration/profile plus the explicitly named review
skill. The security and output contract remain identical.

The complete project context is supplied on every call: project orientation and required
reading, target governing files, manifest/authorization snapshot, current evidence,
selected role package, and selected skill. A project package cannot reference files
outside its declared project/shared roots or target-governing roots.

### 6.3 V0 prompt control

Project-specific author/reviewer prompt and skill files exist from V0 because Engine and
UI rules differ. Their paths and digests are immutable within a lane; changes apply only
to a new lane through ordinary reviewed repository changes. Evaluation fixtures and the
six-step prompt change-control workflow are deferred until real-lane evidence shows they
are needed.

## 7. Structured contracts

### 7.1 Required external artifacts

The implementation needs versioned schemas for:

| Artifact | Producer | Purpose |
|---|---|---|
| `author-result` | lane author | Initial commit, session identity, files changed, tests, and evidence. |
| `fold` | lane author | Exact disposition of every outstanding blocking finding. |
| `review` | lane reviewer | Gating review, no-code guidance, or non-gating advisory output, selected by a required `lens`. |
| `target-gate-result` | target RVA | Stable gate category, checks, commands, and evidence digests. |

These are the four V0 external schemas. Typed operator commands and ledger events are
internal domain records, not model-produced external artifacts.

### 7.2 Required schema upgrade

The present `review.schema.json` and `fold.schema.json` are useful V0 drafts but cannot
enforce the agreed protocol. Version 2 must add at least:

- explicit assessment of every prior blocking finding;
- lifecycle outcome (`STILL_PRESENT`, `VERIFIED_RESOLVED`, `REOPENED`,
  `REVIEWER_ACCEPTS_REJECTION`, or `REVIEWER_DISAGREES`);
- root cause, consequence, recommended approach, invariants, and closure evidence;
- all repair dispositions from the convergence protocol;
- changed files, repair commit, test evidence, and guidance deviations;
- `lens: gating | guidance | advisory`, with only `gating` eligible to supply CLEAN;
- `review_kind: design | code | security | bookkeeping`; v1's domain-valued `lens` is
  renamed rather than overloaded;
- cross-lane transferred-finding and handoff provenance; and
- contradiction rules preventing `CLEAN` with an unresolved historical blocker.

Do not silently extend schema v1. Publish v2 under a new `$id`, retain v1 fixtures, and
pin each lane to exactly one schema set.

### 7.3 Exact-set reconciliation

Before accepting a fold:

```text
outstanding blocking finding IDs == disposition finding IDs
```

Before accepting a re-review:

```text
historical blocking finding IDs == prior-finding assessment IDs
```

Duplicates, omissions, unknown IDs, illegal lifecycle transitions, inconsistent SHAs,
or a claimed CLEAN verdict with open evidence cause one correction retry. A second
malformed result STOPs the lane.

### 7.4 Output capture

The drivers capture structured output and let the orchestrator write accepted artifacts
to the run directory. Codex does not need write access to either repository merely to
create `review.json`. Human-readable prose is a field or companion artifact and is never
parsed by the reducer.

Raw streams are untrusted and are never persisted in full. Drivers parse them in memory
and persist only schema-validated structured artifacts. When recovery requires diagnostic
text, it is bounded to 1 MiB per invocation, excludes environment/auth-file capture, and
passes built-in forbidden-field/path handling. Structured evidence and human briefs are
retained locally without automatic expiry in V1.

## 8. Evidence, verification, and test integrity

After every Claude return, the coordinator independently verifies:

- the claimed full commit exists and descends from immutable `scope_base`;
- the range is contiguous and contains no foreign commits;
- the author worktree is clean;
- changed files satisfy the target RVA scope contract;
- current full SHA and Git tree digest match the claimed result;
- structured artifacts contain no forbidden credential/auth/environment fields and all
  persisted diagnostics satisfy the path/type/1-MiB boundary;
- required tests and target gate ran under the configured policy; and
- no unexplained test deletion, skip/xfail, assertion weakening, test-count reduction,
  or mock substitution occurred.

For a testable P0-P2, Codex supplies a concrete counterexample and required regression
behavior in the review artifact. V0 never directly executes reviewer-supplied code or
commands. The lane author adds the permanent repository test, demonstrates pre-fix fail
and post-fix pass when practical, and the restricted verify worker runs the repository
gate/tests. Executable external challenges and mutation testing are future features that
require their own sandbox design.

Verification produces typed evidence. Agent claims and successful exit codes alone are
never sufficient.

## 9. Persistence and audit trail

### 9.1 Run layout

```text
projects/<project-id>/
  INDEX.md                      # versioned project orientation; no runtime status
  project.yaml                  # versioned integration/package configuration
  work-index.json               # versioned work-item/manifest routing catalog
  agents/ skills/ policies/ plans/
  state/                        # generated and gitignored
    current.json                # atomic derived project projection
    project-index.json          # atomic derived work/status view
    project.lock
    runs/<run-id>/
      run.json                  # immutable single-phase run/policy digests
      ledger.jsonl              # authoritative, append-only, digest-chained
      status.json               # atomic derived run projection
      commands/                 # typed operator requests and acknowledgements
      lanes/<lane-id>/
        lane-policy.json        # immutable resolved policy and digests
        STOP                    # optional operator kill switch
        inputs/                 # immutable invocation evidence packages
        artifacts/              # accepted author/fold/review/gate + finding/P3/handoff artifacts
        invocations/<id>/        # request/result JSON + optional bounded diagnostic text
        briefs/                  # human gate and completion briefs
```

The run ledger is authoritative. `current.json`, `project-index.json`, and `status.json`
exist for fast discovery/monitoring but must be reconstructed and checked against the
ledger during resume. There is no shared top-level runtime `runs/` directory and no
project runtime file outside its project folder.

Most persisted files are JSON. The ledger is JSON Lines; human gate/completion briefs are
Markdown; optional recovery diagnostics are bounded text. Per-lane artifacts include
author-result, review/fold/guidance, target-gate/test results, finding history, a derived
`p3-backlog.json`, handoff evidence, commands/acknowledgements, SHA/tree/digest
evidence, and changed-file lists. Full raw CLI transcripts, environment dumps, auth files,
tokens, and credential values are never artifacts. Temporary worktrees are removed after
safe completion or reconciliation.

Each ledger line includes event sequence, event ID, lane ID, UTC timestamp, event type,
state before/after, reason code, predicate inputs, artifact digests, previous-event
digest, and current-event digest. Appends are flushed before the next effect begins.

### 9.2 Effect protocol and idempotency

Every external effect uses intent/completion records:

```text
ACTION_PLANNED -> ACTION_STARTED -> ACTION_COMPLETED or ACTION_FAILED
```

The invocation ID is stable across reconciliation. Read-only actions may be rerun safely.
Mutating actions require explicit reconciliation:

- interrupted Claude call: inspect worktree, Git range, and artifact binding; accept only
  one provable result, otherwise enter `RECOVERY_REQUIRED`;
- interrupted review or gate: discard partial output and rerun from the same exact SHA;
- interrupted V0 landing: compare expected SHA/tree with the local integration ref;
  complete only when the exact local state is proven, otherwise STOP; V1 additionally
  reconciles the remote only after automatic-push graduation;
- interrupted console rendering: no state reconciliation is required because the ledger
  event is authoritative and `assist watch` can replay it.

The system never assumes exactly-once subprocess behavior.

### 9.3 Resume

With exactly one active V1 lane, `assist resume` discovers the sole non-terminal
project-local projection, then performs:

1. global coordinator, project, and target-branch lock acquisition;
2. complete ledger digest-chain validation;
3. state replay and status-projection comparison;
4. target base/current SHA, worktree, and integration-branch reconciliation;
5. unfinished-effect reconciliation; and
6. continuation from the last verified event selected by replay, not by filename or
   modification time.

The short command does not mean "trust latest": it is allowed only because the registry
contains exactly one non-terminal lane and the application validates the complete chain
before selecting its last verified event. Zero or multiple active lanes refuse to guess.
For diagnostics and ambiguous recovery, the explicit form remains:

```text
assist resume --project <id> --run <id> --lane <id> --from-event <sequence>
```

`assist resume --retry-now` performs the same reconciliation and then emits the typed
immediate-retry command only when the verified state is recoverably paused.

### 9.4 Durable limit and outage scheduler

The scheduler persists `classification`, `detected_at`, `reported_reset_at`,
`usage_window_anchor`, `next_retry_at`, `attempt`, and the exact interrupted
`InvocationSpec` before waiting. Its clock adapter uses UTC internally and an
interruptible wait; a typed command inbox is checked at least every five seconds.

Policy is project-configured but V1 defaults are operator-ruled:

- hourly limit: reported reset plus one minute; absent a valid report, the affected
  agent's run-start usage anchor plus 5h30; then every 30 minutes;
- weekly limit: every eight hours, or sooner at a reported reset plus one minute;
- recognized limit of unknown window: every 30 minutes;
- provider/service outage: every hour; and
- authentication/account failure: one confirmation retry, then `WAIT_OPERATOR`.

Recoverable limit/outage waiting has no total 12-hour stop budget. It does not consume a
round or finding attempt. Immediately before every newly spawned `claude` or `codex`
process—including a retry—the ledger increments the lane's 40-invocation counter. Each
attempt produces a ledger event and console/status update.
`assist retry-now` appends `IMMEDIATE_RETRY_REQUESTED`, wakes
the worker, and attempts the same step; failure restores the normal schedule.

## 10. Monitoring and operator interaction

Recommended CLI surface:

```text
assist doctor
assist start --project trading-ai-engine --work-item <id>
assist status [--json]
assist watch [--verbose]
assist retry-now
assist stop --reason <text>
assist resume [--retry-now]

# explicit diagnostic forms remain available
assist status --project <id> --run <id> --lane <id> [--json]
assist resume --project <id> --run <id> --lane <id> --from-event <n>
```

`doctor` validates CLI presence and pinned capabilities, repository cleanliness, target
configuration, project registry/work-index consistency, selected manifest readiness,
project-local state roots,
subscription-only authentication, schemas, role/skill digests, lock availability, limit
signals/schedules, and console/status configuration. It never opens a lane.

Every state transition prints one concise line containing lane, state, actor/action,
round, current SHA prefix, reason, and next step. `watch` follows these structured events.
`status` reports state, current actor/action, elapsed time, current SHA/tree, last verified
event, findings, review-round and agent-invocation counters, next transition/reason, and
evidence/brief paths.

Print and persist status events for limit/outage detection, every scheduled check that
remains blocked, every manual retry, limit/outage clear, successful resume, `COMPLETED`,
`WAIT_OPERATOR`, `STOPPED`, and failed recovery. Each event identifies project, work item,
lane, agent/step, detected/reset/next-check times, and an exact recovery command. V1 has no
external notification transport; `assist status` and `assist watch` read the project-local
projections/ledger.

### 10.1 Console exchange view

Console rendering is a read-only projection of accepted ledger events and artifacts:

```text
state machine -> validated artifact -> ledger -> ConsoleRenderer
```

The renderer never creates an event, selects an action, parses prose for a transition, or
blocks lane execution. Foreground `start` and `watch` use the same renderer.

Three output levels are supported:

- default: phase/task, actor/action, SHA, verification result, finding counts and titles,
  next action, and artifact path;
- `--verbose`: the complete bounded and sanitized artifact being exchanged between
  Claude and Codex; and
- `--json`: the same console events as JSON Lines for later tooling.

After each accepted author or reviewer result, the console prints a clearly delimited
`CLAUDE -> CODEX` or `CODEX -> CLAUDE` exchange block. This is the exact validated
human-readable artifact sent onward, not a new LLM summary. Oversized content is
truncated on screen with its immutable artifact path and digest. Schema/field/path/size
persistence safety is applied before display. A broken or disconnected console cannot stop the worker;
the ledger remains authoritative and `watch` can replay from an event sequence.

## 11. Security and privilege boundaries

- Use argument arrays with `shell=False`; never interpolate agent text into a shell.
- Treat target gates and repository tests as model-authored code. They run only in a
  separate verify process with an allowlisted environment, no push credentials or helper,
  no integration-checkout write, and no network/DB access unless exactly granted.
- The land worker is a separate executable/process. It receives only schema-validated Git
  facts, executes fixed Git plumbing, and never imports target modules or runs tests,
  hooks, agent output, or artifact commands. V0 has no remote-push action.
- Every land command supplies `-c core.hooksPath=<orchestrator-owned-empty-directory>`.
  Preflight snapshots the common Git hooks directory; any lane-created/changed hook STOPs.
- Reviewer checkout and tool policy are read-only. Reviewer output is captured by the
  parent process and stored outside the target repository.
- Author access is the intersection of target policy, lane policy, and action policy.
- Agent configuration and secret files are referenced by opaque paths or preconfigured
  profiles; their contents are not loaded into prompts, ledgers, or command lines.
- V1 agent billing mode is subscription-only: Codex must report ChatGPT account
  authentication and Claude must use the operator's Claude account login. The Claude
  child environment excludes `ANTHROPIC_API_KEY`; Codex is pinned to ChatGPT login. No
  driver automatically switches to API-key billing, buys credits, enables overage, or
  changes model after a limit.
- Child environments are built from an allowlist. Secrets needed by an implementation
  agent are injected only into that child through a reviewed launcher/profile.
- Shared/project agent and skill packages prohibit environment/auth/credential capture.
  Persisted output is limited to schema-defined artifacts and optional 1-MiB diagnostics;
  obvious credential fields, auth-file paths, and environment dumps are rejected. A
  dedicated repository secret scanner is future security-phase scope.
- The orchestrator never receives live-trading credentials and never acts on Test.
- Human responses are bound to lane, exact SHA/tree, brief digest, action, and scope.
- Landing requires an unchanged integration tip and exact CLEAN-reviewed tree. V0 ends
  after the successful local fast-forward. V1 remote verification exists only after tests
  prove agent/verify children cannot push, no model-authored code can execute in the land
  process, and three watched lanes have zero scope/tip/digest/hooks surprises.
- Project configuration and state paths are containment-checked. One project's adapters,
  commands, console/status projection, or recovery code cannot resolve another project's state root.

One wording correction is necessary in the existing invariants: because the orchestrator
captures child stdout/stderr, it cannot honestly guarantee that a misbehaving agent never
emits a secret into its process memory. The enforceable guarantee is that the orchestrator
does not intentionally load secret values and does not persist or log unsafe raw output.
Child output must be treated as hostile and bounded accordingly.

## 12. Python package architecture

```text
src/orchestrator/
  cli.py                # start/status/stop/resume/retry-now/doctor parsing
  application.py        # foreground coordinator loop; no transition judgement
  model.py              # frozen state, event, policy, and invocation types
  reducer.py            # pure transitions, finding lifecycle, counters
  project.py            # one project config/index plus containment and locks
  artifacts.py          # four external schema validators and bounded persistence
  ledger.py             # append/replay/digest chain and status projection
  agents.py             # thin Claude/Codex role-selected drivers
  verify_worker.py      # credential-free RVA/test process adapter
  land_worker.py        # fixed local Git plumbing; hooks disabled; no target import
  git.py                # read-only facts, worktree, tip/scope/digest checks
  recovery.py           # effect intent/completion reconciliation
  limits.py             # fake-clock schedules and 40-invocation counter
  console.py            # deterministic event/artifact rendering
```

Domain dataclasses are frozen. Adapters return typed results and never choose a state.
Vendor-specific CLI events and failures are classified only through capability contracts
pinned by `assist doctor` and registered project configuration.

## 13. Testing strategy

The fast suite invokes no real agents, network, database, or target repository.

1. Reducer table tests cover every `(state, event)` pair; unspecified pairs fail closed.
2. Finding-lifecycle tests cover persistence, rejection, reopening, no-code guidance,
   cross-lane handoff, ping-pong, oscillation, and exact-set reconciliation.
3. Invariant/property tests prove that no path lands with an unresolved blocker,
   unverified tree, malformed artifact, exhausted round/finding bound, or human gate.
4. Adapter contract tests use recorded, redacted Claude/Codex/RVA streams.
5. Fault-injection tests crash before and after every intent/completion append and prove
   safe resume or `RECOVERY_REQUIRED`.
6. Ledger tests detect deletion, reordering, rewriting, duplicate sequence, and digest
   corruption.
7. Persistence-safety tests cover command injection, path escape, forbidden
   credential/environment/auth fields, the 1-MiB diagnostic bound, unsafe symlinks, and
   malicious structured strings.
8. A manual V0 smoke suite uses a disposable repository and throwaway branch to verify
   actual CLI flags, sandbox behavior, output schemas, timeouts, cancellation, limits,
   and session resume.
9. The first real design lane runs in foreground while the operator watches; V0 locally
   lands and stops before push and before the separately authorized bookkeeping run.
10. Project-isolation tests register Engine and UI simultaneously and prove that config,
    locks, state, commands, artifacts, and resume cannot cross project roots.
11. Scheduler tests use a fake clock for hourly 5h30/30m, weekly 8h, outage 1h,
    reported-reset, restart, and `retry-now` paths; no pause increments review counters.
12. Authentication tests prove subscription-only preflight and child-environment
    sanitization without reading or persisting credential values; `doctor` checks auth
    inside the exact driver environment and `CODEX_HOME` rather than the parent shell.
13. Privilege tests prove agent/verify children cannot push, the land worker never runs
    target/model-authored code, hooks are disabled and monitored, and V0 has no push action.
14. Tip-lease tests move the integration branch before every agent/gate/land boundary and
    prove the lane stops before consuming the next expensive invocation.
15. Counter tests prove every spawned Claude/Codex process—including post-pause retries—
    increments the 40-invocation ceiling exactly once.

## 14. Delivery slices — walking skeleton

| Slice | Deliverable |
|---|---|
| T0 | In `trading-ai`, implement/review the versioned machine-readable RVA result. No Assist live lane precedes it. |
| V0-A | One project folder, reduced domain/reducer, four v2 external schemas, ledger/replay, fake agents, and a simulated author-review-repair-guidance loop. |
| V0-B | Real CLI drivers, exact-child auth doctor, verify/land process split, local-only hooks-disabled landing, recovery, limits, console, and the five control commands. |
| V0-C | One watched real design lane and its separately authorized bookkeeping run; record outcome metrics and keep push manual. |
| V1 | Enable automatic push only after the explicit privilege and three-clean-watched-lane graduation gate; then add implementation human gates. |
| Future | Multi-phase, detach/service, executable challenges, mutation testing, prompt evaluation/change control, plans/decision inbox, and additional active projects. |

Each slice has its own manifest, one author, one non-authoring reviewer, and deterministic
acceptance tests.

## 15. Pre-live obligations (ruled, not open design questions)

1. Deliver the separately reviewed `trading-ai` RVA JSON contract before any live lane.
2. Publish the four v2 external schemas; v1 is draft test data and cannot govern a lane.
3. Enforce 10 review rounds and 40 spawned agent processes; no token or total-time budget.
4. Keep V0 local-only, stop before push, and run bookkeeping separately under its own
   prepared manifest and operator-started run.
5. Prove the verify/land process split, exact-child credential absence, hooks protection,
   and the automatic-push graduation predicate.
6. Smoke-test both subscriptions, schemas, sandbox, limit surfaces, and resume inside the
   exact child launch environments; shell-level auth observations are insufficient.
7. Check the integration tip before every agent, gate, and land effect; document the
   operator's exclusive-branch obligation and expose an advisory target-branch marker.
8. Implement no-code GUIDANCE and one-way cross-lane authorship handoff, including the
   separate handoff-manifest preparation slice. A Codex-authored
   repair is reviewed by Claude; Codex `lens: advisory` can never gate its own work.
9. Keep executable reviewer challenges, mutation testing, dedicated scanners, detach,
   prompt-evaluation machinery, and multi-phase plans out of V0.
10. Implement intent/completion/reconciliation records before any live external effect.
11. Retain project-local JSON/JSONL/Markdown evidence indefinitely, never full raw streams,
    and bound optional diagnostics to 1 MiB without env/auth/token material.
12. Keep one project folder and project-specific prompt/skill files from V0, while only
    one execution worker/lane is active globally.
13. Implement the ruled limit schedules, immediate retry, subscription-only mode, manual
    restart/resume, and console/status/watch monitoring without external transport.
14. Require existing literal-base manifests. Assist and Codex never synthesize one;
    Claude manifest preparation is separately authorized and reviewed.

## 16. First-release acceptance criteria

The deterministic application is ready for its watched real-lane trial only when:

- every state transition is covered by fail-closed reducer tests;
- a simulated lane reaches author, review, repairs, no-code guidance, final review, and
  convergence without prose routing; a separate fixture proves cross-lane handoff;
- every historical blocker is explicitly reconciled on every re-review;
- malformed, contradictory, missing, oversized, or secret-bearing artifacts STOP;
- crash injection demonstrates safe recovery around each external effect;
- a reviewer cannot write to the target checkout and an author cannot exceed allowed scope;
- CLI capability smoke evidence is pinned and `assist doctor` passes;
- foreground monitoring, stop, and named-event resume work after simulated process loss;
- short-form status/resume/retry locate exactly one active lane while explicit forms
  remain available, and ambiguous active state refuses to guess;
- Engine/UI fixtures prove project-local state and package isolation;
- fake-clock limit/outage tests prove every agreed schedule, durable restart, immediate
  retry, and console/status event without consuming a review round;
- per-phase fixtures prove ledger-authoritative JSON/JSONL state, rebuildable projections,
  indefinite structured retention, no full transcript, and the 1-MiB diagnostic bound;
- console default, verbose exchange, and JSON views replay identical accepted events
  without influencing transitions;
- every pre-effect tip check stops a stale lane before another agent invocation;
- agent/verify children cannot push or access the integration checkout; the land process
  executes no target/model code and ignores hooks; V0 contains no push transition;
- a phase run stops after local landing and cannot claim bookkeeping completion;
- the watched lane report records four operator authorizations, interventions, elapsed
  time, agent invocations, review rounds, pauses, and comparison to the manual baseline;
- landing an unreviewed tree, changed integration tip, or lane with a gate is impossible; and
- every STOP/gate is understandable from one brief without raw agent transcripts.
