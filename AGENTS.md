# Codex Working Notes — trading-ai-assistance

Start with `docs/HANDOFF_2026-08-29_ORCHESTRATOR_KICKOFF.md` (the why, the rulings, a
worked walkthrough, glossary), then `CLAUDE.md` (binding invariants) and
`docs/design/ORCHESTRATED_EXECUTION_DESIGN.md` (design authority, operator-ruled). This repository is a deterministic orchestrator — a
human-assistance tool — that drives the Claude ↔ Codex loop for target repositories.
It is not an LLM agent and must never become one.

## Codex's role here

- **Non-authoring reviewer** of every normal slice (Claude authors by default). In a
  separately authorized one-way handoff lane Codex is author and Claude is reviewer;
  Codex self-checks are advisory and never gating. Findings-first,
  severity P0–P3 per the target's collaboration contract, bounded rounds.
- Review posture: determinism of transitions, fail-closed behaviour on every malformed or
  missing input, ledger completeness, privilege minimalism (no secret ever enters the
  orchestrator process, log, or ledger), correctness of human-gate detection against the
  target manifest schema, and the landing rule (fast-forward only, digest-bound).
- Enforce the Project Control Plane boundary: registered project configuration and
  agent/skill packages are explicit and digest-bound; every runtime record is under that
  project's gitignored `state/`; V1 has one global executing lane; no directory scan or
  generated projection may silently choose work or recovery state.
- When the orchestrator runs its own loop on this repository, Codex is invoked headlessly
  (`codex exec`, read-only sandbox) and must emit `review.json` per `schemas/review.schema.json`
  in addition to its prose review. A malformed `review.json` is retried once, then STOPs
  the lane.

## Structured review contract (v1 draft; v2 required before a live lane)

The checked-in v1 schemas are test scaffolding and cannot govern a live lane. Version 2
must add historical-finding reconciliation, lifecycle outcomes,
`review_kind`, `lens: gating | guidance | advisory`, and handoff
provenance. Once v2 is reviewed, every gating review produces:
1. Prose findings (as today).
2. `review.json` — `reviewed_range`, `tree_digest`, `verdict` (`CLEAN` | `FINDINGS`),
   `findings[]` with stable ids across rounds (`id` reused when a finding is reopened),
   `severity`, `section`, `required_change`, and the flags `requires_ruling`,
   `earlier_phase_gap`, `blocks_downstream`, `unknown_contract`; `scope_observations[]`
   (any entry is a STOP); the `security` checklist block for code slices;
   `dependencies_added[]` naming any new pinned library (informational, never a gate).

`CLEAN` means no P0–P2 and is valid only with `lens: gating`. P3s go to the project-local
lane artifact `p3-backlog.json`, surfaced in the completion brief, not the fold loop.

## Boundaries Codex must enforce in review

- No LLM call on a transition path. No prose parsing. No "latest" inference anywhere.
- No target-repo write except V0's local fast-forward of a converged slice. No V0 push.
- No credential, env file content, or agent CLI config in any log, ledger, brief, or test
  fixture (both agents' configs contain connection strings with passwords).
- Human gates are derived from the target manifest/diff (design §3.3); a gate rule
  implemented as a hard-coded phase list or target-specific migration/deployment path in
  generic source is a P1. Project policy supplies those globs.
- Ledger append-only and digest-chained; a rewrite path is a P0.
- A shared mutable runtime `runs/` store, cross-project state path, implicit global skill,
  uncontrolled second worker, API-key billing fallback, or recoverable-limit pause that
  consumes a review round is a blocking defect.
- V1 must not add a dedicated scanner, external notification transport, token/total-lane
  budget, multi-phase plan, or automatic manifest bootstrap. Enforce structured
  persistence safety, console/status monitoring, max-rounds/finding controls, and
  separately authorized Claude manifest preparation instead.
- A missing machine-readable RVA result, stale integration tip before any agent/gate/land
  effect, agent invocation above 40, model-authored execution in the land process,
  enabled Git hooks during landing, or push capability in an agent/verify child is blocking.
- GUIDANCE explains and tests but supplies no patch/replacement code. Persistent failure
  stops for the separately manifested Codex-author/Claude-reviewer handoff lane.

## Deployment reality

Runs on the operator's Windows machine against local checkouts; the Linux Test server is
not visible from here and is never a target of orchestrator action. `claude` is on PATH;
Codex CLI 0.151.0 is installed, but `assist doctor` must prove subscription auth and
structured-output/sandbox behavior inside the exact restricted child environment.
