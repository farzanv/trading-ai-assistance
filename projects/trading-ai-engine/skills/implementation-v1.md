# Skill: implementation lane v1 (trading-ai-engine)

The reusable protocol for an implementation work item (code slice). Selected
explicitly by the invocation envelope; never inferred.

- Tools per lane policy: file edit, git, pytest, pyflakes, plus the lane's
  exact grants — Dev database (`trading_ai_v4`, shared RW, manual execution
  only; ruling 2026-08-29) and the provider env file (mounted, never printed,
  never persisted).
- Announce, then execute, then evidence: every Dev execution is declared
  (command, `--asof`, dry-run or real) before it runs and its run id and
  row-count deltas recorded after.
- "Ready for Test" is machine-checked before the human gate: migrations
  applied on Dev, `--dry-run` proven zero-write, one real run, a convergent
  re-run, and the non-authoring agent's row inspection recorded in the review
  artifact.
- Provider traffic is metered per the manifest's declared budget; exceeding it
  is a STOP. Paper-trading keys only.
- The operator gate (deploy to Test, dry-run, real run, RVA pre/post, approve)
  is mandatory and detected from the manifest and diff, never remembered.
