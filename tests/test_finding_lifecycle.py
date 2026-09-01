"""Finding lifecycle: guidance, handoff, rejection, adjudication, ping-pong, flags."""

from __future__ import annotations

from orchestrator.model import (
    AgentAction,
    Awaiting,
    Disposition,
    FindingRecord,
    FindingState,
    FoldAccepted,
    GateCategory,
    GuidanceAccepted,
    InvokeAuthor,
    InvokeGuidance,
    LanePolicy,
    LaneSnapshot,
    LaneState,
    Lens,
    NewFinding,
    PriorAssessment,
    PriorOutcome,
    ReasonCode,
    ReviewAccepted,
    Severity,
    VerifyRevision,
)
from orchestrator.reducer import reduce

POLICY = LanePolicy(
    lane_kind="design",
    accepted_gate_categories=frozenset(
        {GateCategory.PASS, GateCategory.DOCS_INCONCLUSIVE_SCOPE_PASS}
    ),
)


def record(
    fid: str = "F1",
    state: FindingState = FindingState.FIX_CLAIMED,
    severity: Severity = Severity.P1,
    **overrides: object,
) -> FindingRecord:
    return FindingRecord(finding_id=fid, severity=severity, title=fid, state=state, **overrides)


def reviewing(*findings: FindingRecord, **overrides: object) -> LaneSnapshot:
    return LaneSnapshot(
        state=LaneState.REVIEWING,
        awaiting=Awaiting.REVIEW,
        revision=2,
        review_round=2,
        agent_invocations=4,
        findings=tuple(findings),
        **overrides,
    )


def review_event(
    prior: dict[str, PriorOutcome],
    new: tuple[NewFinding, ...] = (),
    verdict: str = "FINDINGS",
) -> ReviewAccepted:
    return ReviewAccepted(
        lens=Lens.GATING,
        verdict=verdict,
        has_scope_observations=False,
        new_findings=new,
        prior_findings=tuple(PriorAssessment(fid, o) for fid, o in sorted(prior.items())),
    )


# -- guidance and handoff ----------------------------------------------------


def test_still_present_after_first_repair_requests_no_code_guidance() -> None:
    snapshot = reviewing(record("F1", FindingState.FIX_CLAIMED, repair_attempts=1))
    decision = reduce(snapshot, review_event({"F1": PriorOutcome.STILL_PRESENT}), POLICY)
    assert decision.command == InvokeGuidance()
    assert decision.reason is ReasonCode.GUIDANCE_REQUIRED
    assert decision.snapshot.finding("F1").state is FindingState.GUIDANCE_REQUIRED


def test_guidance_accepted_marks_findings_and_requests_repair() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.REVIEWING,
        awaiting=Awaiting.GUIDANCE,
        revision=2,
        agent_invocations=5,
        findings=(record("F1", FindingState.GUIDANCE_REQUIRED, repair_attempts=1),),
    )
    decision = reduce(snapshot, GuidanceAccepted(finding_ids=("F1",)), POLICY)
    assert decision.command == InvokeAuthor(action=AgentAction.REPAIR)
    assert decision.snapshot.finding("F1").guidance_given is True


def test_still_present_after_guided_repair_stops_handoff_required() -> None:
    snapshot = reviewing(
        record("F1", FindingState.FIX_CLAIMED, repair_attempts=2, guidance_given=True)
    )
    decision = reduce(snapshot, review_event({"F1": PriorOutcome.STILL_PRESENT}), POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.HANDOFF_REQUIRED
    assert decision.command is None  # nothing lands, no in-place role swap


def test_author_blocked_after_guidance_stops_handoff_required() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.REPAIRING,
        awaiting=Awaiting.FOLD,
        revision=2,
        agent_invocations=6,
        findings=(record("F1", FindingState.GUIDANCE_REQUIRED, guidance_given=True),),
    )
    event = FoldAccepted(
        revision=3, dispositions=(("F1", Disposition.BLOCKED_NEEDS_TECHNICAL_GUIDANCE),)
    )
    decision = reduce(snapshot, event, POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.HANDOFF_REQUIRED


def test_author_may_request_guidance_before_any_repair() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.REPAIRING,
        awaiting=Awaiting.FOLD,
        revision=1,
        agent_invocations=3,
        findings=(record("F1", FindingState.OPEN),),
    )
    event = FoldAccepted(
        revision=2, dispositions=(("F1", Disposition.BLOCKED_NEEDS_TECHNICAL_GUIDANCE),)
    )
    decision = reduce(snapshot, event, POLICY)
    assert decision.command == InvokeGuidance()
    assert decision.snapshot.state is LaneState.REVIEWING
    assert decision.snapshot.awaiting is Awaiting.GUIDANCE


# -- rejection, clarification, adjudication ---------------------------------


def test_first_rejection_goes_back_to_the_reviewer() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.REPAIRING,
        awaiting=Awaiting.FOLD,
        revision=1,
        agent_invocations=3,
        findings=(record("F1", FindingState.OPEN),),
    )
    event = FoldAccepted(revision=2, dispositions=(("F1", Disposition.REJECTED_WITH_REASON),))
    decision = reduce(snapshot, event, POLICY)
    assert decision.command == VerifyRevision()  # verify then re-review assesses the rejection
    updated = decision.snapshot.finding("F1")
    assert updated.state is FindingState.REJECTED_PENDING_REVIEW
    assert updated.consecutive_rejections == 1


def test_reviewer_accepting_rejection_closes_and_lane_can_converge() -> None:
    snapshot = reviewing(
        record("F1", FindingState.REJECTED_PENDING_REVIEW, consecutive_rejections=1)
    )
    decision = reduce(
        snapshot,
        review_event({"F1": PriorOutcome.REVIEWER_ACCEPTS_REJECTION}, verdict="CLEAN"),
        POLICY,
    )
    assert decision.snapshot.finding("F1").state is FindingState.CLOSED_NOT_A_DEFECT
    assert decision.snapshot.state is LaneState.LANDING
    assert decision.reason is ReasonCode.CONVERGED


def test_reviewer_disagreement_routes_clarification_back_to_repair() -> None:
    snapshot = reviewing(
        record("F1", FindingState.REJECTED_PENDING_REVIEW, consecutive_rejections=1)
    )
    decision = reduce(snapshot, review_event({"F1": PriorOutcome.REVIEWER_DISAGREES}), POLICY)
    assert decision.command == InvokeAuthor(action=AgentAction.REPAIR)
    assert decision.snapshot.finding("F1").state is FindingState.TECHNICAL_CLARIFICATION


def test_second_consecutive_rejection_requires_adjudication() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.REPAIRING,
        awaiting=Awaiting.FOLD,
        revision=2,
        agent_invocations=5,
        findings=(
            record("F1", FindingState.TECHNICAL_CLARIFICATION, consecutive_rejections=1),
        ),
    )
    event = FoldAccepted(revision=3, dispositions=(("F1", Disposition.REJECTED_WITH_REASON),))
    decision = reduce(snapshot, event, POLICY)
    assert decision.snapshot.state is LaneState.WAIT_OPERATOR
    assert decision.reason is ReasonCode.ADJUDICATION_REQUIRED


def test_folding_resets_the_consecutive_rejection_count() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.REPAIRING,
        awaiting=Awaiting.FOLD,
        revision=2,
        agent_invocations=5,
        findings=(
            record("F1", FindingState.TECHNICAL_CLARIFICATION, consecutive_rejections=1),
        ),
    )
    event = FoldAccepted(revision=3, dispositions=(("F1", Disposition.FOLDED),))
    decision = reduce(snapshot, event, POLICY)
    updated = decision.snapshot.finding("F1")
    assert updated.consecutive_rejections == 0
    assert updated.state is FindingState.FIX_CLAIMED


# -- reopening and ping-pong -------------------------------------------------


def test_first_reopen_routes_to_repair_with_the_same_id() -> None:
    snapshot = reviewing(record("F1", FindingState.VERIFIED_RESOLVED, repair_attempts=1))
    decision = reduce(snapshot, review_event({"F1": PriorOutcome.REOPENED}), POLICY)
    assert decision.command == InvokeAuthor(action=AgentAction.REPAIR)
    updated = decision.snapshot.finding("F1")
    assert updated.state is FindingState.REOPENED
    assert updated.reopen_count == 1


def test_second_reopen_is_ping_pong_and_waits_for_the_operator() -> None:
    snapshot = reviewing(
        record("F1", FindingState.VERIFIED_RESOLVED, repair_attempts=2, reopen_count=1)
    )
    decision = reduce(snapshot, review_event({"F1": PriorOutcome.REOPENED}), POLICY)
    assert decision.snapshot.state is LaneState.WAIT_OPERATOR
    assert decision.reason is ReasonCode.PING_PONG


# -- operator flags ----------------------------------------------------------


def test_requires_ruling_finding_waits_for_the_operator() -> None:
    snapshot = reviewing()
    event = review_event({}, new=(NewFinding("F1", Severity.P1, "t", requires_ruling=True),))
    decision = reduce(snapshot, event, POLICY)
    assert decision.snapshot.state is LaneState.WAIT_OPERATOR
    assert decision.reason is ReasonCode.REQUIRES_RULING


def test_earlier_phase_gap_stops() -> None:
    snapshot = reviewing()
    event = review_event({}, new=(NewFinding("F1", Severity.P1, "t", earlier_phase_gap=True),))
    decision = reduce(snapshot, event, POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.EARLIER_PHASE_GAP


def test_unknown_contract_finding_waits_for_the_operator() -> None:
    snapshot = reviewing()
    event = review_event({}, new=(NewFinding("F1", Severity.P1, "t", unknown_contract=True),))
    decision = reduce(snapshot, event, POLICY)
    assert decision.snapshot.state is LaneState.WAIT_OPERATOR
    assert decision.reason is ReasonCode.UNKNOWN_CONTRACT


def test_unknown_contract_disposition_waits_for_the_operator() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.REPAIRING,
        awaiting=Awaiting.FOLD,
        revision=1,
        agent_invocations=3,
        findings=(record("F1", FindingState.OPEN),),
    )
    event = FoldAccepted(revision=2, dispositions=(("F1", Disposition.UNKNOWN_CONTRACT),))
    decision = reduce(snapshot, event, POLICY)
    assert decision.snapshot.state is LaneState.WAIT_OPERATOR
    assert decision.reason is ReasonCode.UNKNOWN_CONTRACT


def test_scope_expansion_and_operator_action_dispositions_wait() -> None:
    for disposition, reason in (
        (Disposition.REQUIRES_SCOPE_EXPANSION, ReasonCode.REQUIRES_SCOPE_EXPANSION),
        (Disposition.REQUIRES_OPERATOR_ACTION, ReasonCode.REQUIRES_OPERATOR_ACTION),
    ):
        snapshot = LaneSnapshot(
            state=LaneState.REPAIRING,
            awaiting=Awaiting.FOLD,
            revision=1,
            agent_invocations=3,
            findings=(record("F1", FindingState.OPEN),),
        )
        decision = reduce(
            snapshot, FoldAccepted(revision=2, dispositions=(("F1", disposition),)), POLICY
        )
        assert decision.snapshot.state is LaneState.WAIT_OPERATOR
        assert decision.reason is reason


# -- convergence guard -------------------------------------------------------


def test_no_convergence_while_any_blocker_is_unresolved() -> None:
    snapshot = reviewing(
        record("F1", FindingState.FIX_CLAIMED, repair_attempts=1),
        record("F2", FindingState.FIX_CLAIMED, repair_attempts=1),
    )
    event = review_event(
        {"F1": PriorOutcome.VERIFIED_RESOLVED, "F2": PriorOutcome.STILL_PRESENT}
    )
    decision = reduce(snapshot, event, POLICY)
    assert decision.snapshot.state is not LaneState.LANDING
    assert decision.snapshot.state is not LaneState.COMPLETED
