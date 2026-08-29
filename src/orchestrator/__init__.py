"""trading-ai-assistance — deterministic orchestrator for the author/review loop.

Package layout (design: docs/design/ORCHESTRATED_EXECUTION_DESIGN.md):

- ``state``    — lane states, transitions, and the convergence / stop predicates (§3).
- ``gates``    — human-gate detection from a target manifest + diff (§3.3).
- ``artifacts``— schema validation of review.json / fold.json against ``schemas/``.
- ``drivers``  — thin subprocess wrappers for ``claude -p`` and ``codex exec`` behind an
                 interface, so tests use fakes and never spawn a CLI.
- ``target``   — target-repository adapter: git facts, RVA invocation, landing (ff + push).
- ``ledger``   — append-only, digest-chained run ledger (§5.3).
- ``brief``    — Human Gate Brief rendering (§3.4).
- ``cli``      — ``orchestrate`` entry point.

Nothing here calls a model. Nothing here holds a credential.
"""

from __future__ import annotations

__version__ = "0.0.0"
