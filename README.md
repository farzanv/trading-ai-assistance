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
Binding invariants: [`CLAUDE.md`](CLAUDE.md) · Codex notes: [`AGENTS.md`](AGENTS.md).
Current state: [`STATUS.md`](STATUS.md).

## Layout

```
trading-ai-assistance/
├── CLAUDE.md / AGENTS.md / STATUS.md
├── docs/design/ORCHESTRATED_EXECUTION_DESIGN.md   # operator-ruled design (rev 1)
├── schemas/                 # review.schema.json, fold.schema.json — the published agent contract
├── targets/                 # one YAML per target repository (path, branch, gate command)
├── manifests/               # work-item manifests for THIS repository's own changes
├── src/orchestrator/        # state machine, drivers, gate runner, ledger, briefs, cli
├── tests/                   # unit suite; never invokes real claude/codex or a real target
└── runs/                    # per-target, per-lane ledgers + briefs (gitignored except .gitkeep)
```

## Setup

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q --tb=short
```

Requires `claude` (Claude Code) and `codex` (Codex CLI) on PATH for live lanes; the test
suite needs neither.
