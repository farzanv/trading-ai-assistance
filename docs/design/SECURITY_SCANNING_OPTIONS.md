# Security scanning — options for phase 2 (OPEN, for Codex + operator)

**Status:** PHASE 2, not decided (operator, 2026-08-29). The orchestrator loop carries no
dependency gate and no scanner (design §7); library vetting and vulnerability scanning happen
*outside* the loop. This note records the options and the analysis so the choice can be
made with Codex's input. Nothing here is implemented.

## The concern

"Whatever we pick must be able to check the Python project and its dependencies, and check
them again later" — i.e. a library that was fine when an agent added it must still be caught
when an advisory appears months later, and the check must reflect what is *actually
installed* on Test, not just what a file declares.

## Fact that shapes the choice: the target's requirements are mostly unpinned

`trading-ai/dil-engine/requirements.txt` (2026-08-29) pins only `alpaca-py==0.43.5`; the other
nine entries (`httpx`, `requests`, `PyYAML`, `pyarrow`, `psycopg[binary]`, `pytest`, `pytz`,
`tzdata`) are bare names. Consequences:

- Any manifest-based scanner (GitHub Dependabot included) cannot know the installed version
  and cannot see transitive dependencies (`alpaca-py` alone pulls `pydantic`, `websockets`,
  `msgpack`, …). Its alerts are approximate.
- The Test install is not reproducible from the file, which also weakens the design's
  "reviewed code = deployed code" rule (§7.2) for third-party code.

A lock file (`pip freeze` from a validated venv → `requirements.lock`, installed with
`pip install -r requirements.lock`) fixes both, and is a prerequisite for *any* scanner to
be exact. Recommended regardless of the scanner choice.

## Option A — GitHub Dependabot alerts (remote, passive)

- Supports Python manifests in subdirectories (dependency graph auto-detects
  `dil-engine/requirements.txt`); automated update PRs would need
  `.github/dependabot.yml` with `directory: /dil-engine`.
- **Re-checks continuously**: new advisories re-evaluate existing dependency graphs, not
  only on push.
- Free on private repos. Zero maintenance. Email notification.
- Limits: declaration-based (see above); no transitive visibility without a lock file; no
  secret scanning or CodeQL without the paid GitHub Advanced Security add-ons on private
  repos (pricing to be verified by the operator — not checkable from here).

## Option B — local open-source scanners (precise, operator-run)

- `pip-audit` inside the **actual venv**: audits what is installed, including transitive
  dependencies, against the same advisory databases (PyPI Advisory DB / OSV). Runs in
  seconds; only advisory lookups leave the machine. Can run locally before push, on the
  Test server against its venv after each deploy, and on a schedule.
- `gitleaks` (or `detect-secrets`) as a pre-push hook: secret scan on every push, no account
  needed. (The orchestrator keeps its own secret scan as a hard STOP for slices it lands —
  design §7.1; the hook covers hand-made commits.)
- Optional later: `bandit` for static security lint of changed files.
- Limits: someone must run it (hooks + a scheduled task make that automatic); no central
  dashboard.

## Option C — hybrid (Claude's recommendation, 2026-08-29)

1. Pin: lock file in `trading-ai` (small reviewed slice).
2. Primary: local `pip-audit` + `gitleaks` pre-push hooks in both repos; `pip-audit` on Test
   after each deploy as an operator command.
3. Backstop: Dependabot alerts enabled on both GitHub repos for continuous re-checking
   between local runs.

Rationale: B answers the "check the real installation, again and again" concern exactly;
A costs nothing and covers the gap between local runs; the lock file makes both exact.

## Questions for Codex

1. Agree the lock file is a prerequisite for either option, and that it belongs in
   `trading-ai` as its own reviewed slice (with the Test deploy runbook switching to
   `requirements.lock`)?
2. Is a pre-push hook the right enforcement point for `pip-audit`/`gitleaks`, or should
   the orchestrator run them post-landing as *informational* (never a gate) and surface
   results in the Human Gate Brief?
3. Any objection to Dependabot on private repos given the repos' contents (no secrets are
   committed; `.env` is gitignored in both)?
4. Anything in this analysis that is wrong for the Windows/Linux-Test split?
