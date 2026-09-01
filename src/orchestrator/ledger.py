"""Append-only, digest-chained JSONL run ledger with replay.

The ledger is the sole authoritative runtime history of a run (architecture
§9.1). Each line carries a strictly increasing sequence number, the digest of
the previous line, and its own digest over the canonical JSON of everything
else. Appends are flushed to disk before the next external effect begins.

Replay validates the complete chain and re-runs the pure reducer over every
recorded DECISION event, proving that the recorded transitions are exactly
what the reducer decides today — determinism, not trust.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from orchestrator.model import LanePolicy, LaneSnapshot, event_from_dict
from orchestrator.reducer import reduce

GENESIS_DIGEST = "0" * 64

KIND_DECISION = "DECISION"
KIND_EFFECT = "EFFECT"


class LedgerError(Exception):
    """The ledger failed integrity validation. Fail closed; never guess."""


class LedgerReplayError(LedgerError):
    """Replaying the recorded events does not reproduce the recorded states."""


def _canonical(data: Mapping[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    kind: str  # DECISION | EFFECT
    lane_id: str
    ts: str  # UTC ISO-8601; recorded evidence, never a reducer input
    payload: Mapping[str, Any]
    prev_digest: str
    digest: str

    def body(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "lane_id": self.lane_id,
            "ts": self.ts,
            "payload": self.payload,
            "prev_digest": self.prev_digest,
        }

    @staticmethod
    def compute_digest(body: Mapping[str, Any]) -> str:
        return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


class Ledger:
    """Appends entries to one JSONL file; never rewrites or deletes."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._seq = 0
        self._last_digest = GENESIS_DIGEST
        if path.exists():
            entries = read_entries(path)
            if entries:
                self._seq = entries[-1].seq
                self._last_digest = entries[-1].digest

    @property
    def path(self) -> Path:
        return self._path

    def append(self, kind: str, lane_id: str, ts: str, payload: Mapping[str, Any]) -> LedgerEntry:
        body = {
            "seq": self._seq + 1,
            "kind": kind,
            "lane_id": lane_id,
            "ts": ts,
            "payload": payload,
            "prev_digest": self._last_digest,
        }
        digest = LedgerEntry.compute_digest(body)
        entry = LedgerEntry(digest=digest, **body)
        line = _canonical({**body, "digest": digest})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._seq = entry.seq
        self._last_digest = digest
        return entry


def read_entries(path: Path) -> list[LedgerEntry]:
    """Read and integrity-check the complete chain. Any defect raises."""
    entries: list[LedgerEntry] = []
    prev_digest = GENESIS_DIGEST
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                raise LedgerError(f"line {line_no}: blank line in ledger")
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"line {line_no}: not valid JSON: {exc}") from None
            expected_keys = {"seq", "kind", "lane_id", "ts", "payload", "prev_digest", "digest"}
            if set(raw) != expected_keys:
                raise LedgerError(f"line {line_no}: unexpected ledger fields {sorted(set(raw))}")
            entry = LedgerEntry(
                seq=raw["seq"],
                kind=raw["kind"],
                lane_id=raw["lane_id"],
                ts=raw["ts"],
                payload=raw["payload"],
                prev_digest=raw["prev_digest"],
                digest=raw["digest"],
            )
            if entry.seq != line_no:
                raise LedgerError(
                    f"line {line_no}: sequence {entry.seq} out of order (expected {line_no})"
                )
            if entry.prev_digest != prev_digest:
                raise LedgerError(f"line {line_no}: previous-digest chain broken")
            if LedgerEntry.compute_digest(entry.body()) != entry.digest:
                raise LedgerError(f"line {line_no}: digest mismatch (line rewritten?)")
            prev_digest = entry.digest
            entries.append(entry)
    return entries


def replay(path: Path, policy: LanePolicy) -> LaneSnapshot:
    """Validate the chain, then re-reduce every DECISION and compare states.

    Returns the final snapshot proved by replay. The recorded ``state_after``,
    ``reason``, and ``command`` of every decision must equal what the reducer
    produces from the recorded event — otherwise the ledger and the reducer
    disagree and the run is not trustworthy (``LedgerReplayError``).
    """
    snapshot = LaneSnapshot()
    for entry in read_entries(path):
        if entry.kind != KIND_DECISION:
            continue
        payload = entry.payload
        event = event_from_dict(payload["event"])
        if payload["state_before"] != snapshot.state.value:
            raise LedgerReplayError(
                f"seq {entry.seq}: recorded state_before {payload['state_before']!r} "
                f"!= replayed {snapshot.state.value!r}"
            )
        decision = reduce(snapshot, event, policy)
        recorded_command = payload.get("command")
        replayed_command = decision.command.kind if decision.command is not None else None
        if (
            payload["state_after"] != decision.snapshot.state.value
            or payload["reason"] != decision.reason.value
            or recorded_command != replayed_command
        ):
            raise LedgerReplayError(
                f"seq {entry.seq}: recorded transition "
                f"({payload['state_after']}, {payload['reason']}, {recorded_command}) "
                f"!= replayed ({decision.snapshot.state.value}, "
                f"{decision.reason.value}, {replayed_command})"
            )
        snapshot = decision.snapshot
    return snapshot
