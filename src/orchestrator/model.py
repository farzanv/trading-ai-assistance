"""Frozen domain types: states, findings, events, commands, policy, decisions.

Design authority: docs/design/DETERMINISTIC_PYTHON_APPLICATION_ARCHITECTURE.md
(§4 domain model, §5 transition flow) and
docs/design/REVIEW_REPAIR_CONVERGENCE_PROTOCOL.md (§3 finding lifecycle).

Nothing in this module performs I/O, reads a clock, or interprets prose.
Events carry only validated, typed facts; the reducer consumes nothing else.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class LaneState(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    AUTHORING = "AUTHORING"
    VERIFYING = "VERIFYING"
    REVIEWING = "REVIEWING"
    REPAIRING = "REPAIRING"
    PAUSED_LIMIT = "PAUSED_LIMIT"
    WAIT_OPERATOR = "WAIT_OPERATOR"
    LANDING = "LANDING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


TERMINAL_STATES = frozenset({LaneState.COMPLETED, LaneState.STOPPED})


class AgentAction(str, Enum):
    AUTHOR = "AUTHOR"
    REPAIR = "REPAIR"
    REVIEW = "REVIEW"
    GUIDANCE = "GUIDANCE"


class Lens(str, Enum):
    GATING = "gating"
    GUIDANCE = "guidance"
    ADVISORY = "advisory"


class Severity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


BLOCKING_SEVERITIES = frozenset({Severity.P0, Severity.P1, Severity.P2})


class GateCategory(str, Enum):
    PASS = "PASS"
    DOCS_INCONCLUSIVE_SCOPE_PASS = "DOCS_INCONCLUSIVE_SCOPE_PASS"
    FIXABLE_TEST = "FIXABLE_TEST"
    FIXABLE_LINT = "FIXABLE_LINT"
    FIXABLE_STATIC_CHECK = "FIXABLE_STATIC_CHECK"
    SCOPE = "SCOPE"
    BASE = "BASE"
    DEPENDENCY = "DEPENDENCY"
    FOREIGN_COMMIT = "FOREIGN_COMMIT"
    UNKNOWN = "UNKNOWN"


FIXABLE_GATE_CATEGORIES = frozenset(
    {GateCategory.FIXABLE_TEST, GateCategory.FIXABLE_LINT, GateCategory.FIXABLE_STATIC_CHECK}
)


class FindingState(str, Enum):
    OPEN = "OPEN"
    FIX_CLAIMED = "FIX_CLAIMED"
    STILL_PRESENT = "STILL_PRESENT"
    GUIDANCE_REQUIRED = "GUIDANCE_REQUIRED"
    VERIFIED_RESOLVED = "VERIFIED_RESOLVED"
    REOPENED = "REOPENED"
    REJECTED_PENDING_REVIEW = "REJECTED_PENDING_REVIEW"
    TECHNICAL_CLARIFICATION = "TECHNICAL_CLARIFICATION"
    CLOSED_NOT_A_DEFECT = "CLOSED_NOT_A_DEFECT"


#: Finding states that block CLEAN and landing.
OPEN_BLOCKING_STATES = frozenset(
    {
        FindingState.OPEN,
        FindingState.FIX_CLAIMED,
        FindingState.STILL_PRESENT,
        FindingState.GUIDANCE_REQUIRED,
        FindingState.REOPENED,
        FindingState.REJECTED_PENDING_REVIEW,
        FindingState.TECHNICAL_CLARIFICATION,
    }
)

#: Finding states the author must disposition in the next fold (exact set).
FOLD_OUTSTANDING_STATES = frozenset(
    {
        FindingState.OPEN,
        FindingState.STILL_PRESENT,
        FindingState.GUIDANCE_REQUIRED,
        FindingState.REOPENED,
        FindingState.TECHNICAL_CLARIFICATION,
    }
)


class Disposition(str, Enum):
    FOLDED = "FOLDED"
    REJECTED_WITH_REASON = "REJECTED_WITH_REASON"
    BLOCKED_NEEDS_TECHNICAL_GUIDANCE = "BLOCKED_NEEDS_TECHNICAL_GUIDANCE"
    UNKNOWN_CONTRACT = "UNKNOWN_CONTRACT"
    REQUIRES_SCOPE_EXPANSION = "REQUIRES_SCOPE_EXPANSION"
    REQUIRES_OPERATOR_ACTION = "REQUIRES_OPERATOR_ACTION"


class PriorOutcome(str, Enum):
    STILL_PRESENT = "STILL_PRESENT"
    VERIFIED_RESOLVED = "VERIFIED_RESOLVED"
    REOPENED = "REOPENED"
    REVIEWER_ACCEPTS_REJECTION = "REVIEWER_ACCEPTS_REJECTION"
    REVIEWER_DISAGREES = "REVIEWER_DISAGREES"


#: Legal review outcomes given a finding's pre-review state (architecture §7.3:
#: an illegal lifecycle transition makes the review artifact malformed).
LEGAL_PRIOR_OUTCOMES: Mapping[FindingState, frozenset[PriorOutcome]] = {
    FindingState.FIX_CLAIMED: frozenset({PriorOutcome.VERIFIED_RESOLVED, PriorOutcome.STILL_PRESENT}),
    FindingState.REJECTED_PENDING_REVIEW: frozenset(
        {PriorOutcome.REVIEWER_ACCEPTS_REJECTION, PriorOutcome.REVIEWER_DISAGREES}
    ),
    FindingState.VERIFIED_RESOLVED: frozenset({PriorOutcome.VERIFIED_RESOLVED, PriorOutcome.REOPENED}),
    FindingState.CLOSED_NOT_A_DEFECT: frozenset({PriorOutcome.REVIEWER_ACCEPTS_REJECTION}),
}


class FindingOrigin(str, Enum):
    REVIEWER = "REVIEWER"
    GATE = "GATE"  # deterministic system finding from a fixable target-gate failure


class ReasonCode(str, Enum):
    LANE_AUTHORIZED = "LANE_AUTHORIZED"
    AUTHOR_RESULT_ACCEPTED = "AUTHOR_RESULT_ACCEPTED"
    FOLD_ACCEPTED = "FOLD_ACCEPTED"
    REVISION_VERIFIED = "REVISION_VERIFIED"
    GATE_FIXABLE = "GATE_FIXABLE"
    GATE_BLOCKING = "GATE_BLOCKING"
    GATE_UNKNOWN = "GATE_UNKNOWN"
    GIT_FACTS_FAILED = "GIT_FACTS_FAILED"
    REVIEW_FINDINGS = "REVIEW_FINDINGS"
    CONVERGED = "CONVERGED"
    GUIDANCE_REQUIRED = "GUIDANCE_REQUIRED"
    GUIDANCE_ACCEPTED = "GUIDANCE_ACCEPTED"
    HANDOFF_REQUIRED = "HANDOFF_REQUIRED"
    PING_PONG = "PING_PONG"
    ADJUDICATION_REQUIRED = "ADJUDICATION_REQUIRED"
    REQUIRES_RULING = "REQUIRES_RULING"
    EARLIER_PHASE_GAP = "EARLIER_PHASE_GAP"
    UNKNOWN_CONTRACT = "UNKNOWN_CONTRACT"
    REQUIRES_SCOPE_EXPANSION = "REQUIRES_SCOPE_EXPANSION"
    REQUIRES_OPERATOR_ACTION = "REQUIRES_OPERATOR_ACTION"
    SCOPE_OBSERVATION = "SCOPE_OBSERVATION"
    NON_GATING_REVIEW = "NON_GATING_REVIEW"
    CLEAN_WITH_OPEN_BLOCKERS = "CLEAN_WITH_OPEN_BLOCKERS"
    VERDICT_INCONSISTENT = "VERDICT_INCONSISTENT"
    MALFORMED_ARTIFACT_RETRY = "MALFORMED_ARTIFACT_RETRY"
    MALFORMED_ARTIFACT_STOP = "MALFORMED_ARTIFACT_STOP"
    MAX_ROUNDS_EXCEEDED = "MAX_ROUNDS_EXCEEDED"
    MAX_INVOCATIONS_EXCEEDED = "MAX_INVOCATIONS_EXCEEDED"
    UNSPECIFIED_TRANSITION = "UNSPECIFIED_TRANSITION"


class Awaiting(str, Enum):
    """The artifact the lane is waiting for; keys malformed-artifact retries."""

    AUTHOR_RESULT = "AUTHOR_RESULT"
    FOLD = "FOLD"
    GATE_RESULT = "GATE_RESULT"
    REVIEW = "REVIEW"
    GUIDANCE = "GUIDANCE"


# ---------------------------------------------------------------------------
# Lane identity, policy, and snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaneIdentity:
    """Immutable lane binding fixed at authorization (architecture §4.1).

    Every artifact is validated against these values; a mismatch is a
    malformed artifact, never a silently adopted new identity.
    """

    lane_id: str
    work_item: str
    scope_base: str  # full 40-hex SHA
    manifest: str  # target-repo-relative manifest path


@dataclass(frozen=True)
class LanePolicy:
    """Immutable bounds, gate acceptance, and role binding for one lane
    (architecture §4.1). Resolved from the registered project's lane policy for
    the declared work-item kind — never supplied free-form by a caller."""

    lane_kind: str
    accepted_gate_categories: frozenset[GateCategory]
    max_rounds: int = 10
    max_agent_invocations: int = 40
    author_agent: str = "claude"
    reviewer_agent: str = "codex"


#: The review_kind a gating review must declare for each lane kind.
REVIEW_KIND_FOR_LANE_KIND: Mapping[str, str] = {
    "design": "design",
    "implementation": "code",
    "bookkeeping": "bookkeeping",
}


def policy_digest(policy: LanePolicy) -> str:
    """Stable digest binding a ledger to the exact policy it ran under."""
    canonical = json.dumps(
        {
            "lane_kind": policy.lane_kind,
            "accepted_gate_categories": sorted(c.value for c in policy.accepted_gate_categories),
            "max_rounds": policy.max_rounds,
            "max_agent_invocations": policy.max_agent_invocations,
            "author_agent": policy.author_agent,
            "reviewer_agent": policy.reviewer_agent,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FindingRecord:
    finding_id: str
    severity: Severity
    title: str
    state: FindingState
    origin: FindingOrigin = FindingOrigin.REVIEWER
    repair_attempts: int = 0
    guidance_given: bool = False
    consecutive_rejections: int = 0
    reopen_count: int = 0

    @property
    def blocking(self) -> bool:
        return self.severity in BLOCKING_SEVERITIES


@dataclass(frozen=True)
class LaneSnapshot:
    """The reducer's complete typed state. Findings are ordered by id.

    ``current_sha``/``current_tree`` retain the validated candidate revision so
    every later artifact is bound to it (never to whatever an artifact claims).
    """

    state: LaneState = LaneState.AUTHORIZED
    awaiting: Awaiting | None = None
    revision: int = 0
    review_round: int = 0
    agent_invocations: int = 0
    artifact_retries: int = 0
    current_sha: str = ""
    current_tree: str = ""
    findings: tuple[FindingRecord, ...] = ()

    def finding(self, finding_id: str) -> FindingRecord | None:
        for record in self.findings:
            if record.finding_id == finding_id:
                return record
        return None

    def blocking_findings(self) -> tuple[FindingRecord, ...]:
        return tuple(f for f in self.findings if f.blocking)

    def open_blocking(self) -> tuple[FindingRecord, ...]:
        return tuple(f for f in self.findings if f.blocking and f.state in OPEN_BLOCKING_STATES)

    def fold_outstanding_ids(self) -> frozenset[str]:
        return frozenset(
            f.finding_id for f in self.findings if f.blocking and f.state in FOLD_OUTSTANDING_STATES
        )

    def historical_blocking_states(self) -> Mapping[str, FindingState]:
        """EVERY historical blocker the reviewer must reconcile (exact set).

        Gate-origin (SYS-*) findings are included (protocol §2): the passing
        gate is their closure evidence, but the reviewer still reconciles the
        root cause and may REOPEN a gamed or superficial repair.
        """
        return {f.finding_id: f.state for f in self.findings if f.blocking}

    def guidance_expected_ids(self) -> frozenset[str]:
        """The exact finding set a guidance artifact must address."""
        return frozenset(
            f.finding_id
            for f in self.findings
            if f.blocking and f.state is FindingState.GUIDANCE_REQUIRED and not f.guidance_given
        )


# ---------------------------------------------------------------------------
# Commands (effects the shell must perform) — never executed by the reducer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvokeAuthor:
    action: AgentAction  # AUTHOR or REPAIR
    kind: str = field(default="InvokeAuthor", init=False)


@dataclass(frozen=True)
class VerifyRevision:
    kind: str = field(default="VerifyRevision", init=False)


@dataclass(frozen=True)
class InvokeReviewer:
    kind: str = field(default="InvokeReviewer", init=False)


@dataclass(frozen=True)
class InvokeGuidance:
    kind: str = field(default="InvokeGuidance", init=False)


@dataclass(frozen=True)
class LandRevision:
    kind: str = field(default="LandRevision", init=False)


@dataclass(frozen=True)
class OpenHumanGate:
    reason: ReasonCode
    kind: str = field(default="OpenHumanGate", init=False)


Command = InvokeAuthor | VerifyRevision | InvokeReviewer | InvokeGuidance | LandRevision | OpenHumanGate

#: Commands that spawn an agent process; issuing one consumes an invocation.
AGENT_COMMAND_KINDS = frozenset({"InvokeAuthor", "InvokeReviewer", "InvokeGuidance"})


# ---------------------------------------------------------------------------
# Events (validated observed facts)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaneAuthorized:
    kind: str = field(default="LaneAuthorized", init=False)


@dataclass(frozen=True)
class AuthorResultAccepted:
    revision: int
    commit: str
    tree_digest: str
    has_unknown_contracts: bool
    kind: str = field(default="AuthorResultAccepted", init=False)


@dataclass(frozen=True)
class FoldAccepted:
    revision: int
    commit: str
    tree_digest: str
    dispositions: tuple[tuple[str, Disposition], ...]
    has_unknown_contracts: bool
    kind: str = field(default="FoldAccepted", init=False)


@dataclass(frozen=True)
class RevisionVerified:
    gate_category: GateCategory
    descends_from_base: bool
    contiguous: bool
    no_foreign_commits: bool
    files_in_scope: bool
    worktree_clean: bool
    failed_checks: tuple[str, ...]
    kind: str = field(default="RevisionVerified", init=False)

    @property
    def git_facts_ok(self) -> bool:
        return (
            self.descends_from_base
            and self.contiguous
            and self.no_foreign_commits
            and self.files_in_scope
            and self.worktree_clean
        )


@dataclass(frozen=True)
class NewFinding:
    finding_id: str
    severity: Severity
    title: str
    requires_ruling: bool = False
    earlier_phase_gap: bool = False
    unknown_contract: bool = False


@dataclass(frozen=True)
class PriorAssessment:
    finding_id: str
    outcome: PriorOutcome


@dataclass(frozen=True)
class ReviewAccepted:
    lens: Lens
    verdict: str  # "CLEAN" | "FINDINGS"
    has_scope_observations: bool
    new_findings: tuple[NewFinding, ...]
    prior_findings: tuple[PriorAssessment, ...]
    kind: str = field(default="ReviewAccepted", init=False)


@dataclass(frozen=True)
class GuidanceAccepted:
    finding_ids: tuple[str, ...]
    kind: str = field(default="GuidanceAccepted", init=False)


@dataclass(frozen=True)
class ArtifactRejected:
    artifact: Awaiting
    kind: str = field(default="ArtifactRejected", init=False)


Event = (
    LaneAuthorized
    | AuthorResultAccepted
    | FoldAccepted
    | RevisionVerified
    | ReviewAccepted
    | GuidanceAccepted
    | ArtifactRejected
)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionDecision:
    snapshot: LaneSnapshot
    command: Command | None
    reason: ReasonCode
    inputs: Mapping[str, Any]


# ---------------------------------------------------------------------------
# Event (de)serialization for the ledger — explicit, no reflection magic
# ---------------------------------------------------------------------------


def event_to_dict(event: Event) -> dict[str, Any]:
    if isinstance(event, LaneAuthorized):
        return {"kind": event.kind}
    if isinstance(event, AuthorResultAccepted):
        return {
            "kind": event.kind,
            "revision": event.revision,
            "commit": event.commit,
            "tree_digest": event.tree_digest,
            "has_unknown_contracts": event.has_unknown_contracts,
        }
    if isinstance(event, FoldAccepted):
        return {
            "kind": event.kind,
            "revision": event.revision,
            "commit": event.commit,
            "tree_digest": event.tree_digest,
            "dispositions": [[fid, disp.value] for fid, disp in event.dispositions],
            "has_unknown_contracts": event.has_unknown_contracts,
        }
    if isinstance(event, RevisionVerified):
        return {
            "kind": event.kind,
            "gate_category": event.gate_category.value,
            "descends_from_base": event.descends_from_base,
            "contiguous": event.contiguous,
            "no_foreign_commits": event.no_foreign_commits,
            "files_in_scope": event.files_in_scope,
            "worktree_clean": event.worktree_clean,
            "failed_checks": list(event.failed_checks),
        }
    if isinstance(event, ReviewAccepted):
        return {
            "kind": event.kind,
            "lens": event.lens.value,
            "verdict": event.verdict,
            "has_scope_observations": event.has_scope_observations,
            "new_findings": [
                {
                    "finding_id": f.finding_id,
                    "severity": f.severity.value,
                    "title": f.title,
                    "requires_ruling": f.requires_ruling,
                    "earlier_phase_gap": f.earlier_phase_gap,
                    "unknown_contract": f.unknown_contract,
                }
                for f in event.new_findings
            ],
            "prior_findings": [[p.finding_id, p.outcome.value] for p in event.prior_findings],
        }
    if isinstance(event, GuidanceAccepted):
        return {"kind": event.kind, "finding_ids": list(event.finding_ids)}
    if isinstance(event, ArtifactRejected):
        return {"kind": event.kind, "artifact": event.artifact.value}
    raise TypeError(f"unserializable event: {event!r}")


def event_from_dict(data: Mapping[str, Any]) -> Event:
    kind = data["kind"]
    if kind == "LaneAuthorized":
        return LaneAuthorized()
    if kind == "AuthorResultAccepted":
        return AuthorResultAccepted(
            revision=int(data["revision"]),
            commit=data["commit"],
            tree_digest=data["tree_digest"],
            has_unknown_contracts=bool(data["has_unknown_contracts"]),
        )
    if kind == "FoldAccepted":
        return FoldAccepted(
            revision=int(data["revision"]),
            commit=data["commit"],
            tree_digest=data["tree_digest"],
            dispositions=tuple((fid, Disposition(disp)) for fid, disp in data["dispositions"]),
            has_unknown_contracts=bool(data["has_unknown_contracts"]),
        )
    if kind == "RevisionVerified":
        return RevisionVerified(
            gate_category=GateCategory(data["gate_category"]),
            descends_from_base=bool(data["descends_from_base"]),
            contiguous=bool(data["contiguous"]),
            no_foreign_commits=bool(data["no_foreign_commits"]),
            files_in_scope=bool(data["files_in_scope"]),
            worktree_clean=bool(data["worktree_clean"]),
            failed_checks=tuple(data["failed_checks"]),
        )
    if kind == "ReviewAccepted":
        return ReviewAccepted(
            lens=Lens(data["lens"]),
            verdict=data["verdict"],
            has_scope_observations=bool(data["has_scope_observations"]),
            new_findings=tuple(
                NewFinding(
                    finding_id=f["finding_id"],
                    severity=Severity(f["severity"]),
                    title=f["title"],
                    requires_ruling=bool(f["requires_ruling"]),
                    earlier_phase_gap=bool(f["earlier_phase_gap"]),
                    unknown_contract=bool(f["unknown_contract"]),
                )
                for f in data["new_findings"]
            ),
            prior_findings=tuple(
                PriorAssessment(finding_id=fid, outcome=PriorOutcome(outcome))
                for fid, outcome in data["prior_findings"]
            ),
        )
    if kind == "GuidanceAccepted":
        return GuidanceAccepted(finding_ids=tuple(data["finding_ids"]))
    if kind == "ArtifactRejected":
        return ArtifactRejected(artifact=Awaiting(data["artifact"]))
    raise ValueError(f"unknown event kind: {kind!r}")


# ---------------------------------------------------------------------------
# Snapshot (de)serialization — the ledger records the COMPLETE snapshot so
# replay can compare every field, not just the state name
# ---------------------------------------------------------------------------


def snapshot_to_dict(snapshot: LaneSnapshot) -> dict[str, Any]:
    return {
        "state": snapshot.state.value,
        "awaiting": snapshot.awaiting.value if snapshot.awaiting is not None else None,
        "revision": snapshot.revision,
        "review_round": snapshot.review_round,
        "agent_invocations": snapshot.agent_invocations,
        "artifact_retries": snapshot.artifact_retries,
        "current_sha": snapshot.current_sha,
        "current_tree": snapshot.current_tree,
        "findings": [
            {
                "finding_id": f.finding_id,
                "severity": f.severity.value,
                "title": f.title,
                "state": f.state.value,
                "origin": f.origin.value,
                "repair_attempts": f.repair_attempts,
                "guidance_given": f.guidance_given,
                "consecutive_rejections": f.consecutive_rejections,
                "reopen_count": f.reopen_count,
            }
            for f in snapshot.findings
        ],
    }


def snapshot_from_dict(data: Mapping[str, Any]) -> LaneSnapshot:
    return LaneSnapshot(
        state=LaneState(data["state"]),
        awaiting=Awaiting(data["awaiting"]) if data["awaiting"] is not None else None,
        revision=int(data["revision"]),
        review_round=int(data["review_round"]),
        agent_invocations=int(data["agent_invocations"]),
        artifact_retries=int(data["artifact_retries"]),
        current_sha=data["current_sha"],
        current_tree=data["current_tree"],
        findings=tuple(
            FindingRecord(
                finding_id=f["finding_id"],
                severity=Severity(f["severity"]),
                title=f["title"],
                state=FindingState(f["state"]),
                origin=FindingOrigin(f["origin"]),
                repair_attempts=int(f["repair_attempts"]),
                guidance_given=bool(f["guidance_given"]),
                consecutive_rejections=int(f["consecutive_rejections"]),
                reopen_count=int(f["reopen_count"]),
            )
            for f in data["findings"]
        ),
    )
