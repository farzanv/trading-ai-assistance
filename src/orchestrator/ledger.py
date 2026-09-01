"""Append-only, digest-chained JSONL run ledger with full-evidence replay.

The ledger is the sole authoritative runtime history of a run (architecture
§9.1). Each line carries a strictly increasing sequence number, the digest of
the previous line, and its own digest over the canonical JSON of everything
else. Appends are flushed to disk before the next external effect begins.
The event vocabulary is closed: unknown kinds or effect names fail validation.

Replay proves more than chain integrity: it re-runs the pure reducer over
every recorded DECISION and compares the COMPLETE decision (snapshot, reason,
command, predicate inputs), enforces the effect protocol around every decision
(invocation planned -> artifact accepted/rejected), re-verifies every accepted
artifact file against its ledgered digest, and binds the ledger to the exact
lane policy digest it was opened with. A tamperer who recomputes the whole
digest chain still cannot forge a transition, an input, or an artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from orchestrator.model import (
    LanePolicy,
    LaneSnapshot,
    event_from_dict,
    policy_digest,
    snapshot_to_dict,
)
from orchestrator.reducer import reduce

GENESIS_DIGEST = "0" * 64

KIND_DECISION = "DECISION"
KIND_EFFECT = "EFFECT"

_ENTRY_KINDS = frozenset({KIND_DECISION, KIND_EFFECT})
_EFFECT_NAMES = frozenset(
    {"LANE_OPENED", "INVOCATION_PLANNED", "ARTIFACT_ACCEPTED", "ARTIFACT_REJECTED", "RUN_END"}
)
_DECISION_KEYS = frozenset(
    {"event", "state_before", "state_after", "reason", "command", "inputs", "snapshot_after"}
)
_LANE_OPENED_KEYS = frozenset(
    {
        "effect",
        "run_id",
        "project_id",
        "work_item",
        "manifest",
        "scope_base",
        "lane_kind",
        "policy_digest",
        "package_digests",
        "schemas_digest",
    }
)
_AGENT_COMMANDS = frozenset({"InvokeAuthor", "InvokeReviewer", "InvokeGuidance"})
_TERMINAL_COMMANDS = frozenset({None, "OpenHumanGate", "LandRevision"})

#: Accepted-event kind -> the artifact kind that must precede it.
_EVENT_ARTIFACT_KIND = {
    "AuthorResultAccepted": "author-result",
    "FoldAccepted": "fold",
    "RevisionVerified": "target-gate-result",
    "ReviewAccepted": "review",
    "GuidanceAccepted": "guidance",
}
#: ArtifactRejected awaiting value -> the artifact kind of the rejection.
_AWAITING_ARTIFACT_KIND = {
    "AUTHOR_RESULT": "author-result",
    "FOLD": "fold",
    "GATE_RESULT": "target-gate-result",
    "REVIEW": "review",
    "GUIDANCE": "guidance",
}


class LedgerError(Exception):
    """The ledger failed integrity validation. Fail closed; never guess."""


class LedgerReplayError(LedgerError):
    """Replaying the recorded evidence does not reproduce the recorded run."""


def _canonical(data: Mapping[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _norm(data: Any) -> Any:
    """JSON-normalize for comparison (tuples become lists, keys ordered)."""
    return json.loads(json.dumps(data, sort_keys=True))


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
            if raw["kind"] not in _ENTRY_KINDS:
                raise LedgerError(f"line {line_no}: unknown entry kind {raw['kind']!r}")
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


def _artifact_file_digest(path: Path) -> str:
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerReplayError(f"accepted artifact unreadable: {path.name}: {exc}") from None
    return hashlib.sha256(_canonical(raw).encode("utf-8")).hexdigest()


def _check_effect_gap(
    seq: int,
    gap: list[LedgerEntry],
    prev_command: str | None,
    event_payload: Mapping[str, Any],
    invocations_after_prev: int,
    artifacts_dir: Path | None,
) -> None:
    """The effects between two decisions must match the issued command and the
    event the next decision consumed — invocation planned, then exactly one
    accepted (digest-bound) or rejected artifact of the right kind."""
    event_kind = event_payload["kind"]
    if prev_command is None:
        if gap or event_kind != "LaneAuthorized":
            raise LedgerReplayError(f"seq {seq}: unexpected evidence before lane authorization")
        return
    if len(gap) != 2:
        raise LedgerReplayError(
            f"seq {seq}: expected invocation+artifact evidence for {prev_command}, "
            f"found {len(gap)} effect(s)"
        )
    planned, outcome = gap[0].payload, gap[1].payload
    if planned.get("effect") != "INVOCATION_PLANNED":
        raise LedgerReplayError(f"seq {seq}: missing INVOCATION_PLANNED for {prev_command}")
    counted = prev_command in _AGENT_COMMANDS
    if bool(planned.get("counted")) != counted:
        raise LedgerReplayError(f"seq {seq}: invocation counted flag disagrees with {prev_command}")
    if counted and planned.get("invocation_number") != invocations_after_prev:
        raise LedgerReplayError(
            f"seq {seq}: invocation number {planned.get('invocation_number')} != "
            f"counter {invocations_after_prev}"
        )
    if event_kind == "ArtifactRejected":
        expected_kind = _AWAITING_ARTIFACT_KIND[event_payload["artifact"]]
        if outcome.get("effect") != "ARTIFACT_REJECTED" or outcome.get("artifact") != expected_kind:
            raise LedgerReplayError(f"seq {seq}: rejected artifact evidence missing or wrong kind")
        return
    expected_kind = _EVENT_ARTIFACT_KIND.get(event_kind)
    if expected_kind is None:
        raise LedgerReplayError(f"seq {seq}: no artifact contract for event {event_kind}")
    if outcome.get("effect") != "ARTIFACT_ACCEPTED" or outcome.get("artifact") != expected_kind:
        raise LedgerReplayError(f"seq {seq}: accepted artifact evidence missing or wrong kind")
    if artifacts_dir is not None:
        recorded = outcome.get("digest")
        actual = _artifact_file_digest(artifacts_dir / outcome["path"])
        if recorded != actual:
            raise LedgerReplayError(
                f"seq {seq}: artifact {outcome['path']} digest does not match the ledger"
            )


def replay(path: Path, policy: LanePolicy, artifacts_dir: Path | None = None) -> LaneSnapshot:
    """Validate the chain and replay the COMPLETE recorded run.

    Checks, in order: the LANE_OPENED binding (including the policy digest),
    the effect protocol around every decision, every accepted artifact's file
    digest, and — for every DECISION — that re-running the pure reducer over
    the recorded event reproduces the recorded snapshot, reason, command, and
    predicate inputs exactly. Returns the final proved snapshot.

    ``artifacts_dir`` defaults to the coordinator's layout next to the ledger;
    pass ``artifacts_dir=None`` explicitly only when files are intentionally
    absent (not applicable to coordinator-produced runs).
    """
    entries = read_entries(path)
    if not entries:
        raise LedgerReplayError("empty ledger")
    opened = entries[0]
    if opened.kind != KIND_EFFECT or opened.payload.get("effect") != "LANE_OPENED":
        raise LedgerReplayError("ledger does not begin with LANE_OPENED")
    if set(opened.payload) != _LANE_OPENED_KEYS:
        raise LedgerReplayError("LANE_OPENED binding is incomplete")
    if opened.payload["policy_digest"] != policy_digest(policy):
        raise LedgerReplayError("ledger was opened under a different lane policy")
    if artifacts_dir is None:
        artifacts_dir = path.parent / "lanes" / opened.lane_id / "artifacts"

    snapshot = LaneSnapshot()
    prev_command: str | None = None
    gap: list[LedgerEntry] = []
    saw_run_end = False
    decision_count = 0
    last_decision_command: str | None = None
    for entry in entries[1:]:
        if saw_run_end:
            raise LedgerReplayError(f"seq {entry.seq}: evidence after RUN_END")
        if entry.kind == KIND_EFFECT:
            effect = entry.payload.get("effect")
            if effect not in _EFFECT_NAMES:
                raise LedgerReplayError(f"seq {entry.seq}: unknown effect {effect!r}")
            if effect == "LANE_OPENED":
                raise LedgerReplayError(f"seq {entry.seq}: duplicate LANE_OPENED")
            if effect == "RUN_END":
                if decision_count == 0 or prev_command not in _TERMINAL_COMMANDS or gap:
                    raise LedgerReplayError(f"seq {entry.seq}: RUN_END without a terminal decision")
                if entry.payload.get("state") != snapshot.state.value:
                    raise LedgerReplayError(f"seq {entry.seq}: RUN_END state disagrees with replay")
                saw_run_end = True
                continue
            gap.append(entry)
            continue
        # DECISION
        payload = entry.payload
        if set(payload) != _DECISION_KEYS:
            raise LedgerReplayError(f"seq {entry.seq}: decision payload keys are incomplete")
        _check_effect_gap(
            entry.seq,
            gap,
            prev_command,
            payload["event"],
            snapshot.agent_invocations,
            artifacts_dir,
        )
        gap = []
        event = event_from_dict(payload["event"])
        if payload["state_before"] != snapshot.state.value:
            raise LedgerReplayError(
                f"seq {entry.seq}: recorded state_before {payload['state_before']!r} "
                f"!= replayed {snapshot.state.value!r}"
            )
        decision = reduce(snapshot, event, policy)
        replayed_command = decision.command.kind if decision.command is not None else None
        if (
            payload["state_after"] != decision.snapshot.state.value
            or payload["reason"] != decision.reason.value
            or payload["command"] != replayed_command
        ):
            raise LedgerReplayError(
                f"seq {entry.seq}: recorded transition "
                f"({payload['state_after']}, {payload['reason']}, {payload['command']}) "
                f"!= replayed ({decision.snapshot.state.value}, "
                f"{decision.reason.value}, {replayed_command})"
            )
        if _norm(payload["inputs"]) != _norm(decision.inputs):
            raise LedgerReplayError(f"seq {entry.seq}: recorded predicate inputs disagree")
        if _norm(payload["snapshot_after"]) != _norm(snapshot_to_dict(decision.snapshot)):
            raise LedgerReplayError(f"seq {entry.seq}: recorded snapshot disagrees with replay")
        snapshot = decision.snapshot
        prev_command = replayed_command
        last_decision_command = replayed_command
        decision_count += 1
    if not saw_run_end:
        raise LedgerReplayError("ledger has no RUN_END (incomplete run)")
    if last_decision_command not in _TERMINAL_COMMANDS:
        raise LedgerReplayError("final decision did not terminate the run")
    return snapshot
