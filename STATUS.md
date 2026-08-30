# STATUS — trading-ai-assistance

**Updated:** 2026-08-29

| Item | State |
|---|---|
| Repository | scaffolded 2026-08-29 (this commit); no orchestrator code yet |
| Design | `docs/design/ORCHESTRATED_EXECUTION_DESIGN.md` rev 1 — PROPOSED, operator rulings folded (Mode A; Dev RW; no dependency gate; own repo); awaiting Codex review |
| Build plan | M0 in progress (repo created; Codex CLI not yet installed; schemas drafted, unreviewed) |
| First target | `trading-ai` (`c:\Repos\trading-ai`, branch `development`); first live lane planned = CA design Phase 9 under Mode B while the operator watches (M1) |
| Suite | 0 tests |

## Review requests outstanding

- **Codex:** review the scaffold (`manifests/repo_scaffold.yaml`), the DRAFT schemas, the
  design rev 1, and answer the four questions in `docs/design/SECURITY_SCANNING_OPTIONS.md`
  (security scanning = phase 2, approach undecided).
- Also pending Codex review in `trading-ai`: branch `process/orchestrator-pointer-dev-ruling`
  (CLAUDE.md Dev-RW amendment + design pointer; parked off `development`).

## Open decisions (design §10)

`max_rounds` and budgets · P2-on-design-docs rule · landing policy · ownership of M0–M3 ·
template-database relocation (`trading-ai` side) · whether this repo gets push-time scanning.

## Prerequisites outstanding (design §8)

1. Codex CLI (`codex exec`) installed and smoke-tested in a read-only sandbox.
2. `claude -p` headless smoke run with `--resume` and a restricted tool allowlist.
3. `review.json` / `fold.json` obligations added to `trading-ai`'s `AGENTS.md` + `CLAUDE.md`
   (a `trading-ai` process slice).
4. `trading-ai` collaboration contract §1.2 amendment (operator).
5. `trading_ai_agent` login on Dev; provider env file for implementation lanes.
