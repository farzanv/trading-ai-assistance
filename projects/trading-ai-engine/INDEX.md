# trading-ai-engine — project orientation

**Target:** the `trading-ai` engine repository at `c:\Repos\trading-ai`,
integration branch `development`, manifests under `dil-engine/manifests/`.

**Objective.** Drive the Claude-author / Codex-reviewer loop for `trading-ai`
work items (design phases, implementation slices, bookkeeping) to the
deterministic convergence predicate, calling the operator only at named human
gates. This project's configuration was migrated in V0-A from the legacy
`targets/trading-ai.yaml` proposal.

**No live lane is startable yet.** Prerequisite T0 — a versioned,
machine-readable RVA JSON result from `trading-ai` — is not delivered, and the
`gate.contract_status` in `project.yaml` is `BLOCKED_UNTIL_T0`. Nothing in this
project parses the current human RVA output.

## Required reading order (agents)

1. This file.
2. The target's governing rules (binding inside every lane): `CLAUDE.md`,
   `AGENTS.md`, `dil-engine/docs/process/COLLABORATION_CONTRACT.md`,
   `dil-engine/docs/process/DECISIONS.md` — all in the target repository.
3. This repository's `docs/design/REVIEW_REPAIR_CONVERGENCE_PROTOCOL.md`
   (finding lifecycle, guidance, handoff).
4. The role package and skill named by the invocation envelope (under
   `agents/` and `skills/` here), plus `../../shared/policies/safety-v1.md`.

## Terminology

- **Lane** — one authorized work item bound to one target manifest.
- **RVA** — the target's Release Verification Agent
  (`dil-engine/scripts/verify_release.py`), the deterministic gate.
- **Dev / Test** — `trading_ai_v4` (agents' shared RW validation DB, manual
  runs only; ruling 2026-08-29) / `trading_ai_v4_test` (operator-only).

## Boundaries

- The orchestrator never edits target content; only a converged slice lands,
  locally and fast-forward-only in V0, stopping before push.
- Design lanes grant no DB, provider, or web access; implementation lanes
  mount the lane's exact grants into the agent session only.
- All runtime state is under `state/` (gitignored); the run ledger is the sole
  authoritative history.
