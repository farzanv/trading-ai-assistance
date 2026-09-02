"""The walking skeleton: a complete simulated lane, deterministic and replayable.

author -> verify -> review (findings) -> repair -> verify -> review
(one finding still present) -> no-code guidance -> repair -> verify ->
final review -> convergence. No prose routes anything; the run ends at
convergence with no landing effect (V0-A boundary). Every artifact is bound
to the lane identity and the verified candidate revision.
"""

from __future__ import annotations

import json
from pathlib import Path

from orchestrator.artifacts import load_schema_set
from orchestrator.ledger import read_entries, replay
from orchestrator.model import FindingState, GateCategory, LanePolicy, LaneState, ReasonCode

from tests.fakes import (
    ScriptedAuthor,
    ScriptedGate,
    ScriptedReviewer,
    build_coordinator,
    make_author_result,
    make_finding,
    make_fold,
    make_guidance,
    make_identity,
    make_review,
    tree,
)

SCHEMAS = load_schema_set(Path(__file__).resolve().parents[1] / "schemas" / "v2")
POLICY = LanePolicy(
    lane_kind="design",
    accepted_gate_categories=frozenset(
        {GateCategory.PASS, GateCategory.DOCS_INCONCLUSIVE_SCOPE_PASS}
    ),
)
CLOCK = lambda: "2026-09-01T00:00:00+00:00"  # noqa: E731 - fixed clock; never a reducer input


def coordinator_for(tmp_path: Path, *, lane_id: str, author, reviewer, gate):
    return build_coordinator(
        tmp_path,
        lane_id=lane_id,
        schemas=SCHEMAS,
        author=author,
        reviewer=reviewer,
        gate=gate,
        clock=CLOCK,
    )


def artifacts_dir_for(ledger_path: Path, lane_id: str) -> Path:
    return ledger_path.parent / "lanes" / lane_id / "artifacts"


def build_walking_skeleton(tmp_path: Path):
    author = ScriptedAuthor(
        author_results=[make_author_result(revision=1)],
        folds=[
            # round 1 fold: both blockers claimed fixed
            make_fold(revision=2, dispositions={"F1": "FOLDED", "F2": "FOLDED"}),
            # post-guidance fold: the stubborn finding repaired under guidance
            make_fold(revision=3, dispositions={"F2": "FOLDED"}),
        ],
    )
    reviewer = ScriptedReviewer(
        reviews=[
            make_review(
                revision=1,
                verdict="FINDINGS",
                findings=[
                    make_finding("F1", "P1"),
                    make_finding("F2", "P2"),
                    make_finding("F3", "P3"),
                ],
            ),
            make_review(
                revision=2,
                verdict="FINDINGS",
                prior={"F1": "VERIFIED_RESOLVED", "F2": "STILL_PRESENT"},
            ),
            make_review(
                revision=3,
                verdict="CLEAN",
                prior={"F1": "VERIFIED_RESOLVED", "F2": "VERIFIED_RESOLVED"},
            ),
        ],
        guidances=[make_guidance(["F2"], revision=2)],
    )
    return coordinator_for(
        tmp_path, lane_id="LANE-SIM", author=author, reviewer=reviewer, gate=ScriptedGate()
    )


def test_simulated_lane_reaches_convergence_without_prose_routing(tmp_path: Path) -> None:
    coordinator = build_walking_skeleton(tmp_path)
    result = coordinator.run()
    snapshot = result.snapshot

    assert snapshot.state is LaneState.LANDING  # converged; V0-A stops before landing
    assert snapshot.revision == 3
    assert snapshot.review_round == 3
    # author, review, repair, review, guidance, repair, review = 7 spawned agents
    assert snapshot.agent_invocations == 7
    assert snapshot.current_tree == tree(3)  # the converged candidate is pinned

    f1, f2, f3 = snapshot.finding("F1"), snapshot.finding("F2"), snapshot.finding("F3")
    assert f1.state is FindingState.VERIFIED_RESOLVED
    assert f2.state is FindingState.VERIFIED_RESOLVED
    assert f2.guidance_given is True and f2.repair_attempts == 2
    assert f3.severity.value == "P3" and f3.blocking is False


def test_simulated_lane_ledger_is_complete_and_replayable(tmp_path: Path) -> None:
    coordinator = build_walking_skeleton(tmp_path)
    result = coordinator.run()

    entries = read_entries(result.ledger_path)
    assert entries[0].payload["effect"] == "LANE_OPENED"
    assert entries[0].payload["work_item"] == "sim_phase"
    decisions = [e for e in entries if e.kind == "DECISION"]
    effects = [e for e in entries if e.kind == "EFFECT"]

    reasons = [d.payload["reason"] for d in decisions]
    assert reasons[-1] == ReasonCode.CONVERGED.value
    assert ReasonCode.GUIDANCE_REQUIRED.value in reasons
    assert ReasonCode.GUIDANCE_ACCEPTED.value in reasons

    planned = [e.payload for e in effects if e.payload.get("effect") == "INVOCATION_PLANNED"]
    counted = [p for p in planned if p["counted"]]
    assert len(counted) == 7  # every spawned agent process is ledgered exactly once
    assert [p["invocation_number"] for p in counted] == list(range(1, 8))
    gate_runs = [p for p in planned if p["role"] == "gate"]
    assert len(gate_runs) == 3 and all(not p["counted"] for p in gate_runs)

    accepted = [e.payload for e in effects if e.payload.get("effect") == "ARTIFACT_ACCEPTED"]
    assert all("digest" in a and len(a["digest"]) == 64 for a in accepted)
    assert {a["artifact"] for a in accepted} == {
        "author-result",
        "fold",
        "review",
        "guidance",
        "target-gate-result",
    }
    assert [e.payload for e in effects if e.payload.get("effect") == "RUN_END"][0]["end"] == (
        "CONVERGED_V0A_NO_LANDING"
    )

    # Replay re-runs the pure reducer over every recorded event, enforces the
    # effect protocol, and re-verifies every persisted artifact digest.
    assert (
        replay(result.ledger_path, POLICY, make_identity(lane_id="LANE-SIM"), SCHEMAS)
        == result.snapshot
    )


def test_two_identical_runs_are_deterministic(tmp_path: Path) -> None:
    first = build_walking_skeleton(tmp_path / "a").run()
    second = build_walking_skeleton(tmp_path / "b").run()
    assert first.snapshot == second.snapshot
    assert first.steps == second.steps
    lines_a = first.ledger_path.read_text(encoding="utf-8")
    lines_b = second.ledger_path.read_text(encoding="utf-8")
    assert lines_a == lines_b  # fixed clock: byte-identical evidence


def test_p3_backlog_artifact_is_written_not_folded(tmp_path: Path) -> None:
    coordinator = build_walking_skeleton(tmp_path)
    result = coordinator.run()
    backlog = json.loads(
        (artifacts_dir_for(result.ledger_path, "LANE-SIM") / "p3-backlog.json").read_text(
            encoding="utf-8"
        )
    )
    assert backlog["findings"] == [{"id": "F3", "severity": "P3", "title": "finding F3"}]


def test_run_state_stays_inside_the_project_state_root(tmp_path: Path) -> None:
    coordinator = build_walking_skeleton(tmp_path)
    result = coordinator.run()
    state_root = (tmp_path / "control-plane" / "projects" / "sim-project" / "state").resolve()
    assert result.ledger_path.resolve().is_relative_to(state_root / "runs" / "RUN-001")


def test_malformed_review_is_retried_once_then_accepted(tmp_path: Path) -> None:
    malformed = make_review(revision=1, verdict="CLEAN")
    malformed["verdict"] = "LOOKS_FINE"  # schema violation
    coordinator = coordinator_for(
        tmp_path,
        lane_id="LANE-RETRY",
        author=ScriptedAuthor(author_results=[make_author_result()]),
        reviewer=ScriptedReviewer(reviews=[malformed, make_review(revision=1, verdict="CLEAN")]),
        gate=ScriptedGate(),
    )
    result = coordinator.run()
    assert result.snapshot.state is LaneState.LANDING
    assert result.snapshot.agent_invocations == 3  # the retry spawn counted
    assert result.snapshot.review_round == 1  # but no extra round was consumed
    rejected = [
        e.payload
        for e in read_entries(result.ledger_path)
        if e.kind == "EFFECT" and e.payload.get("effect") == "ARTIFACT_REJECTED"
    ]
    assert len(rejected) == 1 and rejected[0]["artifact"] == "review"


def test_second_malformed_review_stops_the_lane(tmp_path: Path) -> None:
    malformed = make_review(revision=1, verdict="CLEAN")
    malformed["verdict"] = "LOOKS_FINE"
    coordinator = coordinator_for(
        tmp_path,
        lane_id="LANE-STOP",
        author=ScriptedAuthor(author_results=[make_author_result()]),
        reviewer=ScriptedReviewer(reviews=[malformed, dict(malformed)]),
        gate=ScriptedGate(),
    )
    result = coordinator.run()
    assert result.snapshot.state is LaneState.STOPPED
    entries = read_entries(result.ledger_path)
    last_decision = [e for e in entries if e.kind == "DECISION"][-1]
    assert last_decision.payload["reason"] == ReasonCode.MALFORMED_ARTIFACT_STOP.value
    assert (
        replay(result.ledger_path, POLICY, make_identity(lane_id="LANE-STOP"), SCHEMAS).state
        is LaneState.STOPPED
    )


def test_unbound_review_naming_a_foreign_revision_stops_the_lane(tmp_path: Path) -> None:
    """A review of the wrong range/tree is malformed, never silently adopted."""
    foreign = make_review(revision=7, verdict="CLEAN")  # not the verified candidate
    coordinator = coordinator_for(
        tmp_path,
        lane_id="LANE-UNBOUND",
        author=ScriptedAuthor(author_results=[make_author_result()]),
        reviewer=ScriptedReviewer(reviews=[foreign, dict(foreign)]),
        gate=ScriptedGate(),
    )
    result = coordinator.run()
    assert result.snapshot.state is LaneState.STOPPED
    rejected = [
        e.payload
        for e in read_entries(result.ledger_path)
        if e.kind == "EFFECT" and e.payload.get("effect") == "ARTIFACT_REJECTED"
    ]
    assert len(rejected) == 2 and all(r["artifact"] == "review" for r in rejected)


def test_author_unknown_contract_opens_a_human_gate(tmp_path: Path) -> None:
    raw = make_author_result(
        unknown_contracts=[
            {
                "provider": "FMP",
                "endpoint": "/v4/thing",
                "question": "paging rule not established",
                "consulted": ["pinned docs"],
                "would_settle": "an audited capture",
            }
        ]
    )
    coordinator = coordinator_for(
        tmp_path,
        lane_id="LANE-UC",
        author=ScriptedAuthor(author_results=[raw]),
        reviewer=ScriptedReviewer(),
        gate=ScriptedGate(),
    )
    result = coordinator.run()
    assert result.snapshot.state is LaneState.WAIT_OPERATOR
    end = [
        e.payload
        for e in read_entries(result.ledger_path)
        if e.kind == "EFFECT" and e.payload.get("effect") == "RUN_END"
    ][0]
    assert end["end"] == "HUMAN_GATE" and end["reason"] == ReasonCode.UNKNOWN_CONTRACT.value


def test_requires_ruling_review_opens_a_human_gate(tmp_path: Path) -> None:
    coordinator = coordinator_for(
        tmp_path,
        lane_id="LANE-GATE",
        author=ScriptedAuthor(author_results=[make_author_result()]),
        reviewer=ScriptedReviewer(
            reviews=[
                make_review(
                    revision=1,
                    verdict="FINDINGS",
                    findings=[make_finding("F1", "P1", requires_ruling=True)],
                )
            ]
        ),
        gate=ScriptedGate(),
    )
    result = coordinator.run()
    assert result.snapshot.state is LaneState.WAIT_OPERATOR
    end = [
        e.payload
        for e in read_entries(result.ledger_path)
        if e.kind == "EFFECT" and e.payload.get("effect") == "RUN_END"
    ][0]
    assert end["end"] == "HUMAN_GATE" and end["reason"] == ReasonCode.REQUIRES_RULING.value


def test_max_rounds_exhaustion_stops_never_forces_acceptance(tmp_path: Path) -> None:
    reviews = []
    folds = []
    # Round n review reopens nothing but always finds one new P1; the author
    # always folds it. Round 11 would exceed max_rounds=10 and must STOP first.
    for n in range(1, 11):
        prior = {f"F{i}": "VERIFIED_RESOLVED" for i in range(1, n)}
        reviews.append(
            make_review(
                revision=n,
                verdict="FINDINGS",
                findings=[make_finding(f"F{n}", "P1")],
                prior=prior,
            )
        )
        folds.append(make_fold(revision=n + 1, dispositions={f"F{n}": "FOLDED"}))
    coordinator = coordinator_for(
        tmp_path,
        lane_id="LANE-ROUNDS",
        author=ScriptedAuthor(author_results=[make_author_result()], folds=folds),
        reviewer=ScriptedReviewer(reviews=reviews),
        gate=ScriptedGate(),
    )
    result = coordinator.run()
    assert result.snapshot.state is LaneState.STOPPED
    assert result.snapshot.review_round == 10
    last_decision = [e for e in read_entries(result.ledger_path) if e.kind == "DECISION"][-1]
    assert last_decision.payload["reason"] == ReasonCode.MAX_ROUNDS_EXCEEDED.value


def test_repair_rolling_back_to_an_earlier_candidate_stops_the_lane(tmp_path: Path) -> None:
    """R1-01 regression: a fold naming any earlier lane commit is refused by
    the Git facts (no descent from the previous accepted candidate)."""
    from tests.fakes import sha

    rollback = make_fold(
        revision=3, dispositions={"F1": "FOLDED"}, commit=sha(1), tree_digest=tree(1)
    )
    coordinator = coordinator_for(
        tmp_path,
        lane_id="LANE-ROLLBACK",
        author=ScriptedAuthor(
            author_results=[make_author_result()],
            folds=[
                make_fold(revision=2, dispositions={"F1": "FOLDED"}),
                rollback,
                dict(rollback),
            ],
        ),
        reviewer=ScriptedReviewer(
            reviews=[
                make_review(revision=1, verdict="FINDINGS", findings=[make_finding("F1", "P1")]),
                make_review(revision=2, verdict="FINDINGS", prior={"F1": "STILL_PRESENT"}),
            ],
            guidances=[make_guidance(["F1"], revision=2)],
        ),
        gate=ScriptedGate(),
    )
    result = coordinator.run()
    assert result.snapshot.state is LaneState.STOPPED
    assert result.snapshot.current_sha == sha(2)  # the lane never rolled back
    rejected = [
        e.payload
        for e in read_entries(result.ledger_path)
        if e.kind == "EFFECT" and e.payload.get("effect") == "ARTIFACT_REJECTED"
    ]
    assert len(rejected) == 2 and all(r["artifact"] == "fold" for r in rejected)
    assert any("previous accepted candidate" in err for r in rejected for err in r["errors"])


def test_fixable_gate_failure_is_repaired_and_reconciled_by_the_reviewer(tmp_path: Path) -> None:
    """R2-01: the SYS gate finding stays in the reviewer's exact set; the
    passing gate is its closure evidence and the reviewer reconciles it."""
    from tests.fakes import GOOD_FACTS, make_gate_result

    coordinator = coordinator_for(
        tmp_path,
        lane_id="LANE-SYSGATE",
        author=ScriptedAuthor(
            author_results=[make_author_result()],
            folds=[make_fold(revision=2, dispositions={"SYS-FIXABLE_TEST": "FOLDED"})],
        ),
        reviewer=ScriptedReviewer(
            reviews=[
                make_review(
                    revision=2,
                    verdict="CLEAN",
                    prior={"SYS-FIXABLE_TEST": "VERIFIED_RESOLVED"},
                )
            ]
        ),
        gate=ScriptedGate(
            results=[(GOOD_FACTS, make_gate_result("FIXABLE_TEST", revision=1))]
        ),
    )
    result = coordinator.run()
    assert result.snapshot.state is LaneState.LANDING
    record = result.snapshot.finding("SYS-FIXABLE_TEST")
    assert record.state is FindingState.VERIFIED_RESOLVED
    assert (
        replay(result.ledger_path, POLICY, make_identity(lane_id="LANE-SYSGATE"), SCHEMAS)
        == result.snapshot
    )


def test_review_omitting_the_sys_gate_finding_is_malformed(tmp_path: Path) -> None:
    from tests.fakes import GOOD_FACTS, make_gate_result

    unreconciled = make_review(revision=2, verdict="CLEAN")  # empty prior_findings
    coordinator = coordinator_for(
        tmp_path,
        lane_id="LANE-SYSMISS",
        author=ScriptedAuthor(
            author_results=[make_author_result()],
            folds=[make_fold(revision=2, dispositions={"SYS-FIXABLE_TEST": "FOLDED"})],
        ),
        reviewer=ScriptedReviewer(reviews=[unreconciled, dict(unreconciled)]),
        gate=ScriptedGate(
            results=[(GOOD_FACTS, make_gate_result("FIXABLE_TEST", revision=1))]
        ),
    )
    result = coordinator.run()
    assert result.snapshot.state is LaneState.STOPPED


def test_author_self_review_cannot_gate(tmp_path: Path) -> None:
    """R2-02: a gating review claiming the lane author as reviewer is malformed."""
    self_review = make_review(revision=1, verdict="CLEAN", reviewer={"agent": "claude"})
    coordinator = coordinator_for(
        tmp_path,
        lane_id="LANE-SELF",
        author=ScriptedAuthor(author_results=[make_author_result()]),
        reviewer=ScriptedReviewer(reviews=[self_review, dict(self_review)]),
        gate=ScriptedGate(),
    )
    result = coordinator.run()
    assert result.snapshot.state is LaneState.STOPPED
    rejected = [
        e.payload
        for e in read_entries(result.ledger_path)
        if e.kind == "EFFECT" and e.payload.get("effect") == "ARTIFACT_REJECTED"
    ]
    assert any("authorized reviewer" in err for r in rejected for err in r["errors"])


def test_wrong_review_kind_for_the_lane_is_malformed(tmp_path: Path) -> None:
    """R2-02: a design lane accepts only review_kind=design gating reviews."""
    wrong_kind = make_review(revision=1, verdict="CLEAN", review_kind="bookkeeping")
    coordinator = coordinator_for(
        tmp_path,
        lane_id="LANE-KIND",
        author=ScriptedAuthor(author_results=[make_author_result()]),
        reviewer=ScriptedReviewer(reviews=[wrong_kind, dict(wrong_kind)]),
        gate=ScriptedGate(),
    )
    result = coordinator.run()
    assert result.snapshot.state is LaneState.STOPPED
