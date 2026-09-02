"""Reducer table tests: every specified transition, and fail-closed everywhere else."""

from __future__ import annotations

import pytest

from orchestrator import reducer as reducer_module
from orchestrator.model import (
    AgentAction,
    ArtifactRejected,
    AuthorResultAccepted,
    Awaiting,
    Disposition,
    FindingOrigin,
    FindingRecord,
    FindingState,
    FoldAccepted,
    GateCategory,
    GuidanceAccepted,
    InvokeAuthor,
    InvokeReviewer,
    LandRevision,
    LaneAuthorized,
    LanePolicy,
    LaneSnapshot,
    LaneState,
    Lens,
    NewFinding,
    PriorAssessment,
    PriorOutcome,
    ReasonCode,
    ReviewAccepted,
    RevisionVerified,
    Severity,
    VerifyRevision,
)
from orchestrator.reducer import reduce

DESIGN_POLICY = LanePolicy(
    lane_kind="design",
    accepted_gate_categories=frozenset(
        {GateCategory.PASS, GateCategory.DOCS_INCONCLUSIVE_SCOPE_PASS}
    ),
)


def verified(category: GateCategory = GateCategory.DOCS_INCONCLUSIVE_SCOPE_PASS, **facts: bool) -> RevisionVerified:
    defaults = dict(
        descends_from_base=True,
        contiguous=True,
        no_foreign_commits=True,
        files_in_scope=True,
        worktree_clean=True,
    )
    defaults.update(facts)
    failed = () if category in (GateCategory.PASS, GateCategory.DOCS_INCONCLUSIVE_SCOPE_PASS, GateCategory.UNKNOWN) else ("scope",)
    return RevisionVerified(gate_category=category, failed_checks=failed, **defaults)


def author_accepted(revision: int = 1, unknown: bool = False) -> AuthorResultAccepted:
    return AuthorResultAccepted(
        revision=revision,
        commit=f"{revision:040x}",
        tree_digest=f"{revision + 5000:040x}",
        has_unknown_contracts=unknown,
    )


def fold_accepted(
    revision: int = 2,
    dispositions: tuple = (),
    unknown: bool = False,
) -> FoldAccepted:
    return FoldAccepted(
        revision=revision,
        commit=f"{revision:040x}",
        tree_digest=f"{revision + 5000:040x}",
        dispositions=dispositions,
        has_unknown_contracts=unknown,
    )


def clean_review(prior: dict[str, PriorOutcome] | None = None) -> ReviewAccepted:
    return ReviewAccepted(
        lens=Lens.GATING,
        verdict="CLEAN",
        has_scope_observations=False,
        new_findings=(),
        prior_findings=tuple(
            PriorAssessment(fid, outcome) for fid, outcome in sorted((prior or {}).items())
        ),
    )


def finding(
    fid: str = "F1",
    state: FindingState = FindingState.OPEN,
    severity: Severity = Severity.P1,
    **overrides: object,
) -> FindingRecord:
    return FindingRecord(finding_id=fid, severity=severity, title=fid, state=state, **overrides)


SAMPLE_EVENTS = {
    LaneAuthorized: LaneAuthorized(),
    AuthorResultAccepted: author_accepted(),
    FoldAccepted: fold_accepted(),
    RevisionVerified: verified(),
    ReviewAccepted: clean_review(),
    GuidanceAccepted: GuidanceAccepted(finding_ids=()),
    ArtifactRejected: ArtifactRejected(artifact=Awaiting.AUTHOR_RESULT),
}

#: The complete specified transition table. Everything else must fail closed.
SPECIFIED = {
    (LaneState.AUTHORIZED, LaneAuthorized),
    (LaneState.AUTHORING, AuthorResultAccepted),
    (LaneState.AUTHORING, ArtifactRejected),
    (LaneState.REPAIRING, FoldAccepted),
    (LaneState.REPAIRING, ArtifactRejected),
    (LaneState.VERIFYING, RevisionVerified),
    (LaneState.VERIFYING, ArtifactRejected),
    (LaneState.REVIEWING, ReviewAccepted),
    (LaneState.REVIEWING, GuidanceAccepted),
    (LaneState.REVIEWING, ArtifactRejected),
}


def test_handler_table_matches_the_specified_contract() -> None:
    assert set(reducer_module._HANDLERS) == SPECIFIED


@pytest.mark.parametrize("state", list(LaneState))
@pytest.mark.parametrize("event_type", list(SAMPLE_EVENTS))
def test_unspecified_pairs_fail_closed(state: LaneState, event_type: type) -> None:
    if (state, event_type) in SPECIFIED:
        return
    snapshot = LaneSnapshot(state=state)
    decision = reduce(snapshot, SAMPLE_EVENTS[event_type], DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.UNSPECIFIED_TRANSITION
    assert decision.command is None


def test_authorized_lane_invokes_author_and_counts_the_invocation() -> None:
    decision = reduce(LaneSnapshot(), LaneAuthorized(), DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.AUTHORING
    assert decision.snapshot.awaiting is Awaiting.AUTHOR_RESULT
    assert decision.command == InvokeAuthor(action=AgentAction.AUTHOR)
    assert decision.snapshot.agent_invocations == 1


def test_author_result_moves_to_verifying_and_pins_the_candidate() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.AUTHORING, awaiting=Awaiting.AUTHOR_RESULT, agent_invocations=1
    )
    event = author_accepted()
    decision = reduce(snapshot, event, DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.VERIFYING
    assert decision.snapshot.revision == 1
    assert decision.snapshot.current_sha == event.commit
    assert decision.snapshot.current_tree == event.tree_digest
    assert decision.command == VerifyRevision()
    assert decision.snapshot.agent_invocations == 1  # verify spawns no agent


def test_author_unknown_contract_waits_for_the_operator() -> None:
    snapshot = LaneSnapshot(state=LaneState.AUTHORING, awaiting=Awaiting.AUTHOR_RESULT)
    decision = reduce(snapshot, author_accepted(unknown=True), DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.WAIT_OPERATOR
    assert decision.reason is ReasonCode.UNKNOWN_CONTRACT


def test_fold_unknown_contract_report_waits_for_the_operator() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.REPAIRING,
        awaiting=Awaiting.FOLD,
        revision=1,
        findings=(finding("F1"),),
    )
    event = fold_accepted(dispositions=(("F1", Disposition.FOLDED),), unknown=True)
    decision = reduce(snapshot, event, DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.WAIT_OPERATOR
    assert decision.reason is ReasonCode.UNKNOWN_CONTRACT


def test_accepted_gate_moves_to_review_and_counts_the_round() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.VERIFYING, awaiting=Awaiting.GATE_RESULT, revision=1, agent_invocations=1
    )
    decision = reduce(snapshot, verified(), DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.REVIEWING
    assert decision.snapshot.awaiting is Awaiting.REVIEW
    assert decision.command == InvokeReviewer()
    assert decision.snapshot.review_round == 1
    assert decision.snapshot.agent_invocations == 2


@pytest.mark.parametrize(
    "fact",
    [
        "descends_from_base",
        "contiguous",
        "no_foreign_commits",
        "files_in_scope",
        "worktree_clean",
    ],
)
def test_git_fact_failure_stops(fact: str) -> None:
    snapshot = LaneSnapshot(state=LaneState.VERIFYING, awaiting=Awaiting.GATE_RESULT)
    decision = reduce(snapshot, verified(**{fact: False}), DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.GIT_FACTS_FAILED


def test_fixable_gate_creates_system_finding_and_requests_repair() -> None:
    snapshot = LaneSnapshot(state=LaneState.VERIFYING, awaiting=Awaiting.GATE_RESULT, revision=1)
    decision = reduce(snapshot, verified(GateCategory.FIXABLE_LINT), DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.REPAIRING
    assert decision.command == InvokeAuthor(action=AgentAction.REPAIR)
    assert decision.reason is ReasonCode.GATE_FIXABLE
    record = decision.snapshot.finding("SYS-FIXABLE_LINT")
    assert record is not None and record.state is FindingState.OPEN and record.blocking
    assert record.origin is FindingOrigin.GATE
    # Gate-origin findings stay in the reviewer's exact set (protocol §2): the
    # passing gate supplies closure evidence, the reviewer reconciles it.
    assert "SYS-FIXABLE_LINT" in decision.snapshot.historical_blocking_states()
    assert "SYS-FIXABLE_LINT" in decision.snapshot.fold_outstanding_ids()


@pytest.mark.parametrize(
    "category,reason",
    [
        (GateCategory.SCOPE, ReasonCode.GATE_BLOCKING),
        (GateCategory.BASE, ReasonCode.GATE_BLOCKING),
        (GateCategory.DEPENDENCY, ReasonCode.GATE_BLOCKING),
        (GateCategory.FOREIGN_COMMIT, ReasonCode.GATE_BLOCKING),
        (GateCategory.UNKNOWN, ReasonCode.GATE_UNKNOWN),
    ],
)
def test_blocking_and_unknown_gate_categories_stop(
    category: GateCategory, reason: ReasonCode
) -> None:
    snapshot = LaneSnapshot(state=LaneState.VERIFYING, awaiting=Awaiting.GATE_RESULT)
    decision = reduce(snapshot, verified(category), DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is reason


def test_gate_category_not_accepted_by_lane_policy_stops() -> None:
    implementation = LanePolicy(
        lane_kind="implementation", accepted_gate_categories=frozenset({GateCategory.PASS})
    )
    snapshot = LaneSnapshot(state=LaneState.VERIFYING, awaiting=Awaiting.GATE_RESULT)
    decision = reduce(snapshot, verified(GateCategory.DOCS_INCONCLUSIVE_SCOPE_PASS), implementation)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.GATE_BLOCKING


def test_clean_gating_review_converges_to_landing() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.REVIEWING, awaiting=Awaiting.REVIEW, revision=1, review_round=1
    )
    decision = reduce(snapshot, clean_review(), DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.LANDING
    assert decision.command == LandRevision()
    assert decision.reason is ReasonCode.CONVERGED


def test_non_gating_clean_review_never_converges() -> None:
    snapshot = LaneSnapshot(state=LaneState.REVIEWING, awaiting=Awaiting.REVIEW)
    event = ReviewAccepted(
        lens=Lens.ADVISORY,
        verdict="CLEAN",
        has_scope_observations=False,
        new_findings=(),
        prior_findings=(),
    )
    decision = reduce(snapshot, event, DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.NON_GATING_REVIEW


def test_scope_observation_stops() -> None:
    snapshot = LaneSnapshot(state=LaneState.REVIEWING, awaiting=Awaiting.REVIEW)
    event = ReviewAccepted(
        lens=Lens.GATING,
        verdict="FINDINGS",
        has_scope_observations=True,
        new_findings=(NewFinding("F1", Severity.P1, "t"),),
        prior_findings=(),
    )
    decision = reduce(snapshot, event, DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.SCOPE_OBSERVATION


def test_blocking_findings_route_to_repair() -> None:
    snapshot = LaneSnapshot(state=LaneState.REVIEWING, awaiting=Awaiting.REVIEW, review_round=1)
    event = ReviewAccepted(
        lens=Lens.GATING,
        verdict="FINDINGS",
        has_scope_observations=False,
        new_findings=(NewFinding("F1", Severity.P1, "t"), NewFinding("F2", Severity.P3, "t")),
        prior_findings=(),
    )
    decision = reduce(snapshot, event, DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.REPAIRING
    assert decision.command == InvokeAuthor(action=AgentAction.REPAIR)
    assert decision.snapshot.fold_outstanding_ids() == frozenset({"F1"})  # P3 never folds


def test_p3_only_review_converges() -> None:
    snapshot = LaneSnapshot(state=LaneState.REVIEWING, awaiting=Awaiting.REVIEW, review_round=1)
    event = ReviewAccepted(
        lens=Lens.GATING,
        verdict="CLEAN",
        has_scope_observations=False,
        new_findings=(NewFinding("F9", Severity.P3, "minor"),),
        prior_findings=(),
    )
    decision = reduce(snapshot, event, DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.LANDING
    assert decision.reason is ReasonCode.CONVERGED


def test_findings_verdict_without_blockers_is_inconsistent() -> None:
    snapshot = LaneSnapshot(state=LaneState.REVIEWING, awaiting=Awaiting.REVIEW)
    event = ReviewAccepted(
        lens=Lens.GATING,
        verdict="FINDINGS",
        has_scope_observations=False,
        new_findings=(),
        prior_findings=(),
    )
    decision = reduce(snapshot, event, DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.VERDICT_INCONSISTENT


def test_review_while_awaiting_guidance_fails_closed() -> None:
    snapshot = LaneSnapshot(state=LaneState.REVIEWING, awaiting=Awaiting.GUIDANCE)
    decision = reduce(snapshot, clean_review(), DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.UNSPECIFIED_TRANSITION


def test_guidance_while_awaiting_review_fails_closed() -> None:
    snapshot = LaneSnapshot(state=LaneState.REVIEWING, awaiting=Awaiting.REVIEW)
    decision = reduce(snapshot, GuidanceAccepted(finding_ids=("F1",)), DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.UNSPECIFIED_TRANSITION


def test_mismatched_artifact_rejection_fails_closed() -> None:
    snapshot = LaneSnapshot(state=LaneState.AUTHORING, awaiting=Awaiting.AUTHOR_RESULT)
    decision = reduce(snapshot, ArtifactRejected(artifact=Awaiting.REVIEW), DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.UNSPECIFIED_TRANSITION


def test_malformed_artifact_retries_once_then_stops() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.AUTHORING, awaiting=Awaiting.AUTHOR_RESULT, agent_invocations=1
    )
    retry = reduce(snapshot, ArtifactRejected(artifact=Awaiting.AUTHOR_RESULT), DESIGN_POLICY)
    assert retry.reason is ReasonCode.MALFORMED_ARTIFACT_RETRY
    assert retry.command == InvokeAuthor(action=AgentAction.AUTHOR)
    assert retry.snapshot.agent_invocations == 2  # a retry spawn still counts
    stop = reduce(
        retry.snapshot, ArtifactRejected(artifact=Awaiting.AUTHOR_RESULT), DESIGN_POLICY
    )
    assert stop.snapshot.state is LaneState.STOPPED
    assert stop.reason is ReasonCode.MALFORMED_ARTIFACT_STOP


def test_malformed_gate_result_retry_spawns_no_agent() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.VERIFYING, awaiting=Awaiting.GATE_RESULT, agent_invocations=3
    )
    retry = reduce(snapshot, ArtifactRejected(artifact=Awaiting.GATE_RESULT), DESIGN_POLICY)
    assert retry.command == VerifyRevision()
    assert retry.snapshot.agent_invocations == 3


def test_max_rounds_trips_to_stop_never_forced_acceptance() -> None:
    snapshot = LaneSnapshot(
        state=LaneState.VERIFYING, awaiting=Awaiting.GATE_RESULT, review_round=10
    )
    decision = reduce(snapshot, verified(), DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.MAX_ROUNDS_EXCEEDED


def test_max_invocations_trips_to_stop() -> None:
    snapshot = LaneSnapshot(agent_invocations=40)
    decision = reduce(snapshot, LaneAuthorized(), DESIGN_POLICY)
    assert decision.snapshot.state is LaneState.STOPPED
    assert decision.reason is ReasonCode.MAX_INVOCATIONS_EXCEEDED


def test_every_agent_command_increments_the_counter_exactly_once() -> None:
    # author (1) -> verify (0) -> review (1) -> repair (1) -> guidance (1)
    d1 = reduce(LaneSnapshot(), LaneAuthorized(), DESIGN_POLICY)
    d2 = reduce(d1.snapshot, author_accepted(), DESIGN_POLICY)
    d3 = reduce(d2.snapshot, verified(), DESIGN_POLICY)
    event = ReviewAccepted(
        lens=Lens.GATING,
        verdict="FINDINGS",
        has_scope_observations=False,
        new_findings=(NewFinding("F1", Severity.P1, "t"),),
        prior_findings=(),
    )
    d4 = reduce(d3.snapshot, event, DESIGN_POLICY)
    assert [d.snapshot.agent_invocations for d in (d1, d2, d3, d4)] == [1, 1, 2, 3]
