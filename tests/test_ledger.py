"""Ledger integrity: append-only digest chain, tamper detection, full replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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

from tests.fakes import (
    ScriptedAuthor,
    ScriptedGate,
    ScriptedReviewer,
    build_coordinator,
    make_author_result,
    make_identity,
    make_review,
)

SCHEMAS = load_schema_set(Path(__file__).resolve().parents[1] / "schemas" / "v2")
POLICY = LanePolicy(
    lane_kind="design",
    accepted_gate_categories=frozenset(
        {GateCategory.PASS, GateCategory.DOCS_INCONCLUSIVE_SCOPE_PASS}
    ),
)
IDENTITY = make_identity("LANE-T")


def run_minimal_lane(tmp_path: Path) -> Path:
    """author rev 1 -> gate accepted -> review CLEAN -> converged."""
    coordinator = build_coordinator(
        tmp_path,
        lane_id="LANE-T",
        schemas=SCHEMAS,
        author=ScriptedAuthor(author_results=[make_author_result()]),
        reviewer=ScriptedReviewer(reviews=[make_review(verdict="CLEAN", revision=1)]),
        gate=ScriptedGate(),
        clock=lambda: "2026-09-01T00:00:00+00:00",
    )
    assert coordinator.policy == POLICY  # resolved from the registered project
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
    assert entries[0].payload["effect"] == "LANE_OPENED"
    assert [e.seq for e in entries] == list(range(1, len(entries) + 1))
    snapshot = replay(path, POLICY, IDENTITY, SCHEMAS)
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


def _forge(path: Path, mutate) -> None:
    """Apply ``mutate`` to each parsed line, then recompute the whole chain."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        raw = json.loads(line)
        mutate(raw)
        lines[index] = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    _rewrite(path, _rechain(lines))
    read_entries(path)  # the recomputed chain itself is internally consistent


def test_chain_aware_transition_forgery_fails_replay(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)

    def mutate(raw: dict) -> None:
        if raw["kind"] == "DECISION" and raw["payload"]["state_after"] == "LANDING":
            raw["payload"]["state_after"] = "COMPLETED"
            raw["payload"]["snapshot_after"]["state"] = "COMPLETED"

    _forge(path, mutate)
    with pytest.raises(LedgerReplayError):
        replay(path, POLICY, IDENTITY, SCHEMAS)


def test_chain_aware_predicate_input_forgery_fails_replay(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)

    def mutate(raw: dict) -> None:
        if raw["kind"] == "DECISION" and "gate_category" in raw["payload"].get("inputs", {}):
            raw["payload"]["inputs"]["worktree_clean"] = False

    _forge(path, mutate)
    with pytest.raises(LedgerReplayError, match="predicate inputs"):
        replay(path, POLICY, IDENTITY, SCHEMAS)


def test_chain_aware_snapshot_forgery_fails_replay(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)

    def mutate(raw: dict) -> None:
        if raw["kind"] == "DECISION":
            raw["payload"]["snapshot_after"]["agent_invocations"] = 0

    _forge(path, mutate)
    with pytest.raises(LedgerReplayError, match="snapshot"):
        replay(path, POLICY, IDENTITY, SCHEMAS)


def test_chain_aware_artifact_digest_forgery_fails_replay(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)

    def mutate(raw: dict) -> None:
        if raw["kind"] == "EFFECT" and raw["payload"].get("effect") == "ARTIFACT_ACCEPTED":
            raw["payload"]["digest"] = "f" * 64

    _forge(path, mutate)
    with pytest.raises(LedgerReplayError, match="digest does not match"):
        replay(path, POLICY, IDENTITY, SCHEMAS)


def test_mutated_artifact_file_fails_replay(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    artifacts_dir = path.parent / "lanes" / "LANE-T" / "artifacts"
    target = next(p for p in artifacts_dir.iterdir() if "review" in p.name)
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["verdict"] = "FINDINGS"
    target.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    with pytest.raises(LedgerReplayError, match="digest does not match"):
        replay(path, POLICY, IDENTITY, SCHEMAS)


def test_deleted_effect_evidence_fails_replay(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    planned = next(
        i for i, line in enumerate(lines)
        if json.loads(line)["payload"].get("effect") == "INVOCATION_PLANNED"
    )
    del lines[planned]
    _rewrite(path, _rechain(lines))
    with pytest.raises(LedgerReplayError):
        replay(path, POLICY, IDENTITY, SCHEMAS)


def test_swapped_artifact_with_forged_digest_and_chain_fails_replay(tmp_path: Path) -> None:
    """Even forging artifact + digest + full chain cannot forge a run: replay
    re-validates the artifact content and re-derives the typed event."""
    from orchestrator.artifacts import artifact_digest

    path = run_minimal_lane(tmp_path)
    artifacts_dir = path.parent / "lanes" / "LANE-T" / "artifacts"
    target = next(p for p in artifacts_dir.iterdir() if "review" in p.name)
    garbage = {"schema_version": 2, "unrelated": True}
    target.write_text(
        json.dumps(garbage, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )

    def mutate(raw: dict) -> None:
        if raw["kind"] == "EFFECT" and raw["payload"].get("effect") == "ARTIFACT_ACCEPTED":
            if raw["payload"]["artifact"] == "review":
                raw["payload"]["digest"] = artifact_digest(garbage)

    _forge(path, mutate)
    with pytest.raises(LedgerReplayError, match="fails re-validation"):
        replay(path, POLICY, IDENTITY, SCHEMAS)


def test_swapped_valid_but_different_artifact_fails_replay(tmp_path: Path) -> None:
    """A well-formed substitute that derives a DIFFERENT event is also caught."""
    from orchestrator.artifacts import artifact_digest

    path = run_minimal_lane(tmp_path)
    artifacts_dir = path.parent / "lanes" / "LANE-T" / "artifacts"
    target = next(p for p in artifacts_dir.iterdir() if "review" in p.name)
    substitute = make_review(
        verdict="FINDINGS",
        revision=1,
        findings=[
            {
                "id": "FX",
                "severity": "P1",
                "section": "§1",
                "title": "planted",
                "description": "planted",
                "required_change": "planted",
                "root_cause": "planted",
                "consequence": "planted",
                "recommended_approach": "planted",
                "closure_evidence": "planted",
                "requires_ruling": False,
                "earlier_phase_gap": None,
                "blocks_downstream": False,
                "unknown_contract": False,
            }
        ],
    )
    target.write_text(
        json.dumps(substitute, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )

    def mutate(raw: dict) -> None:
        if raw["kind"] == "EFFECT" and raw["payload"].get("effect") == "ARTIFACT_ACCEPTED":
            if raw["payload"]["artifact"] == "review":
                raw["payload"]["digest"] = artifact_digest(substitute)

    _forge(path, mutate)
    with pytest.raises(LedgerReplayError, match="does not derive the ledgered event"):
        replay(path, POLICY, IDENTITY, SCHEMAS)


def test_replay_refuses_a_different_lane_identity(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    with pytest.raises(LedgerReplayError, match="different lane identity"):
        replay(path, POLICY, make_identity("LANE-OTHER"), SCHEMAS)


def test_replay_verifies_caller_supplied_project_context(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    with pytest.raises(LedgerReplayError, match="different project packages"):
        replay(
            path,
            POLICY,
            IDENTITY,
            SCHEMAS,
            expected_package_digests=(("agents.author", "0" * 64),),
        )
    with pytest.raises(LedgerReplayError, match="different project configuration"):
        replay(path, POLICY, IDENTITY, SCHEMAS, expected_config_digest="0" * 64)


def test_replay_refuses_a_different_lane_policy(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    other = LanePolicy(
        lane_kind="design",
        accepted_gate_categories=frozenset({GateCategory.PASS}),
    )
    with pytest.raises(LedgerReplayError, match="different lane policy"):
        replay(path, other, IDENTITY, SCHEMAS)


def test_replay_refuses_an_incomplete_run(tmp_path: Path) -> None:
    path = run_minimal_lane(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    _rewrite(path, _rechain(lines[:-1]))  # drop RUN_END
    with pytest.raises(LedgerReplayError, match="RUN_END"):
        replay(path, POLICY, IDENTITY, SCHEMAS)


def test_append_continues_an_existing_chain(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    first = Ledger(path)
    first.append("EFFECT", "LANE-T", "2026-09-01T00:00:00+00:00", {"effect": "A"})
    second = Ledger(path)  # a new process resumes the same file
    second.append("EFFECT", "LANE-T", "2026-09-01T00:00:01+00:00", {"effect": "B"})
    entries = read_entries(path)
    assert [e.seq for e in entries] == [1, 2]
    assert entries[1].prev_digest == entries[0].digest
