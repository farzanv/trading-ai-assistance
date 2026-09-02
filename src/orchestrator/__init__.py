"""trading-ai-assistance — deterministic orchestrator for the author/review loop.

Package layout (design: docs/design/DETERMINISTIC_PYTHON_APPLICATION_ARCHITECTURE.md §12):

- ``model``       — frozen states, findings, events, commands, policy, decisions.
- ``reducer``     — pure transitions, finding lifecycle, counters; unspecified
                    (state, event) pairs fail closed to STOPPED.
- ``artifacts``   — the four v2 external-schema validators, exact-set
                    reconciliation, and persistence safety.
- ``ledger``      — append-only, digest-chained JSONL ledger with replay.
- ``project``     — Project Control Plane loading with containment and digests.
- ``agents``      — driver interfaces (V0-A: interfaces only; fakes in tests).
- ``application`` — the lane coordinator loop; no transition judgement.

V0-B adds real CLI drivers, the verify/land process split, recovery, limits,
console monitoring, and the ``assist`` control commands.

Nothing here calls a model. Nothing here holds a credential.
"""

from __future__ import annotations

__version__ = "0.1.0"
