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

from orchestrator.artifacts import (
    ArtifactError,
    SchemaSet,
    event_from_author_summary,
    event_from_fold_summary,
    event_from_guidance_summary,
    event_from_review_summary,
    validate_author_result,
    validate_fold,
    validate_gate_result,
    validate_guidance,
    validate_review,
)
from orchestrator.model import (
    AuthorResultAccepted,
    Event,
    FoldAccepted,
    GuidanceAccepted,
    LaneIdentity,
    LanePolicy,
    LaneSnapshot,
    Lens,
    REVIEW_KIND_FOR_LANE_KIND,
    ReviewAccepted,
    RevisionVerified,
    event_from_dict,
    event_to_dict,
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
        "config_digest",
        "work_index_digest",
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


def _load_artifact(path: Path) -> Mapping[str, Any]:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerReplayError(f"accepted artifact unreadable: {path.name}: {exc}") from None


def _check_effect_gap(
    seq: int,
    gap: list[LedgerEntry],
    prev_command: str | None,
    event_payload: Mapping[str, Any],
    invocations_after_prev: int,
    artifacts_dir: Path,
) -> Mapping[str, Any] | None:
    """The effects between two decisions must match the issued command and the
    event the next decision consumed — invocation planned, then exactly one
    accepted (digest-bound) or rejected artifact of the right kind.

    Returns the ARTIFACT_ACCEPTED payload for an accepted event (so the caller
    can re-validate the artifact content), or None for rejections/authorization.
    """
    event_kind = event_payload["kind"]
    if prev_command is None:
        if gap or event_kind != "LaneAuthorized":
            raise LedgerReplayError(f"seq {seq}: unexpected evidence before lane authorization")
        return None
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
        return None
    expected_kind = _EVENT_ARTIFACT_KIND.get(event_kind)
    if expected_kind is None:
        raise LedgerReplayError(f"seq {seq}: no artifact contract for event {event_kind}")
    if outcome.get("effect") != "ARTIFACT_ACCEPTED" or outcome.get("artifact") != expected_kind:
        raise LedgerReplayError(f"seq {seq}: accepted artifact evidence missing or wrong kind")
    raw = _load_artifact(artifacts_dir / outcome["path"])
    actual = hashlib.sha256(_canonical(raw).encode("utf-8")).hexdigest()
    if outcome.get("digest") != actual:
        raise LedgerReplayError(
            f"seq {seq}: artifact {outcome['path']} digest does not match the ledger"
        )
    return outcome


def _rederive_event(
    seq: int,
    event: Event,
    outcome: Mapping[str, Any],
    snapshot: LaneSnapshot,
    policy: LanePolicy,
    identity: LaneIdentity,
    schemas: SchemaSet,
    artifacts_dir: Path,
) -> None:
    """Re-run the artifact validator in the replayed lane context and require
    that the persisted artifact derives EXACTLY the ledgered typed event —
    a forged artifact-plus-digest-plus-chain still cannot forge the event."""
    raw = _load_artifact(artifacts_dir / outcome["path"])
    review_kind = REVIEW_KIND_FOR_LANE_KIND[policy.lane_kind]
    try:
        derived: Event
        if isinstance(event, AuthorResultAccepted):
            summary = validate_author_result(
                schemas,
                raw,
                identity=identity,
                expected_revision=1,
                expected_author_agent=policy.author_agent,
            )
            _check_git_block(seq, outcome, summary.tree_digest)
            derived = event_from_author_summary(summary)
        elif isinstance(event, FoldAccepted):
            summary = validate_fold(
                schemas,
                raw,
                identity=identity,
                outstanding_ids=snapshot.fold_outstanding_ids(),
                expected_revision=snapshot.revision + 1,
                prev_sha=snapshot.current_sha,
                expected_author_agent=policy.author_agent,
            )
            _check_git_block(seq, outcome, summary.tree_digest)
            derived = event_from_fold_summary(summary)
        elif isinstance(event, ReviewAccepted):
            summary = validate_review(
                schemas,
                raw,
                identity=identity,
                expected_lens=Lens.GATING,
                historical=snapshot.historical_blocking_states(),
                current_sha=snapshot.current_sha,
                current_tree=snapshot.current_tree,
                expected_review_kind=review_kind,
                expected_reviewer_agent=policy.reviewer_agent,
            )
            derived = event_from_review_summary(summary)
        elif isinstance(event, GuidanceAccepted):
            summary = validate_guidance(
                schemas,
                raw,
                identity=identity,
                expected_finding_ids=snapshot.guidance_expected_ids(),
                current_sha=snapshot.current_sha,
                current_tree=snapshot.current_tree,
                expected_review_kind=review_kind,
                expected_reviewer_agent=policy.reviewer_agent,
            )
            derived = event_from_guidance_summary(summary)
        elif isinstance(event, RevisionVerified):
            gate = validate_gate_result(
                schemas,
                raw,
                identity=identity,
                current_sha=snapshot.current_sha,
                current_tree=snapshot.current_tree,
            )
            # The five worktree/range facts are driver-observed evidence with
            # no artifact source; the artifact-backed fields must match.
            if gate.category is not event.gate_category or gate.failed_checks != event.failed_checks:
                raise LedgerReplayError(
                    f"seq {seq}: gate artifact does not derive the ledgered event"
                )
            return
        else:
            raise LedgerReplayError(f"seq {seq}: no re-derivation for event {event.kind}")
    except ArtifactError as exc:
        raise LedgerReplayError(
            f"seq {seq}: accepted artifact fails re-validation: {exc.artifact}"
        ) from exc
    if _norm(event_to_dict(derived)) != _norm(event_to_dict(event)):
        raise LedgerReplayError(f"seq {seq}: artifact does not derive the ledgered event")


def _check_git_block(seq: int, outcome: Mapping[str, Any], claimed_tree: str) -> None:
    git = outcome.get("git")
    if not isinstance(git, Mapping):
        raise LedgerReplayError(f"seq {seq}: candidate git facts missing from acceptance")
    if not (
        git.get("exists") is True
        and git.get("descends_from_scope_base") is True
        and git.get("descends_from_previous_candidate") is True
        and git.get("tree_digest") == claimed_tree
    ):
        raise LedgerReplayError(f"seq {seq}: candidate git facts do not support acceptance")


def replay(
    path: Path,
    policy: LanePolicy,
    identity: LaneIdentity,
    schemas: SchemaSet,
    *,
    expected_package_digests: tuple[tuple[str, str], ...] | None = None,
    expected_config_digest: str | None = None,
    expected_work_index_digest: str | None = None,
    artifacts_dir: Path | None = None,
) -> LaneSnapshot:
    """Validate the chain and replay the COMPLETE recorded run.

    Checks, in order: the LANE_OPENED binding against the CALLER-supplied
    policy, identity, schema set, and (when given) package/config/work-index
    digests — never the ledger's self-recorded provenance alone; the effect
    protocol around every decision; every accepted artifact's file digest AND
    its re-validation in the replayed lane context, requiring that it derives
    exactly the ledgered typed event; and — for every DECISION — that
    re-running the pure reducer reproduces the recorded snapshot, reason,
    command, and predicate inputs exactly. Returns the final proved snapshot.
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
    if opened.payload["schemas_digest"] != schemas.digest:
        raise LedgerReplayError("ledger was opened under a different schema set")
    if (
        opened.lane_id != identity.lane_id
        or opened.payload["work_item"] != identity.work_item
        or opened.payload["manifest"] != identity.manifest
        or opened.payload["scope_base"] != identity.scope_base
        or opened.payload["lane_kind"] != policy.lane_kind
    ):
        raise LedgerReplayError("ledger was opened under a different lane identity")
    if expected_package_digests is not None and _norm(opened.payload["package_digests"]) != _norm(
        [list(pair) for pair in expected_package_digests]
    ):
        raise LedgerReplayError("ledger was opened under different project packages")
    if (
        expected_config_digest is not None
        and opened.payload["config_digest"] != expected_config_digest
    ):
        raise LedgerReplayError("ledger was opened under a different project configuration")
    if (
        expected_work_index_digest is not None
        and opened.payload["work_index_digest"] != expected_work_index_digest
    ):
        raise LedgerReplayError("ledger was opened under a different work index")
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
        outcome = _check_effect_gap(
            entry.seq,
            gap,
            prev_command,
            payload["event"],
            snapshot.agent_invocations,
            artifacts_dir,
        )
        gap = []
        event = event_from_dict(payload["event"])
        if outcome is not None:
            _rederive_event(
                entry.seq, event, outcome, snapshot, policy, identity, schemas, artifacts_dir
            )
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
