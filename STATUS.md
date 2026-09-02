# STATUS — trading-ai-assistance

**Updated:** 2026-09-01

| Item | State |
|---|---|
| Repository | V0-A walking skeleton rev 3 DELIVERED 2026-09-01 (manifest `manifests/v0a_walking_skeleton.yaml`) — Codex rev-2 review folded (R1-01/05/06 repaired, R2-01/02/03 folded); awaiting Codex re-review |
| Design | Governing design rev 3 (`103020f`, operator-approved architecture baseline for implementation) plus Python architecture, Project Control Plane, and review/repair protocol |
| Build plan | T0 (target RVA JSON contract) still outstanding on the `trading-ai` side; V0-A done pending review; V0-B (real drivers, verify/land split, recovery, limits, console, `assist` commands) next |
| First project | `projects/trading-ai-engine/` registered (migrated from the legacy `targets/trading-ai.yaml`, now deleted) -> `c:\Repos\trading-ai`, branch `development`; gate `BLOCKED_UNTIL_T0`; no Phase 9 manifest exists, so no live lane is startable |
| Schemas | Four v2 external contracts published (`schemas/v2/`: author-result, fold, review, target-gate-result); v1 drafts retained as fixtures under `schemas/v1/` |
| Suite | 262 tests passing locally (`python -m pytest -q --tb=short`, 2026-09-01); pyflakes clean; fakes only — no CLI, network, or target repo |

## V0-A slice contents (awaiting review)

- Project Control Plane: `projects/INDEX.md`, `registry.yaml`,
  `trading-ai-engine/` (INDEX, project.yaml, work-index, agent/skill packages,
  lanes/limits/monitoring/persistence policies), `shared/policies/safety-v1.md`.
- Deterministic core: `src/orchestrator/model.py` (frozen domain), `reducer.py`
  (pure transitions, finding lifecycle, counters; unspecified pairs fail closed),
  `artifacts.py` (v2 validation, exact-set reconciliation, persistence safety),
  `ledger.py` (append-only digest-chained JSONL + replay), `project.py`
  (containment + digests), `agents.py` (driver interfaces only),
  `application.py` (coordinator shell; run ends at convergence — no landing).
- Tests: reducer table incl. exhaustive fail-closed sweep, finding lifecycle
  (guidance/handoff/rejection/adjudication/ping-pong), artifact reconciliation
  and safety, ledger tamper/replay, project isolation, and the simulated
  author → review → repair → no-code guidance → final review → convergence lane.

## Review requests outstanding

- **Codex:** re-review the V0-A slice `103020f..<tip>` against
  `manifests/v0a_walking_skeleton.yaml`. Rev 3 folds the rev-2 review:
  R1-01 — Git-driver-corroborated candidates (existence, tree, descent from
  scope_base AND the previous accepted candidate; rollback regression);
  R1-05 — the lane policy is resolved from the registered project's lanes
  policy for the declared work-item kind (never caller-supplied) and the
  LANE_OPENED binding adds config/work-index digests; R1-06 — replay
  re-validates every accepted artifact in the replayed lane context and
  requires it to derive exactly the ledgered typed event, and verifies
  caller-supplied identity/policy/schema/package context; R2-01 — gate-origin
  blockers are back in the reviewer's exact prior-finding set (gate result as
  closure evidence); R2-02 — review_kind, reviewer/author agent, and handoff
  provenance are bound to the lane (author self-review structurally cannot
  gate); R2-03 — every failed security item must name its own open blocking
  finding (schema + semantic, no hitchhiking).
- Still pending from before: final Codex review of the rev-3 design fold, and
  the `trading-ai` branch `process/orchestrator-pointer-dev-ruling`.

## Open decisions (design §10)

ownership of T0/V0-B/V0-C/V1 · template-database relocation (`trading-ai` side).
Everything ruled through 2026-08-31 stands (see design §10 and CLAUDE.md).

## Prerequisites outstanding (design §8)

1. `trading-ai` delivers the versioned RVA JSON result (T0) — blocking for any live lane.
2. Codex CLI exact-child auth/smoke evidence (schema, sandbox, resume, exit codes,
   usage-limit surface → pin into `projects/trading-ai-engine/policies/limits.yaml`).
3. `claude -p` headless smoke run (`--resume`, `--model`, `--effort`, tool allowlist,
   `system/api_retry` + usage-limit text capture).
4. v2 contract obligations added to `trading-ai`'s `AGENTS.md`/`CLAUDE.md`
   (a `trading-ai` process slice); v2 schemas here are published but unreviewed.
5. `trading-ai` collaboration contract §1.2 amendment (operator).
6. `trading_ai_agent` login on Dev; provider env file for implementation lanes.
