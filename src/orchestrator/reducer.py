"""Pure lane state transitions, finding lifecycle, and counters.

``reduce(snapshot, event, policy)`` is a pure function: the same inputs always
produce the same ``TransitionDecision``. It performs no I/O, reads no clock,
and never interprets prose. Unspecified (state, event) pairs fail closed to
``STOPPED`` — silence is never a PASS.

Transition authority: DETERMINISTIC_PYTHON_APPLICATION_ARCHITECTURE.md §5 and
REVIEW_REPAIR_CONVERGENCE_PROTOCOL.md §3, §8, §12-§13.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from orchestrator.model import (
    AgentAction,
    ArtifactRejected,
    AuthorResultAccepted,
    Awaiting,
    Command,
    Disposition,
    Event,
    FindingOrigin,
    FindingRecord,
    FindingState,
    FoldAccepted,
    GateCategory,
    FIXABLE_GATE_CATEGORIES,
    GuidanceAccepted,
    InvokeAuthor,
    InvokeGuidance,
    InvokeReviewer,
    LandRevision,
    LaneAuthorized,
    LanePolicy,
    LaneSnapshot,
    LaneState,
    Lens,
    OpenHumanGate,
    PriorOutcome,
    ReasonCode,
    ReviewAccepted,
    RevisionVerified,
    Severity,
    TransitionDecision,
    VerifyRevision,
)

_BLOCKING_GATE_CATEGORIES = frozenset(
    {GateCategory.SCOPE, GateCategory.BASE, GateCategory.DEPENDENCY, GateCategory.FOREIGN_COMMIT}
)

#: Fold dispositions that require the operator (architecture §5.2).
_WAIT_OPERATOR_DISPOSITIONS: tuple[tuple[Disposition, ReasonCode], ...] = (
    (Disposition.UNKNOWN_CONTRACT, ReasonCode.UNKNOWN_CONTRACT),
    (Disposition.REQUIRES_SCOPE_EXPANSION, ReasonCode.REQUIRES_SCOPE_EXPANSION),
    (Disposition.REQUIRES_OPERATOR_ACTION, ReasonCode.REQUIRES_OPERATOR_ACTION),
)


def reduce(snapshot: LaneSnapshot, event: Event, policy: LanePolicy) -> TransitionDecision:
    """Decide the next state and command. Fail closed on anything unspecified."""
    key = (snapshot.state, type(event))
    handler = _HANDLERS.get(key)
    if handler is None:
        return _fail_closed(snapshot, event)
    return handler(snapshot, event, policy)


# ---------------------------------------------------------------------------
# Decision helpers
# ---------------------------------------------------------------------------


def _fail_closed(snapshot: LaneSnapshot, event: Event) -> TransitionDecision:
    return TransitionDecision(
        snapshot=replace(snapshot, state=LaneState.STOPPED, awaiting=None),
        command=None,
        reason=ReasonCode.UNSPECIFIED_TRANSITION,
        inputs={"state": snapshot.state.value, "event": event.kind},
    )


def _stopped(snapshot: LaneSnapshot, reason: ReasonCode, inputs: dict[str, Any]) -> TransitionDecision:
    return TransitionDecision(
        snapshot=replace(snapshot, state=LaneState.STOPPED, awaiting=None),
        command=None,
        reason=reason,
        inputs=inputs,
    )


def _wait_operator(
    snapshot: LaneSnapshot, reason: ReasonCode, inputs: dict[str, Any]
) -> TransitionDecision:
    return TransitionDecision(
        snapshot=replace(snapshot, state=LaneState.WAIT_OPERATOR, awaiting=None),
        command=OpenHumanGate(reason=reason),
        reason=reason,
        inputs=inputs,
    )


def _issue_agent(
    snapshot: LaneSnapshot,
    policy: LanePolicy,
    state: LaneState,
    awaiting: Awaiting,
    command: Command,
    reason: ReasonCode,
    inputs: dict[str, Any],
    *,
    count_round: bool = False,
    reset_retries: bool = True,
) -> TransitionDecision:
    """Issue an agent-spawning command, enforcing both work bounds first.

    The invocation counter increments with the issued command — immediately
    before the spawn — and every spawned process counts, including a
    malformed-artifact retry (design §3.1; protocol §12).
    """
    if count_round and snapshot.review_round >= policy.max_rounds:
        inputs = dict(inputs, review_round=snapshot.review_round, max_rounds=policy.max_rounds)
        return _stopped(snapshot, ReasonCode.MAX_ROUNDS_EXCEEDED, inputs)
    if snapshot.agent_invocations >= policy.max_agent_invocations:
        inputs = dict(
            inputs,
            agent_invocations=snapshot.agent_invocations,
            max_agent_invocations=policy.max_agent_invocations,
        )
        return _stopped(snapshot, ReasonCode.MAX_INVOCATIONS_EXCEEDED, inputs)
    next_snapshot = replace(
        snapshot,
        state=state,
        awaiting=awaiting,
        agent_invocations=snapshot.agent_invocations + 1,
        review_round=snapshot.review_round + 1 if count_round else snapshot.review_round,
        artifact_retries=0 if reset_retries else snapshot.artifact_retries,
    )
    return TransitionDecision(next_snapshot, command, reason, inputs)


def _replace_finding(
    findings: tuple[FindingRecord, ...], updated: FindingRecord
) -> tuple[FindingRecord, ...]:
    return tuple(updated if f.finding_id == updated.finding_id else f for f in findings)


def _sorted_findings(findings: tuple[FindingRecord, ...]) -> tuple[FindingRecord, ...]:
    return tuple(sorted(findings, key=lambda f: f.finding_id))


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _on_lane_authorized(
    snapshot: LaneSnapshot, event: LaneAuthorized, policy: LanePolicy
) -> TransitionDecision:
    return _issue_agent(
        snapshot,
        policy,
        LaneState.AUTHORING,
        Awaiting.AUTHOR_RESULT,
        InvokeAuthor(action=AgentAction.AUTHOR),
        ReasonCode.LANE_AUTHORIZED,
        {"lane_kind": policy.lane_kind},
    )


def _on_author_result(
    snapshot: LaneSnapshot, event: AuthorResultAccepted, policy: LanePolicy
) -> TransitionDecision:
    working = replace(
        snapshot,
        revision=event.revision,
        current_sha=event.commit,
        current_tree=event.tree_digest,
        artifact_retries=0,
    )
    inputs = {
        "revision": event.revision,
        "commit": event.commit,
        "tree_digest": event.tree_digest,
        "has_unknown_contracts": event.has_unknown_contracts,
    }
    if event.has_unknown_contracts:
        # Guessing a provider contract is prohibited; package for the operator.
        return _wait_operator(working, ReasonCode.UNKNOWN_CONTRACT, inputs)
    next_snapshot = replace(working, state=LaneState.VERIFYING, awaiting=Awaiting.GATE_RESULT)
    return TransitionDecision(
        next_snapshot, VerifyRevision(), ReasonCode.AUTHOR_RESULT_ACCEPTED, inputs
    )


def _on_fold(snapshot: LaneSnapshot, event: FoldAccepted, policy: LanePolicy) -> TransitionDecision:
    findings = snapshot.findings
    for finding_id, disposition in event.dispositions:
        record = snapshot.finding(finding_id)
        if record is None:
            return _stopped(
                snapshot,
                ReasonCode.MALFORMED_ARTIFACT_STOP,
                {"unknown_finding_id": finding_id},
            )
        if disposition is Disposition.FOLDED:
            record = replace(
                record,
                state=FindingState.FIX_CLAIMED,
                repair_attempts=record.repair_attempts + 1,
                consecutive_rejections=0,
            )
        elif disposition is Disposition.REJECTED_WITH_REASON:
            record = replace(
                record,
                state=FindingState.REJECTED_PENDING_REVIEW,
                consecutive_rejections=record.consecutive_rejections + 1,
            )
        elif disposition is Disposition.BLOCKED_NEEDS_TECHNICAL_GUIDANCE:
            record = replace(record, state=FindingState.GUIDANCE_REQUIRED)
        # UNKNOWN_CONTRACT / REQUIRES_SCOPE_EXPANSION / REQUIRES_OPERATOR_ACTION
        # keep the finding state; the lane waits for the operator below.
        findings = _replace_finding(findings, record)
    working = replace(
        snapshot,
        findings=findings,
        revision=event.revision,
        current_sha=event.commit,
        current_tree=event.tree_digest,
        artifact_retries=0,
    )
    inputs: dict[str, Any] = {
        "revision": event.revision,
        "commit": event.commit,
        "tree_digest": event.tree_digest,
        "dispositions": {fid: disp.value for fid, disp in event.dispositions},
        "has_unknown_contracts": event.has_unknown_contracts,
    }

    adjudication = [f.finding_id for f in working.findings if f.consecutive_rejections >= 2]
    if adjudication:
        return _wait_operator(
            working, ReasonCode.ADJUDICATION_REQUIRED, dict(inputs, finding_ids=adjudication)
        )
    dispositions = {disp for _, disp in event.dispositions}
    if event.has_unknown_contracts and Disposition.UNKNOWN_CONTRACT not in dispositions:
        # A top-level unknown-contract report is a STOP even without a
        # matching per-finding disposition.
        return _wait_operator(working, ReasonCode.UNKNOWN_CONTRACT, inputs)
    for disposition, reason in _WAIT_OPERATOR_DISPOSITIONS:
        if disposition in dispositions:
            return _wait_operator(working, reason, inputs)
    blocked_after_guidance = [
        f.finding_id
        for f in working.findings
        if f.blocking and f.state is FindingState.GUIDANCE_REQUIRED and f.guidance_given
    ]
    if blocked_after_guidance:
        # Guidance could not unblock the author: stop for the separately
        # authorized cross-lane handoff (protocol §7).
        return _stopped(
            working, ReasonCode.HANDOFF_REQUIRED, dict(inputs, finding_ids=blocked_after_guidance)
        )
    guidance_ids = [
        f.finding_id
        for f in working.findings
        if f.blocking and f.state is FindingState.GUIDANCE_REQUIRED and not f.guidance_given
    ]
    if guidance_ids:
        return _issue_agent(
            working,
            policy,
            LaneState.REVIEWING,
            Awaiting.GUIDANCE,
            InvokeGuidance(),
            ReasonCode.GUIDANCE_REQUIRED,
            dict(inputs, finding_ids=guidance_ids),
        )
    next_snapshot = replace(working, state=LaneState.VERIFYING, awaiting=Awaiting.GATE_RESULT)
    return TransitionDecision(next_snapshot, VerifyRevision(), ReasonCode.FOLD_ACCEPTED, inputs)


def _on_revision_verified(
    snapshot: LaneSnapshot, event: RevisionVerified, policy: LanePolicy
) -> TransitionDecision:
    inputs: dict[str, Any] = {
        "gate_category": event.gate_category.value,
        "descends_from_base": event.descends_from_base,
        "contiguous": event.contiguous,
        "no_foreign_commits": event.no_foreign_commits,
        "files_in_scope": event.files_in_scope,
        "worktree_clean": event.worktree_clean,
        "failed_checks": list(event.failed_checks),
    }
    if not event.git_facts_ok:
        return _stopped(snapshot, ReasonCode.GIT_FACTS_FAILED, inputs)
    category = event.gate_category
    if category in policy.accepted_gate_categories:
        # The passing gate IS the resolution evidence for gate-origin findings
        # whose fix the author claimed (deterministic, no reviewer judgement).
        findings = snapshot.findings
        for record in snapshot.findings:
            if (
                record.origin is FindingOrigin.GATE
                and record.state is FindingState.FIX_CLAIMED
            ):
                findings = _replace_finding(
                    findings, replace(record, state=FindingState.VERIFIED_RESOLVED)
                )
        return _issue_agent(
            replace(snapshot, findings=findings, artifact_retries=0),
            policy,
            LaneState.REVIEWING,
            Awaiting.REVIEW,
            InvokeReviewer(),
            ReasonCode.REVISION_VERIFIED,
            inputs,
            count_round=True,
        )
    if category in FIXABLE_GATE_CATEGORIES:
        return _on_fixable_gate(snapshot, category, policy, inputs)
    if category in _BLOCKING_GATE_CATEGORIES:
        return _stopped(snapshot, ReasonCode.GATE_BLOCKING, inputs)
    if category is GateCategory.UNKNOWN:
        return _stopped(snapshot, ReasonCode.GATE_UNKNOWN, inputs)
    # A recognised category this lane's policy does not accept (e.g. a
    # docs-only pass on an implementation lane) is not routable: STOP.
    return _stopped(snapshot, ReasonCode.GATE_BLOCKING, inputs)


def _on_fixable_gate(
    snapshot: LaneSnapshot,
    category: GateCategory,
    policy: LanePolicy,
    inputs: dict[str, Any],
) -> TransitionDecision:
    """Route a fixable gate failure through the same per-finding lifecycle as a
    reviewer finding: repair -> guidance -> handoff, and reopen -> ping-pong
    (protocol §8; a system finding must not bypass the escalation bounds)."""
    finding_id = f"SYS-{category.value}"
    inputs = dict(inputs, finding_id=finding_id)
    record = snapshot.finding(finding_id)
    if record is None:
        record = FindingRecord(
            finding_id=finding_id,
            severity=Severity.P1,
            title=f"target gate failed: {category.value}",
            state=FindingState.OPEN,
            origin=FindingOrigin.GATE,
        )
        findings = _sorted_findings(snapshot.findings + (record,))
        return _issue_agent(
            replace(snapshot, findings=findings, artifact_retries=0),
            policy,
            LaneState.REPAIRING,
            Awaiting.FOLD,
            InvokeAuthor(action=AgentAction.REPAIR),
            ReasonCode.GATE_FIXABLE,
            inputs,
        )
    if record.state is FindingState.FIX_CLAIMED:
        # The claimed repair did not fix the gate.
        if record.guidance_given:
            return _stopped(snapshot, ReasonCode.HANDOFF_REQUIRED, inputs)
        findings = _replace_finding(
            snapshot.findings, replace(record, state=FindingState.GUIDANCE_REQUIRED)
        )
        return _issue_agent(
            replace(snapshot, findings=findings, artifact_retries=0),
            policy,
            LaneState.REVIEWING,
            Awaiting.GUIDANCE,
            InvokeGuidance(),
            ReasonCode.GUIDANCE_REQUIRED,
            inputs,
        )
    if record.state is FindingState.VERIFIED_RESOLVED:
        record = replace(
            record, state=FindingState.REOPENED, reopen_count=record.reopen_count + 1
        )
        findings = _replace_finding(snapshot.findings, record)
        working = replace(snapshot, findings=findings, artifact_retries=0)
        if record.reopen_count >= 2:
            return _wait_operator(working, ReasonCode.PING_PONG, inputs)
        return _issue_agent(
            working,
            policy,
            LaneState.REPAIRING,
            Awaiting.FOLD,
            InvokeAuthor(action=AgentAction.REPAIR),
            ReasonCode.GATE_FIXABLE,
            inputs,
        )
    # Any other pre-existing state (e.g. OPEN after a malformed-fold retry):
    # request the repair again without resetting the lifecycle counters.
    findings = _replace_finding(snapshot.findings, replace(record, state=FindingState.OPEN))
    return _issue_agent(
        replace(snapshot, findings=findings, artifact_retries=0),
        policy,
        LaneState.REPAIRING,
        Awaiting.FOLD,
        InvokeAuthor(action=AgentAction.REPAIR),
        ReasonCode.GATE_FIXABLE,
        inputs,
    )


def _on_review(snapshot: LaneSnapshot, event: ReviewAccepted, policy: LanePolicy) -> TransitionDecision:
    if snapshot.awaiting is not Awaiting.REVIEW:
        return _fail_closed(snapshot, event)
    inputs: dict[str, Any] = {
        "lens": event.lens.value,
        "verdict": event.verdict,
        "has_scope_observations": event.has_scope_observations,
        "new_findings": [f.finding_id for f in event.new_findings],
        "prior_findings": {p.finding_id: p.outcome.value for p in event.prior_findings},
    }
    if event.lens is not Lens.GATING:
        return _stopped(snapshot, ReasonCode.NON_GATING_REVIEW, inputs)
    if event.has_scope_observations:
        return _stopped(snapshot, ReasonCode.SCOPE_OBSERVATION, inputs)

    findings = snapshot.findings
    for assessment in event.prior_findings:
        record = snapshot.finding(assessment.finding_id)
        if record is None:
            return _stopped(
                snapshot,
                ReasonCode.MALFORMED_ARTIFACT_STOP,
                dict(inputs, unknown_finding_id=assessment.finding_id),
            )
        outcome = assessment.outcome
        if outcome is PriorOutcome.VERIFIED_RESOLVED:
            record = replace(record, state=FindingState.VERIFIED_RESOLVED)
        elif outcome is PriorOutcome.STILL_PRESENT:
            record = replace(record, state=FindingState.STILL_PRESENT)
        elif outcome is PriorOutcome.REOPENED:
            record = replace(
                record, state=FindingState.REOPENED, reopen_count=record.reopen_count + 1
            )
        elif outcome is PriorOutcome.REVIEWER_ACCEPTS_REJECTION:
            record = replace(record, state=FindingState.CLOSED_NOT_A_DEFECT)
        elif outcome is PriorOutcome.REVIEWER_DISAGREES:
            record = replace(record, state=FindingState.TECHNICAL_CLARIFICATION)
        findings = _replace_finding(findings, record)
    for new in event.new_findings:
        if any(f.finding_id == new.finding_id for f in findings):
            return _stopped(
                snapshot,
                ReasonCode.MALFORMED_ARTIFACT_STOP,
                dict(inputs, duplicate_finding_id=new.finding_id),
            )
        findings = _sorted_findings(
            findings
            + (
                FindingRecord(
                    finding_id=new.finding_id,
                    severity=new.severity,
                    title=new.title,
                    state=FindingState.OPEN,
                ),
            )
        )
    working = replace(snapshot, findings=findings, artifact_retries=0)

    if any(f.earlier_phase_gap for f in event.new_findings):
        return _stopped(working, ReasonCode.EARLIER_PHASE_GAP, inputs)
    if any(f.unknown_contract for f in event.new_findings):
        return _wait_operator(working, ReasonCode.UNKNOWN_CONTRACT, inputs)
    if any(f.requires_ruling for f in event.new_findings):
        return _wait_operator(working, ReasonCode.REQUIRES_RULING, inputs)
    ping_pong = [f.finding_id for f in working.findings if f.reopen_count >= 2]
    if ping_pong:
        return _wait_operator(working, ReasonCode.PING_PONG, dict(inputs, finding_ids=ping_pong))
    still_present = [
        f for f in working.findings if f.blocking and f.state is FindingState.STILL_PRESENT
    ]
    if any(f.guidance_given for f in still_present):
        handoff_ids = [f.finding_id for f in still_present if f.guidance_given]
        return _stopped(working, ReasonCode.HANDOFF_REQUIRED, dict(inputs, finding_ids=handoff_ids))
    if still_present:
        marked = working.findings
        for f in still_present:
            marked = _replace_finding(marked, replace(f, state=FindingState.GUIDANCE_REQUIRED))
        return _issue_agent(
            replace(working, findings=marked),
            policy,
            LaneState.REVIEWING,
            Awaiting.GUIDANCE,
            InvokeGuidance(),
            ReasonCode.GUIDANCE_REQUIRED,
            dict(inputs, finding_ids=[f.finding_id for f in still_present]),
        )
    open_blockers = working.open_blocking()
    if open_blockers:
        return _issue_agent(
            working,
            policy,
            LaneState.REPAIRING,
            Awaiting.FOLD,
            InvokeAuthor(action=AgentAction.REPAIR),
            ReasonCode.REVIEW_FINDINGS,
            dict(inputs, finding_ids=[f.finding_id for f in open_blockers]),
        )
    if event.verdict != "CLEAN":
        return _stopped(working, ReasonCode.VERDICT_INCONSISTENT, inputs)
    # Convergence: gating CLEAN on a gate-accepted revision with every
    # historical blocker VERIFIED_RESOLVED or CLOSED_NOT_A_DEFECT.
    next_snapshot = replace(working, state=LaneState.LANDING, awaiting=None)
    return TransitionDecision(next_snapshot, LandRevision(), ReasonCode.CONVERGED, inputs)


def _on_guidance(
    snapshot: LaneSnapshot, event: GuidanceAccepted, policy: LanePolicy
) -> TransitionDecision:
    if snapshot.awaiting is not Awaiting.GUIDANCE:
        return _fail_closed(snapshot, event)
    findings = snapshot.findings
    for finding_id in event.finding_ids:
        record = snapshot.finding(finding_id)
        if record is None:
            return _stopped(
                snapshot,
                ReasonCode.MALFORMED_ARTIFACT_STOP,
                {"unknown_finding_id": finding_id},
            )
        findings = _replace_finding(findings, replace(record, guidance_given=True))
    return _issue_agent(
        replace(snapshot, findings=findings),
        policy,
        LaneState.REPAIRING,
        Awaiting.FOLD,
        InvokeAuthor(action=AgentAction.REPAIR),
        ReasonCode.GUIDANCE_ACCEPTED,
        {"finding_ids": list(event.finding_ids)},
    )


def _on_artifact_rejected(
    snapshot: LaneSnapshot, event: ArtifactRejected, policy: LanePolicy
) -> TransitionDecision:
    if event.artifact is not snapshot.awaiting:
        return _fail_closed(snapshot, event)
    inputs = {"artifact": event.artifact.value, "attempt": snapshot.artifact_retries + 1}
    if snapshot.artifact_retries >= 1:
        return _stopped(snapshot, ReasonCode.MALFORMED_ARTIFACT_STOP, inputs)
    retried = replace(snapshot, artifact_retries=snapshot.artifact_retries + 1)
    if event.artifact is Awaiting.GATE_RESULT:
        # Re-running the deterministic gate spawns no agent process.
        return TransitionDecision(
            replace(retried, state=LaneState.VERIFYING),
            VerifyRevision(),
            ReasonCode.MALFORMED_ARTIFACT_RETRY,
            inputs,
        )
    reissue: dict[Awaiting, tuple[LaneState, Command]] = {
        Awaiting.AUTHOR_RESULT: (LaneState.AUTHORING, InvokeAuthor(action=AgentAction.AUTHOR)),
        Awaiting.FOLD: (LaneState.REPAIRING, InvokeAuthor(action=AgentAction.REPAIR)),
        Awaiting.REVIEW: (LaneState.REVIEWING, InvokeReviewer()),
        Awaiting.GUIDANCE: (LaneState.REVIEWING, InvokeGuidance()),
    }
    state, command = reissue[event.artifact]
    return _issue_agent(
        retried,
        policy,
        state,
        event.artifact,
        command,
        ReasonCode.MALFORMED_ARTIFACT_RETRY,
        inputs,
        reset_retries=False,
    )


_HANDLERS = {
    (LaneState.AUTHORIZED, LaneAuthorized): _on_lane_authorized,
    (LaneState.AUTHORING, AuthorResultAccepted): _on_author_result,
    (LaneState.AUTHORING, ArtifactRejected): _on_artifact_rejected,
    (LaneState.REPAIRING, FoldAccepted): _on_fold,
    (LaneState.REPAIRING, ArtifactRejected): _on_artifact_rejected,
    (LaneState.VERIFYING, RevisionVerified): _on_revision_verified,
    (LaneState.VERIFYING, ArtifactRejected): _on_artifact_rejected,
    (LaneState.REVIEWING, ReviewAccepted): _on_review,
    (LaneState.REVIEWING, GuidanceAccepted): _on_guidance,
    (LaneState.REVIEWING, ArtifactRejected): _on_artifact_rejected,
}
