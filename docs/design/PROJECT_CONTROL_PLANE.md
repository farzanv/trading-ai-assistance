# Project Control Plane

**Status:** PROPOSED rev 3, operator-directed 2026-08-31. Separate bookkeeping,
cross-lane handoff, branch-lease, invocation-cap, and privilege-boundary decisions folded.
This document defines how the
`trading-ai-assistance` CLI manages multiple target projects without mixing their
configuration, agent contracts, skills, plans, or runtime records. It is design-only and
does not authorize creation or modification of a target-repository manifest.

## 1. Decision

The high-level project-management layer is hosted in the AI-assist repository, not in
the target repositories. Its subsystem name is **Project Control Plane** and its root is
`projects/`.

The control plane routes deterministic work. It does not replace a target repository's
technical authority. Target source code, manifests, engineering rules, design decisions,
and deterministic release gate remain in the target repository.

V1 may register many projects but executes exactly one lane globally. Read-only monitor
commands may run concurrently. Concurrent project execution is a future capability and
must not be implemented by launching uncontrolled duplicate workers.

## 2. Final repository structure

```text
trading-ai-assistance/
|-- README.md
|-- CLAUDE.md
|-- AGENTS.md
|-- STATUS.md
|-- docs/
|   `-- design/
|-- projects/
|   |-- INDEX.md                         # human/agent entry point for managed projects
|   |-- registry.yaml                    # project_id -> project.yaml; no runtime status
|   |-- trading-ai-engine/
|   |   |-- INDEX.md                     # objective, boundaries, terminology, reading order
|   |   |-- project.yaml                 # repo path, branch, gate, manifest root, package refs
|   |   |-- work-index.json              # declared work items and exact manifest references
|   |   |-- agents/                      # project-specific versioned role packages
|   |   |-- skills/                      # project-specific explicit skill packages
|   |   |-- policies/                    # lanes, limits, monitoring, persistence
|   |   |-- plans/                       # reserved for future multi-phase plans
|   |   `-- state/                       # generated runtime data; entirely gitignored
|   |       |-- current.json             # atomic, rebuildable projection
|   |       |-- project-index.json       # atomic, rebuildable work/status projection
|   |       |-- project.lock             # execution lock for this project
|   |       `-- runs/
|   |           `-- <run-id>/
|   |               |-- run.json
|   |               |-- ledger.jsonl     # authoritative, append-only, digest-chained
|   |               |-- status.json      # rebuildable run projection
|   |               |-- commands/
|   |               `-- lanes/
|   |                   `-- <lane-id>/
|   |                       |-- lane-policy.json
|   |                       |-- inputs/
|   |                       |-- artifacts/       # includes p3-backlog + handoff evidence
|   |                       |-- invocations/
|   |                       `-- briefs/
|   `-- trading-ai-ui/
|       |-- INDEX.md
|       |-- project.yaml
|       |-- work-index.json
|       |-- agents/
|       |-- skills/
|       |-- policies/
|       |-- plans/
|       `-- state/
|-- shared/
|   |-- agents/                          # reusable bases; never selected implicitly
|   |-- skills/
|   `-- policies/
|-- schemas/                             # global versioned interchange schemas
|-- manifests/                           # work manifests for AI-assist itself only
|-- src/orchestrator/
`-- tests/
```

There is no shared top-level runtime `runs/` store. Every project owns all of its state,
ledgers, commands, console/status events, and recovery records below its own `state/` directory.
No project may read or write another project's state during execution.

## 3. Static control files and runtime truth

The files have deliberately different authority:

| File | Owner | Authority |
|---|---|---|
| `projects/registry.yaml` | operator-reviewed configuration | Defines available project IDs and config paths. |
| `<project>/INDEX.md` | operator-reviewed project guidance | Human/agent orientation and required reading order; never runtime status. |
| `<project>/project.yaml` | operator-reviewed configuration | Defines repository integration, manifest root, gate, role/skill packages, and policies. |
| `<project>/work-index.json` | operator-reviewed project plan | Names known work items, exact target-manifest paths, lane kinds, dependencies, and plan membership. |
| `<project>/state/runs/*/ledger.jsonl` | orchestrator | Sole authoritative runtime history. |
| `<project>/state/current.json` | orchestrator | Rebuildable pointer to the project's non-terminal run/lane, if any. |
| `<project>/state/project-index.json` | orchestrator | Rebuildable view of manifest existence and execution status. |

Configuration and observed status must not be manually mixed into one file. A generated
projection may contain configuration digests and status together for display, but resume
always validates the ledger, Git, manifest snapshot, and project configuration; it never
trusts the projection alone.

`state/` is retained locally and gitignored. It contains no credentials, environment-file
contents, CLI authentication material, or full raw CLI streams.

## 4. Project registry and configuration

Example registry:

```yaml
schema_version: 1
projects:
  trading-ai-engine:
    config: trading-ai-engine/project.yaml
  trading-ai-ui:
    config: trading-ai-ui/project.yaml
```

Example project configuration:

```yaml
schema_version: 1
project_id: trading-ai-engine
display_name: Trading AI Engine
repository:
  path: "c:/Repos/trading-ai"
  integration_branch: development
  manifest_root: dil-engine/manifests
gate:
  cwd: dil-engine
  command: [python, scripts/verify_release.py, --manifest, "{manifest}", --sha, "{sha}", --phase, static]
context:
  project_index: INDEX.md
agents:
  author: agents/claude-author-v1.md
  repair: agents/claude-repair-v1.md
  reviewer: agents/codex-reviewer-v1.md
  reviewer_assist: agents/codex-assist-v1.md
skills:
  design: skills/design-v1.md
  implementation: skills/implementation-v1.md
  bookkeeping: skills/bookkeeping-v1.md
policies:
  lanes: policies/lanes.yaml
  limits: policies/limits.yaml
  monitoring: policies/monitoring.yaml
  persistence: policies/persistence.yaml
```

Paths are resolved relative to `project.yaml` unless the schema explicitly says they are
relative to the target repository. Resolved paths must remain inside their declared root;
symlink or traversal escape is a STOP.

## 5. Work index and manifests

`work-index.json` is the high-level routing catalog. It may list future expected work and
show a missing manifest, but a missing file is never executable and is never created by
Assist or Codex during a lane. Claude may create the next phase manifest only through a
separately authorized and reviewed manifest-preparation slice after the predecessor lands.

```json
{
  "schema_version": 1,
  "project_id": "trading-ai-engine",
  "work_items": [
    {
      "id": "ca_platform_design_phase9",
      "kind": "design",
      "manifest": "ca_platform_design_phase9.yaml",
      "depends_on": ["ca_platform_design_phase8"]
    }
  ]
}
```

The manifest path is resolved under the target repository's configured `manifest_root`.
The target manifest remains the authority for `scope_base`, allowed files, tests,
dependencies, operator actions, pre/post gates, and verified slices. The run stores an
immutable snapshot and digest; the work index never duplicates those fields.

One phase lane uses one manifest through all review revisions. Bookkeeping is always a
separate work item/run in V0 with a manifest prepared against the locally landed phase tip.
The phase run stops without claiming bookkeeping completion. The honest operator sequence
is: authorize phase-manifest preparation, start phase, authorize bookkeeping-manifest
preparation, start bookkeeping.
Existing historical manifests are not retrofitted merely to register a project.

With the current literal-`scope_base` RVA contract, future phase manifests are prepared
sequentially after the predecessor lands. Listing Phase 10-12 in the work index does not
make missing or stale manifests runnable. V1 runs exactly one phase and stops; symbolic
predecessor resolution and multi-phase chaining require a separately approved future
manifest/RVA schema change.

If a guided repair still fails, the current lane stops `HANDOFF_REQUIRED`. The operator
separately authorizes Claude to prepare/review a handoff manifest in a slice rooted at the
stopped tip. The Codex-owned/Claude-reviewed repair lane uses that accepted preparation
commit as literal `scope_base`; its manifest records the stopped lane/tip and transferred
stable finding IDs. It is a new immutable lane, never an in-place role edit; the handoff
is one-way and none of the dependent ranges lands independently.

The generated `state/project-index.json` gives the operator and CLI one readable status
view without becoming a second source of truth:

```json
{
  "schema_version": 1,
  "project_id": "trading-ai-engine",
  "repository_head": "<full-sha>",
  "active": {
    "run_id": "<run-id>",
    "lane_id": "<lane-id>",
    "work_item": "ca_platform_design_phase9",
    "state": "PAUSED_LIMIT",
    "next_action": "RETRY_REVIEW",
    "next_retry_at": "<utc-timestamp>"
  },
  "work_items": [
    {
      "id": "ca_platform_design_phase9",
      "manifest": "ca_platform_design_phase9.yaml",
      "manifest_status": "EXISTS",
      "execution_status": "PAUSED_LIMIT",
      "last_verified_sha": "<full-sha>"
    },
    {
      "id": "ca_platform_design_phase10",
      "manifest": "ca_platform_design_phase10.yaml",
      "manifest_status": "MISSING",
      "execution_status": "BLOCKED_MISSING_MANIFEST"
    }
  ],
  "rebuilt_from_event": 42,
  "ledger_digest": "<sha256>"
}
```

The projection is atomically refreshed after accepted ledger events and can be rebuilt by
`assist status` or recovery. A stale or corrupt projection is replaced from verified
evidence; it never advances the reducer.

## 6. Agent and skill resolution

Every project explicitly defines its Claude and Codex role packages and skills. Shared
packages may be referenced as versioned bases, but there is no implicit global fallback.
Every project must explicitly reference the shared V1 safety package: never inspect,
print, return, or persist environment dumps, authentication files, tokens, or credentials;
return only the action's schema-defined structured fields and bounded evidence. The
orchestrator independently enforces the persistence boundary, so prompt compliance is not
the only control.
Engine work can therefore bind trading, provider, PostgreSQL, and operational rules while
UI work binds frontend, accessibility, browser, and UI-testing rules.

At lane authorization the orchestrator resolves and snapshots:

- project `INDEX.md` and configuration digest;
- target governing-file paths and digests;
- selected agent role package and skill paths, versions, and digests;
- target manifest snapshot and digest;
- lane/run policy and schema digests; and
- exact base SHA and target gate contract.

That immutable context package is supplied on every invocation. Session resume is only a
performance optimization. Updating a project package affects new lanes only unless the
operator records a typed amendment.

## 7. Start, status, retry, and resume

The first start is explicit; directory scanning never chooses work:

```text
assist projects
assist start --project trading-ai-engine --work-item ca_platform_design_phase9
```

The project configuration supplies the repository and manifest-root paths. The operator
therefore names a work-item ID, not a full repository path. `--next`, `--plan`, and
multi-phase start are not V1 commands; the CLI never guesses the next phase.

Because V1 allows exactly one globally active lane, ordinary commands need no ID:

```text
assist status
assist watch
assist retry-now
assist stop --reason <text>
assist resume
assist resume --retry-now
```

The CLI scans registered project-local `current.json` projections, requires exactly one
non-terminal lane, then validates its complete ledger before acting. Zero or multiple
active lanes is a fail-closed diagnostic. Explicit `--project`, `--run`, `--lane`, and
`--from-event` forms remain available for recovery and audit.

`retry-now` is valid only for a recoverable paused state. It appends a typed
`IMMEDIATE_RETRY_REQUESTED` command bound to the current ledger sequence, interrupts the
waiting timer, and retries the exact persisted step. If the limit or outage remains, the
normal schedule is restored without consuming a review round. Every newly spawned Claude
or Codex process—including that retry—does consume one of the lane's 40 agent invocations.

## 8. Process and lock model

V1 has one executing worker globally. A second `start` or executing `resume` fails with
the active project, run, lane, state, and safe monitor commands. Multiple `status`,
`watch`, `projects`, and `runs` processes may read concurrently; control commands append
typed command requests for the worker.

Each project has its own execution lock and state. A small global coordinator lock guards
the V1 single-worker invariant but contains no project execution state. Future concurrent
Engine/UI execution may be introduced only through one coordinator with per-project and
per-target-branch locks plus shared Claude/Codex subscription-limit coordination. It is
not enabled by removing the global lock or launching a second independent worker.

The target integration branch is an exclusive operator lease while a lane is active.
Assist checks its local and configured remote tip before every agent invocation, verify
effect, and land effect, so manual movement stops before more subscription capacity is
spent. It also writes an advisory marker under the target Git metadata and the target's
Claude/Codex instructions require interactive sessions to check it. The marker is not an
enforcement boundary; unexpected movement always fails closed and audited ranges are not
silently rebased.

## 9. Project state invariants

- Ledger append succeeds and is flushed before the next external effect.
- `current.json`, `project-index.json`, and `status.json` are atomic replacements and can
  always be rebuilt from ledgers plus immutable configuration.
- Resume never infers a successful side effect from a projection or process exit alone.
- Project IDs are immutable stable identifiers; display names and repository paths may
  change only through reviewed configuration.
- A project lock resolves to exactly one project state root and target branch.
- No credential, API key, monthly-plan token, CLI auth cache, or environment-file value is
  copied into project configuration or state.
- Structured JSON artifacts, the JSONL ledger, and Markdown briefs are retained locally
  without automatic expiry. Full raw CLI transcripts are never persisted. Optional
  diagnostic text is capped at 1 MiB per invocation. Temporary worktrees are cleaned up
  after safe completion/reconciliation and are not artifacts.
- Console/status events always identify project, work item, lane, agent, step, state,
  detected time, next retry, and the ready-to-run recovery command.
- `p3-backlog.json` is a derived lane artifact surfaced in the completion brief. Writing
  a target-repository backlog requires a separately manifested work item.
- Agent/verify children and the credentialed land worker are separate processes. Ledger
  events identify which process produced each gate or land result; V0 land events are
  local-only and cannot contain a remote push result.
