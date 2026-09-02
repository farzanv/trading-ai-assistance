"""The lane coordinator: imperative shell around the pure reducer.

The coordinator is constructed from a registered Project Control Plane
configuration plus an immutable lane identity. It derives its run directory
through the project's containment-checked state root, executes reducer-selected
commands through the driver interfaces, validates every returned artifact
against the lane identity and the verified candidate revision, appends ledger
evidence, and calls the reducer again. It holds no transition judgement of its
own (functional core / imperative shell, architecture §3).

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

from orchestrator.agents import (
    AuthorDriver,
    CandidateFacts,
    GateDriver,
    GitDriver,
    InvocationSpec,
    ReviewerDriver,
)
from orchestrator.artifacts import (
    ArtifactError,
    SchemaSet,
    artifact_digest,
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
from orchestrator.ledger import KIND_DECISION, KIND_EFFECT, Ledger, run_end_kind
from orchestrator.model import (
    AgentAction,
    ArtifactRejected,
    Awaiting,
    Command,
    Event,
    InvokeAuthor,
    InvokeGuidance,
    InvokeReviewer,
    LandRevision,
    LaneAuthorized,
    LaneIdentity,
    LanePolicy,
    LaneSnapshot,
    Lens,
    OpenHumanGate,
    RevisionVerified,
    Severity,
    VerifyRevision,
    event_to_dict,
    policy_digest,
    snapshot_to_dict,
)
from orchestrator.project import ProjectConfig, ProjectError, resolve_lane_binding
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
        project: ProjectConfig,
        identity: LaneIdentity,
        run_id: str,
        schemas: SchemaSet,
        author: AuthorDriver,
        reviewer: ReviewerDriver,
        gate: GateDriver,
        git: GitDriver,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        # The complete lane-identity preflight and policy resolution are the
        # shared resolve_lane_binding — the same function replay_context uses,
        # so a lane can never open under an invariant replay would not check.
        try:
            policy, review_kind = resolve_lane_binding(project, identity)
        except ProjectError as exc:
            raise CoordinatorError(str(exc)) from None
        self._project = project
        self._identity = identity
        self._run_id = run_id
        self._policy = policy
        self._review_kind = review_kind
        self._schemas = schemas
        self._author = author
        self._reviewer = reviewer
        self._gate = gate
        self._git = git
        self._run_dir = project.run_dir(run_id)  # containment-checked, project-local
        self._artifacts_dir = self._run_dir / "lanes" / identity.lane_id / "artifacts"
        self._clock = clock
        self._ledger = Ledger(self._run_dir / "ledger.jsonl")

    @property
    def ledger_path(self) -> Path:
        return self._ledger.path

    @property
    def policy(self) -> LanePolicy:
        return self._policy

    def run(self, max_steps: int = 200) -> RunResult:
        """Drive the lane from AUTHORIZED until convergence, gate, or STOP."""
        self._open_lane()
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
                self._identity.lane_id,
                self._clock(),
                {
                    "event": event_to_dict(event),
                    "state_before": snapshot.state.value,
                    "state_after": decision.snapshot.state.value,
                    "reason": decision.reason.value,
                    "command": decision.command.kind if decision.command is not None else None,
                    "inputs": decision.inputs,
                    "snapshot_after": snapshot_to_dict(decision.snapshot),
                },
            )
            snapshot = decision.snapshot
            command = decision.command
            if command is None or isinstance(command, (OpenHumanGate, LandRevision)):
                self._end_run(snapshot, command, decision.reason.value)
                return RunResult(snapshot=snapshot, steps=steps, ledger_path=self._ledger.path)
            event = self._execute(command, snapshot)

    # -- effects ------------------------------------------------------------

    def _open_lane(self) -> None:
        """Bind the immutable lane context into the ledger before any decision."""
        self._ledger.append(
            KIND_EFFECT,
            self._identity.lane_id,
            self._clock(),
            {
                "effect": "LANE_OPENED",
                "run_id": self._run_id,
                "project_id": self._project.project_id,
                "work_item": self._identity.work_item,
                "manifest": self._identity.manifest,
                "scope_base": self._identity.scope_base,
                "lane_kind": self._policy.lane_kind,
                "policy_digest": policy_digest(self._policy),
                "package_digests": [list(pair) for pair in self._project.package_digests],
                "schemas_digest": self._schemas.digest,
                "config_digest": self._project.config_digest,
                "work_index_digest": self._project.work_index_digest,
            },
        )

    def _end_run(self, snapshot: LaneSnapshot, command: Command | None, reason: str) -> None:
        self._write_p3_backlog(snapshot)
        # run_end_kind is the single authority replay re-derives this from.
        self._ledger.append(
            KIND_EFFECT,
            self._identity.lane_id,
            self._clock(),
            {
                "effect": "RUN_END",
                "end": run_end_kind(command),
                "reason": reason,
                "state": snapshot.state.value,
            },
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
            invocation_id=f"{self._identity.lane_id}-I{snapshot.agent_invocations:03d}",
            project_id=self._project.project_id,
            lane_id=self._identity.lane_id,
            work_item=self._identity.work_item,
            manifest=self._identity.manifest,
            scope_base=self._identity.scope_base,
            current_sha=snapshot.current_sha,
            current_tree=snapshot.current_tree,
            role=role,
            action=action,
            review_round=snapshot.review_round,
            revision=snapshot.revision,
            attempt=snapshot.artifact_retries,
            package_digests=self._project.package_digests,
            schemas_digest=self._schemas.digest,
        )

    def _plan_invocation(self, spec: InvocationSpec, counted: bool, count: int) -> None:
        self._ledger.append(
            KIND_EFFECT,
            self._identity.lane_id,
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

    def _accept_artifact(
        self,
        kind: str,
        spec: InvocationSpec,
        raw: object,
        git_facts: CandidateFacts | None = None,
    ) -> None:
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = self._artifacts_dir / f"{spec.invocation_id}-{kind}.json"
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        path.write_text(canonical + "\n", encoding="utf-8")
        payload: dict = {
            "effect": "ARTIFACT_ACCEPTED",
            "artifact": kind,
            "invocation_id": spec.invocation_id,
            "digest": artifact_digest(raw),  # type: ignore[arg-type]
            "path": path.name,
        }
        if git_facts is not None:
            payload["git"] = {
                "exists": git_facts.exists,
                "tree_digest": git_facts.tree_digest,
                "descends_from_scope_base": git_facts.descends_from_scope_base,
                "descends_from_previous_candidate": git_facts.descends_from_previous_candidate,
            }
        self._ledger.append(KIND_EFFECT, self._identity.lane_id, self._clock(), payload)

    def _candidate_errors(self, claimed_tree: str, facts: CandidateFacts) -> list[str]:
        """Repository facts must corroborate the claimed candidate (never trust)."""
        errors: list[str] = []
        if not facts.exists:
            errors.append("claimed commit does not exist in the lane repository")
            return errors
        if facts.tree_digest != claimed_tree:
            errors.append("claimed tree_digest is not the commit's tree in Git")
        if not facts.descends_from_scope_base:
            errors.append("commit does not descend from scope_base")
        if not facts.descends_from_previous_candidate:
            errors.append("commit does not descend from the previous accepted candidate")
        return errors

    def _reject_artifact(self, kind: str, spec: InvocationSpec, error: ArtifactError) -> None:
        # ArtifactError.errors is sanitized (paths/keywords, no raw values).
        self._ledger.append(
            KIND_EFFECT,
            self._identity.lane_id,
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
            summary = validate_author_result(
                self._schemas,
                raw,
                identity=self._identity,
                expected_revision=1,
                expected_author_agent=self._policy.author_agent,
            )
        except ArtifactError as exc:
            self._reject_artifact("author-result", spec, exc)
            return ArtifactRejected(artifact=Awaiting.AUTHOR_RESULT)
        # The first candidate's previous candidate is the immutable base.
        facts = self._git.candidate_facts(spec, summary.commit, self._identity.scope_base)
        errors = self._candidate_errors(summary.tree_digest, facts)
        if errors:
            self._reject_artifact("author-result", spec, ArtifactError("author-result", errors))
            return ArtifactRejected(artifact=Awaiting.AUTHOR_RESULT)
        self._accept_artifact("author-result", spec, raw, git_facts=facts)
        return event_from_author_summary(summary)

    def _do_repair(self, snapshot: LaneSnapshot) -> Event:
        spec = self._spec(snapshot, "author", AgentAction.REPAIR)
        self._plan_invocation(spec, counted=True, count=snapshot.agent_invocations)
        raw = self._author.repair(spec)
        try:
            summary = validate_fold(
                self._schemas,
                raw,
                identity=self._identity,
                outstanding_ids=snapshot.fold_outstanding_ids(),
                expected_revision=snapshot.revision + 1,
                prev_sha=snapshot.current_sha,
                expected_author_agent=self._policy.author_agent,
            )
        except ArtifactError as exc:
            self._reject_artifact("fold", spec, exc)
            return ArtifactRejected(artifact=Awaiting.FOLD)
        # A repair must ADVANCE the lane: descend from the previous accepted
        # candidate, never roll back to an earlier revision.
        facts = self._git.candidate_facts(spec, summary.commit, snapshot.current_sha)
        errors = self._candidate_errors(summary.tree_digest, facts)
        if errors:
            self._reject_artifact("fold", spec, ArtifactError("fold", errors))
            return ArtifactRejected(artifact=Awaiting.FOLD)
        self._accept_artifact("fold", spec, raw, git_facts=facts)
        return event_from_fold_summary(summary)

    def _do_verify(self, snapshot: LaneSnapshot) -> Event:
        spec = self._spec(snapshot, "gate", None)
        self._plan_invocation(spec, counted=False, count=snapshot.agent_invocations)
        git_facts, raw = self._gate.verify(spec)
        try:
            summary = validate_gate_result(
                self._schemas,
                raw,
                identity=self._identity,
                current_sha=snapshot.current_sha,
                current_tree=snapshot.current_tree,
            )
        except ArtifactError as exc:
            self._reject_artifact("target-gate-result", spec, exc)
            return ArtifactRejected(artifact=Awaiting.GATE_RESULT)
        self._accept_artifact("target-gate-result", spec, raw)
        return RevisionVerified(
            gate_category=summary.category,
            descends_from_base=git_facts.descends_from_base,
            contiguous=git_facts.contiguous,
            no_foreign_commits=git_facts.no_foreign_commits,
            files_in_scope=git_facts.files_in_scope,
            worktree_clean=git_facts.worktree_clean,
            failed_checks=summary.failed_checks,
        )

    def _do_review(self, snapshot: LaneSnapshot) -> Event:
        spec = self._spec(snapshot, "reviewer", AgentAction.REVIEW)
        self._plan_invocation(spec, counted=True, count=snapshot.agent_invocations)
        raw = self._reviewer.review(spec)
        try:
            summary = validate_review(
                self._schemas,
                raw,
                identity=self._identity,
                expected_lens=Lens.GATING,
                historical=snapshot.historical_blocking_states(),
                current_sha=snapshot.current_sha,
                current_tree=snapshot.current_tree,
                expected_review_kind=self._review_kind,
                expected_reviewer_agent=self._policy.reviewer_agent,
            )
        except ArtifactError as exc:
            self._reject_artifact("review", spec, exc)
            return ArtifactRejected(artifact=Awaiting.REVIEW)
        self._accept_artifact("review", spec, raw)
        return event_from_review_summary(summary)

    def _do_guidance(self, snapshot: LaneSnapshot) -> Event:
        spec = self._spec(snapshot, "reviewer", AgentAction.GUIDANCE)
        self._plan_invocation(spec, counted=True, count=snapshot.agent_invocations)
        raw = self._reviewer.guidance(spec)
        try:
            summary = validate_guidance(
                self._schemas,
                raw,
                identity=self._identity,
                expected_finding_ids=snapshot.guidance_expected_ids(),
                current_sha=snapshot.current_sha,
                current_tree=snapshot.current_tree,
                expected_review_kind=self._review_kind,
                expected_reviewer_agent=self._policy.reviewer_agent,
            )
        except ArtifactError as exc:
            self._reject_artifact("guidance", spec, exc)
            return ArtifactRejected(artifact=Awaiting.GUIDANCE)
        self._accept_artifact("guidance", spec, raw)
        return event_from_guidance_summary(summary)
