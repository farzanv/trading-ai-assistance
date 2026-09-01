"""The lane coordinator: imperative shell around the pure reducer.

The coordinator executes reducer-selected commands through the driver
interfaces, validates every returned artifact, appends ledger evidence, and
calls the reducer again. It holds no transition judgement of its own: every
routing decision is the reducer's, and every accepted event was schema- and
semantics-validated first (functional core / imperative shell, architecture §3).

V0-A boundary: landing is not implemented. When the reducer decides
``LANDING``/``LandRevision`` the run ends at convergence — nothing is landed,
pushed, or claimed. Real drivers, verify/land process split, and recovery are
V0-B scope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from orchestrator.agents import AuthorDriver, GateDriver, InvocationSpec, ReviewerDriver
from orchestrator.artifacts import (
    ArtifactError,
    SchemaSet,
    artifact_digest,
    validate_author_result,
    validate_fold,
    validate_gate_result,
    validate_guidance,
    validate_review,
)
from orchestrator.ledger import KIND_DECISION, KIND_EFFECT, Ledger
from orchestrator.model import (
    AgentAction,
    ArtifactRejected,
    AuthorResultAccepted,
    Awaiting,
    Command,
    Event,
    FindingState,
    FoldAccepted,
    GuidanceAccepted,
    InvokeAuthor,
    InvokeGuidance,
    InvokeReviewer,
    LandRevision,
    LaneAuthorized,
    LanePolicy,
    LaneSnapshot,
    Lens,
    OpenHumanGate,
    ReviewAccepted,
    RevisionVerified,
    Severity,
    VerifyRevision,
    event_to_dict,
)
from orchestrator.reducer import reduce


class CoordinatorError(Exception):
    """The coordinator itself failed (harness misuse, step guard)."""


@dataclass(frozen=True)
class RunResult:
    snapshot: LaneSnapshot
    steps: int
    ledger_path: Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LaneCoordinator:
    def __init__(
        self,
        lane_id: str,
        policy: LanePolicy,
        schemas: SchemaSet,
        author: AuthorDriver,
        reviewer: ReviewerDriver,
        gate: GateDriver,
        run_dir: Path,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._lane_id = lane_id
        self._policy = policy
        self._schemas = schemas
        self._author = author
        self._reviewer = reviewer
        self._gate = gate
        self._run_dir = run_dir
        self._artifacts_dir = run_dir / "artifacts"
        self._clock = clock
        self._ledger = Ledger(run_dir / "ledger.jsonl")

    @property
    def ledger_path(self) -> Path:
        return self._ledger.path

    def run(self, max_steps: int = 200) -> RunResult:
        """Drive the lane from AUTHORIZED until convergence, gate, or STOP."""
        snapshot = LaneSnapshot()
        event: Event = LaneAuthorized()
        steps = 0
        while True:
            steps += 1
            if steps > max_steps:
                raise CoordinatorError(f"step guard exceeded ({max_steps}); lane did not settle")
            decision = reduce(snapshot, event, self._policy)
            self._ledger.append(
                KIND_DECISION,
                self._lane_id,
                self._clock(),
                {
                    "event": event_to_dict(event),
                    "state_before": snapshot.state.value,
                    "state_after": decision.snapshot.state.value,
                    "reason": decision.reason.value,
                    "command": decision.command.kind if decision.command is not None else None,
                    "inputs": decision.inputs,
                },
            )
            snapshot = decision.snapshot
            command = decision.command
            if command is None or isinstance(command, (OpenHumanGate, LandRevision)):
                self._end_run(snapshot, command, decision.reason.value)
                return RunResult(snapshot=snapshot, steps=steps, ledger_path=self._ledger.path)
            event = self._execute(command, snapshot)

    # -- effects ------------------------------------------------------------

    def _end_run(self, snapshot: LaneSnapshot, command: Command | None, reason: str) -> None:
        if isinstance(command, LandRevision):
            end = "CONVERGED_V0A_NO_LANDING"  # V0-A boundary: landing is not implemented
        elif isinstance(command, OpenHumanGate):
            end = "HUMAN_GATE"
        else:
            end = "STOPPED"
        self._write_p3_backlog(snapshot)
        self._ledger.append(
            KIND_EFFECT,
            self._lane_id,
            self._clock(),
            {"effect": "RUN_END", "end": end, "reason": reason, "state": snapshot.state.value},
        )

    def _write_p3_backlog(self, snapshot: LaneSnapshot) -> None:
        backlog = [
            {"id": f.finding_id, "severity": f.severity.value, "title": f.title}
            for f in snapshot.findings
            if f.severity is Severity.P3
        ]
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = self._artifacts_dir / "p3-backlog.json"
        path.write_text(
            json.dumps({"findings": backlog}, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    def _spec(self, snapshot: LaneSnapshot, role: str, action: AgentAction | None) -> InvocationSpec:
        return InvocationSpec(
            invocation_id=f"{self._lane_id}-I{snapshot.agent_invocations:03d}",
            lane_id=self._lane_id,
            role=role,
            action=action,
            review_round=snapshot.review_round,
            revision=snapshot.revision,
            attempt=snapshot.artifact_retries,
        )

    def _plan_invocation(self, spec: InvocationSpec, counted: bool, count: int) -> None:
        self._ledger.append(
            KIND_EFFECT,
            self._lane_id,
            self._clock(),
            {
                "effect": "INVOCATION_PLANNED",
                "invocation_id": spec.invocation_id,
                "role": spec.role,
                "action": spec.action.value if spec.action is not None else None,
                "attempt": spec.attempt,
                "counted": counted,
                "invocation_number": count if counted else None,
            },
        )

    def _accept_artifact(self, kind: str, spec: InvocationSpec, raw: object) -> None:
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = self._artifacts_dir / f"{spec.invocation_id}-{kind}.json"
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        path.write_text(canonical + "\n", encoding="utf-8")
        self._ledger.append(
            KIND_EFFECT,
            self._lane_id,
            self._clock(),
            {
                "effect": "ARTIFACT_ACCEPTED",
                "artifact": kind,
                "invocation_id": spec.invocation_id,
                "digest": artifact_digest(raw),  # type: ignore[arg-type]
                "path": path.name,
            },
        )

    def _reject_artifact(self, kind: str, spec: InvocationSpec, error: ArtifactError) -> None:
        self._ledger.append(
            KIND_EFFECT,
            self._lane_id,
            self._clock(),
            {
                "effect": "ARTIFACT_REJECTED",
                "artifact": kind,
                "invocation_id": spec.invocation_id,
                "errors": [e[:500] for e in error.errors][:20],
            },
        )

    def _execute(self, command: Command, snapshot: LaneSnapshot) -> Event:
        if isinstance(command, InvokeAuthor):
            if command.action is AgentAction.AUTHOR:
                return self._do_author(snapshot)
            return self._do_repair(snapshot)
        if isinstance(command, VerifyRevision):
            return self._do_verify(snapshot)
        if isinstance(command, InvokeReviewer):
            return self._do_review(snapshot)
        if isinstance(command, InvokeGuidance):
            return self._do_guidance(snapshot)
        raise CoordinatorError(f"unexecutable command: {command!r}")

    def _do_author(self, snapshot: LaneSnapshot) -> Event:
        spec = self._spec(snapshot, "author", AgentAction.AUTHOR)
        self._plan_invocation(spec, counted=True, count=snapshot.agent_invocations)
        raw = self._author.author(spec)
        try:
            summary = validate_author_result(self._schemas, raw, expected_revision=1)
        except ArtifactError as exc:
            self._reject_artifact("author-result", spec, exc)
            return ArtifactRejected(artifact=Awaiting.AUTHOR_RESULT)
        self._accept_artifact("author-result", spec, raw)
        return AuthorResultAccepted(revision=summary.revision)

    def _do_repair(self, snapshot: LaneSnapshot) -> Event:
        spec = self._spec(snapshot, "author", AgentAction.REPAIR)
        self._plan_invocation(spec, counted=True, count=snapshot.agent_invocations)
        raw = self._author.repair(spec)
        try:
            summary = validate_fold(
                self._schemas,
                raw,
                outstanding_ids=snapshot.fold_outstanding_ids(),
                expected_revision=snapshot.revision + 1,
            )
        except ArtifactError as exc:
            self._reject_artifact("fold", spec, exc)
            return ArtifactRejected(artifact=Awaiting.FOLD)
        self._accept_artifact("fold", spec, raw)
        return FoldAccepted(revision=summary.revision, dispositions=summary.dispositions)

    def _do_verify(self, snapshot: LaneSnapshot) -> Event:
        spec = self._spec(snapshot, "gate", None)
        self._plan_invocation(spec, counted=False, count=snapshot.agent_invocations)
        git_facts, raw = self._gate.verify(spec)
        try:
            category = validate_gate_result(self._schemas, raw)
        except ArtifactError as exc:
            self._reject_artifact("target-gate-result", spec, exc)
            return ArtifactRejected(artifact=Awaiting.GATE_RESULT)
        self._accept_artifact("target-gate-result", spec, raw)
        return RevisionVerified(
            gate_category=category,
            descends_from_base=git_facts.descends_from_base,
            contiguous=git_facts.contiguous,
            no_foreign_commits=git_facts.no_foreign_commits,
            files_in_scope=git_facts.files_in_scope,
            worktree_clean=git_facts.worktree_clean,
        )

    def _do_review(self, snapshot: LaneSnapshot) -> Event:
        spec = self._spec(snapshot, "reviewer", AgentAction.REVIEW)
        self._plan_invocation(spec, counted=True, count=snapshot.agent_invocations)
        raw = self._reviewer.review(spec)
        try:
            summary = validate_review(
                self._schemas,
                raw,
                expected_lens=Lens.GATING,
                historical=snapshot.historical_blocking_states(),
            )
        except ArtifactError as exc:
            self._reject_artifact("review", spec, exc)
            return ArtifactRejected(artifact=Awaiting.REVIEW)
        self._accept_artifact("review", spec, raw)
        return ReviewAccepted(
            lens=summary.lens,
            verdict=summary.verdict,
            has_scope_observations=summary.has_scope_observations,
            new_findings=summary.new_findings,
            prior_findings=summary.prior_findings,
        )

    def _do_guidance(self, snapshot: LaneSnapshot) -> Event:
        spec = self._spec(snapshot, "reviewer", AgentAction.GUIDANCE)
        self._plan_invocation(spec, counted=True, count=snapshot.agent_invocations)
        raw = self._reviewer.guidance(spec)
        expected = frozenset(
            f.finding_id
            for f in snapshot.findings
            if f.blocking and f.state is FindingState.GUIDANCE_REQUIRED and not f.guidance_given
        )
        try:
            summary = validate_guidance(self._schemas, raw, expected_finding_ids=expected)
        except ArtifactError as exc:
            self._reject_artifact("guidance", spec, exc)
            return ArtifactRejected(artifact=Awaiting.GUIDANCE)
        self._accept_artifact("guidance", spec, raw)
        return GuidanceAccepted(finding_ids=summary.finding_ids)
