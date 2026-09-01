# STATUS — trading-ai-assistance

**Updated:** 2026-08-31

| Item | State |
|---|---|
| Repository | scaffolded 2026-08-29 (this commit); no orchestrator code yet |
| Design | Governing design rev 3 plus Python architecture, Project Control Plane, and review/repair protocol; independent Claude findings and operator confirmations folded, awaiting final Codex review |
| Build plan | Pre-build: target RVA JSON contract first; then V0 walking skeleton. Codex CLI 0.151.0 and Claude 2.1.163 installed; exact-child subscription auth/smoke evidence pending; schemas v1 draft-only |
| First project | planned ID `trading-ai-engine` -> `c:\Repos\trading-ai`, branch `development`; no Phase 9 manifest exists, so no live lane is startable |
| Suite | 7 tests passing locally (`python -m pytest -q --tb=short -p no:cacheprovider`, 2026-08-31) |

## Review requests outstanding

- **Start with** `docs/HANDOFF_2026-08-29_ORCHESTRATOR_KICKOFF.md` (why, rulings, walkthrough, glossary).
- **Codex:** independently review the complete rev-3 fold, especially the RVA prerequisite,
  verify/land privilege split, cross-lane handoff composite landing, separate bookkeeping,
  walking-skeleton scope, and automatic-push graduation predicate.
- Also pending Codex review in `trading-ai`: branch `process/orchestrator-pointer-dev-ruling`
  (CLAUDE.md Dev-RW amendment + design pointer; parked off `development`).

## Open decisions (design §10)

ownership of T0/V0-A/V0-B/V0-C/V1 · template-database relocation (`trading-ai` side).

Ruled through 2026-08-31: Project Control Plane under `projects/`; project-specific agents/skills;
all runtime state project-local; one global V1 execution worker; subscription-only auth;
hourly/weekly/outage schedules; immediate retry; console/status monitoring only; no
12-hour recoverable-pause cutoff; one phase per run; no token/total-lane budget; P2 blocks;
V0 exact-tree local landing then separate bookkeeping; no dedicated scanner in V1; structured
artifacts retained without expiry, no raw transcripts, 1-MiB diagnostics; manual Windows
resume; Claude manifest preparation is separate and authorized; 40 agent invocations;
no-code guidance; one-way Codex-author/Claude-reviewer handoff; automatic push only after
privilege proof and three surprise-free watched lanes.

The application architecture records ruled pre-live obligations in
`docs/design/DETERMINISTIC_PYTHON_APPLICATION_ARCHITECTURE.md` §15.

## Prerequisites outstanding (design §8)

1. `trading-ai` delivers a separately authorized/reviewed versioned RVA JSON result that
   distinguishes docs-inconclusive/scope-pass from blocking/unknown results.
2. Codex CLI (`codex exec`) authenticated and smoke-tested in the exact restricted driver
   environment/`CODEX_HOME`; confirm schema enforcement, sandbox, resume, exit codes, and
   the exact usage-limit surface (design §5.5/§5.6 — eventually pinned into
   `projects/trading-ai-engine/policies/limits.yaml`).
3. `claude -p` headless smoke run with `--resume`, `--model`, `--effort`, a restricted
   tool allowlist, and capture of the `system/api_retry`
   stream event + the hourly/weekly usage-limit message text.
4. Version 2 author-result/review/fold contracts and the target gate-result contract
   reviewed; v1 remains draft-only. Obligations added to `trading-ai`'s `AGENTS.md` + `CLAUDE.md`
   (a `trading-ai` process slice).
5. `trading-ai` collaboration contract §1.2 amendment (operator).
6. `trading_ai_agent` login on Dev; provider env file for implementation lanes.
7. Implement and review the minimal Project Control Plane layout; migrate the proposed
   `targets/trading-ai.yaml` configuration into `projects/trading-ai-engine/`. Target
   manifests are prepared separately by Claude only after explicit operator authorization.
