# Codex Working Notes — trading-ai-assistance

Start with `docs/HANDOFF_2026-08-29_ORCHESTRATOR_KICKOFF.md` (the why, the rulings, a
worked walkthrough, glossary), then `CLAUDE.md` (binding invariants) and
`docs/design/ORCHESTRATED_EXECUTION_DESIGN.md` (design authority, operator-ruled). This repository is a deterministic orchestrator — a
human-assistance tool — that drives the Claude ↔ Codex loop for target repositories.
It is not an LLM agent and must never become one.

## Codex's role here

- **Non-authoring reviewer** of every slice (Claude authors by default). Findings-first,
  severity P0–P3 per the target's collaboration contract, bounded rounds.
- Review posture: determinism of transitions, fail-closed behaviour on every malformed or
  missing input, ledger completeness, privilege minimalism (no secret ever enters the
  orchestrator process, log, or ledger), correctness of human-gate detection against the
  target manifest schema, and the landing rule (fast-forward only, digest-bound).
- When the orchestrator runs its own loop on this repository, Codex is invoked headlessly
  (`codex exec`, read-only sandbox) and must emit `review.json` per `schemas/review.schema.json`
  in addition to its prose review. A malformed `review.json` is retried once, then STOPs
  the lane.

## Structured review contract (binding once the loop is live)

Every review produces:
1. Prose findings (as today).
2. `review.json` — `reviewed_range`, `tree_digest`, `verdict` (`CLEAN` | `FINDINGS`),
   `findings[]` with stable ids across rounds (`id` reused when a finding is reopened),
   `severity`, `section`, `required_change`, and the flags `requires_ruling`,
   `earlier_phase_gap`, `blocks_downstream`, `unknown_contract`; `scope_observations[]`
   (any entry is a STOP); the `security` checklist block for code slices;
   `dependencies_added[]` naming any new pinned library (informational, never a gate).

`CLEAN` means no P0–P2. P3s go to the backlog file, not the fold loop.

## Boundaries Codex must enforce in review

- No LLM call on a transition path. No prose parsing. No "latest" inference anywhere.
- No target-repo write except a fast-forward landing of a converged slice.
- No credential, env file content, or agent CLI config in any log, ledger, brief, or test
  fixture (both agents' configs contain connection strings with passwords).
- Human gates are derived from the target manifest/diff (design §3.3); a gate rule
  implemented as a hard-coded phase list is a P1.
- Ledger append-only and digest-chained; a rewrite path is a P0.

## Deployment reality

Runs on the operator's Windows machine against local checkouts; the Linux Test server is
not visible from here and is never a target of orchestrator action. `claude` is on PATH;
`codex` CLI is not yet installed (desktop app only) — an M0 prerequisite, not a defect.
