"""Ledger integrity: append-only digest chain, tamper detection, replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.application import LaneCoordinator
from orchestrator.artifacts import load_schema_set
from orchestrator.ledger import (
    Ledger,
    LedgerEntry,
    LedgerError,
    LedgerReplayError,
    read_entries,
    replay,
)
from orchestrator.model import GateCategory, LanePolicy, LaneState

from tests.fakes import ScriptedAuthor, ScriptedGate, ScriptedReviewer, make_author_result, make_review

SCHEMAS = load_schema_set(Path(__file__).resolve().parents[1] / "schemas" / "v2")
POLICY = LanePolicy(
    lane_kind="design",
    accepted_gate_categories=frozenset(
        {GateCategory.PASS, GateCategory.DOCS_INCONCLUSIVE_SCOPE_PASS}
    ),
)


def run_minimal_lane(tmp_path: Path) -> Path:
    """author rev 1 -> gate accepted -> review CLEAN -> converged."""
    coordinator = LaneCoordinator(
        lane_id="LANE-T",
        policy=POLICY,
        schemas=SCHEMAS,
        author=ScriptedAuthor(author_results=[make_author_result()]),
        reviewer=ScriptedReviewer(reviews=[make_review(verdict="CLEAN", revision=1)]),
        gate=ScriptedGate(),
        run_dir=tmp_path / "run",
        clock=lambda: "2026-09-01T00:00:00+00:00",
    )
    result = coordinator.run()
    assert result.snapshot.state is LaneState.LANDING
    return result.ledger_path


def _rewrite(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rechain(lines: list[str]) -> list[str]:
    """Recompute seq/prev/digest for tampered lines (a chain-aware attacker)."""
    rebuilt: list[str] = []
    prev = "0" * 64
    for seq, line in enumerate(lines, start=1):
        raw = json.loads(line)
        raw["seq"] = seq
        raw["prev_digest"] = prev
        body = {k: raw[k] for k in ("seq", "kind", "lane_id", "ts", "payload", "prev_digest")}
        raw["digest"] = LedgerEntry.compute_digest(body)
        prev = raw["digest"]
        rebuilt.append(json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return rebuilt


def test_valid_ledger_reads_and_replays(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    entries = read_entries(path)
    assert entries[0].prev_digest == "0" * 64
    assert [e.seq for e in entries] == list(range(1, len(entries) + 1))
    snapshot = replay(path, POLICY)
    assert snapshot.state is LaneState.LANDING
    assert snapshot.review_round == 1
    assert snapshot.agent_invocations == 2


def test_rewritten_line_is_detected(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    raw = json.loads(lines[1])
    raw["payload"] = dict(raw["payload"], forged=True)
    lines[1] = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    _rewrite(path, lines)
    with pytest.raises(LedgerError, match="digest mismatch"):
        read_entries(path)


def test_deleted_line_is_detected(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[2]
    _rewrite(path, lines)
    with pytest.raises(LedgerError):
        read_entries(path)


def test_reordered_lines_are_detected(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    _rewrite(path, lines)
    with pytest.raises(LedgerError):
        read_entries(path)


def test_duplicate_sequence_is_detected(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines.insert(2, lines[1])
    _rewrite(path, lines)
    with pytest.raises(LedgerError):
        read_entries(path)


def test_corrupted_digest_is_detected(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    raw = json.loads(lines[0])
    raw["digest"] = "f" * 64
    lines[0] = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    _rewrite(path, lines)
    with pytest.raises(LedgerError):
        read_entries(path)


def test_unexpected_fields_are_rejected(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    raw = json.loads(lines[0])
    raw["extra"] = 1
    lines[0] = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    _rewrite(path, lines)
    with pytest.raises(LedgerError, match="unexpected ledger fields"):
        read_entries(path)


def test_chain_aware_transition_forgery_fails_replay(tmp_path: Path) -> None:
    """Even a tamperer who recomputes every digest cannot forge a transition:
    replay re-runs the pure reducer and the recorded decision must match."""
    path = run_minimal_lane(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        raw = json.loads(line)
        if raw["kind"] == "DECISION" and raw["payload"]["state_after"] == "LANDING":
            raw["payload"]["state_after"] = "COMPLETED"
            lines[index] = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    _rewrite(path, _rechain(lines))
    read_entries(path)  # the recomputed chain itself is internally consistent
    with pytest.raises(LedgerReplayError):
        replay(path, POLICY)


def test_append_continues_an_existing_chain(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    first = Ledger(path)
    first.append("EFFECT", "LANE-T", "2026-09-01T00:00:00+00:00", {"effect": "A"})
    second = Ledger(path)  # a new process resumes the same file
    second.append("EFFECT", "LANE-T", "2026-09-01T00:00:01+00:00", {"effect": "B"})
    entries = read_entries(path)
    assert [e.seq for e in entries] == [1, 2]
    assert entries[1].prev_digest == entries[0].digest
