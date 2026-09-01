# trading-ai-assistance

A deterministic orchestrator — a human-assistance tool — that runs the Claude (author) ↔
Codex (reviewer) loop for a target repository until it converges, and calls the operator
in only at named human gates.

- **Not an LLM.** Transitions are pure functions of the target's deterministic gate, the
  reviewer's structured `review.json`, git facts, and counters.
- **No privileges.** Holds no model keys, database credentials, or provider keys; mounts
  a lane's grants into the agent sessions only.
- **Fail closed.** Anything unexpected is a STOP with a plain-language Human Gate Brief.

**New here? Read [`docs/HANDOFF_2026-08-29_ORCHESTRATOR_KICKOFF.md`](docs/HANDOFF_2026-08-29_ORCHESTRATOR_KICKOFF.md) first** — the problem, the rulings, a worked walkthrough of one lane, what changes for Codex, and a glossary.

Design authority: [`docs/design/ORCHESTRATED_EXECUTION_DESIGN.md`](docs/design/ORCHESTRATED_EXECUTION_DESIGN.md).
Implementation architecture: [`docs/design/DETERMINISTIC_PYTHON_APPLICATION_ARCHITECTURE.md`](docs/design/DETERMINISTIC_PYTHON_APPLICATION_ARCHITECTURE.md).
Review/repair protocol: [`docs/design/REVIEW_REPAIR_CONVERGENCE_PROTOCOL.md`](docs/design/REVIEW_REPAIR_CONVERGENCE_PROTOCOL.md).
Project Control Plane and final layout: [`docs/design/PROJECT_CONTROL_PLANE.md`](docs/design/PROJECT_CONTROL_PLANE.md).
Binding invariants: [`CLAUDE.md`](CLAUDE.md) · Codex notes: [`AGENTS.md`](AGENTS.md).
Current state: [`STATUS.md`](STATUS.md).

## Planned final layout

```
trading-ai-assistance/
├── CLAUDE.md / AGENTS.md / STATUS.md
├── docs/design/                     # governing design and architecture
├── projects/
│   ├── INDEX.md / registry.yaml     # deterministic project discovery
│   ├── trading-ai-engine/
│   │   ├── INDEX.md / project.yaml / work-index.json
│   │   ├── agents/ skills/ policies/ plans/
│   │   └── state/runs/              # project-local, generated, gitignored
│   └── trading-ai-ui/               # same isolated project boundary
├── shared/                           # explicitly referenced reusable packages
├── schemas/                          # published versioned agent contracts
├── manifests/                        # work manifests for THIS repository only
├── src/orchestrator/                 # deterministic application
└── tests/                            # no live agents/network/target repos in fast suite
```

The current scaffold still contains the proposed legacy `targets/trading-ai.yaml` and
top-level `runs/.gitkeep`; V0-A migrates those settings into the reviewed Project Control
Plane. No target-repository manifest is created by that migration.

V1 registers multiple projects but executes one lane globally. Normal control is therefore
simple:

```powershell
assist start --project trading-ai-engine --work-item <work-item-id>
assist status
assist retry-now
assist resume
```

V0 runs one phase/work item, locally fast-forwards the exact CLEAN/gate-accepted tree,
and stops before push. Bookkeeping is a separate operator-started run with a manifest
prepared against that landed tip. The next phase manifest is also prepared separately by Claude under operator authorization;
Assist and Codex never synthesize a missing manifest. Monitoring is console plus
`assist status`/`assist watch` only—no external notification transport.

The first phase therefore has four structured operator authorizations: prepare the phase
manifest, run the phase, prepare the bookkeeping manifest, and run bookkeeping. Automatic
push is a later V1 graduation after child credential isolation and three surprise-free
watched lanes are proven.

Each project retains its authoritative JSONL ledger, structured JSON artifacts, and
Markdown briefs under its gitignored `state/`. Full raw CLI transcripts are not retained;
optional diagnostic text is capped at 1 MiB per invocation. Dedicated dependency/secret
scanners, automatic retention cleanup, multi-phase plans, and Windows auto-start are
future work.

V0 caps each lane at 10 review rounds and 40 spawned Claude/Codex processes. Guidance is
non-authoring and contains no code. A persistent repair stops for a separately authorized,
one-way Codex-author/Claude-reviewer handoff lane; Codex advisory self-checks cannot gate.

## Setup

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q --tb=short
```

Requires `claude` (Claude Code) and `codex` (Codex CLI) on PATH for live lanes; the test
suite needs neither.
