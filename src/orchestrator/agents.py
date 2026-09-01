"""Driver interfaces for the lane author, lane reviewer, and target gate.

V0-A contains interfaces only — no subprocess, no CLI, no network. Real
`claude -p` / `codex exec` drivers and the credential-free verify worker are
V0-B scope. Tests use deterministic fake implementations of these protocols;
the coordinator depends only on the interfaces, so no code path here can spawn
a real agent.

Drivers return raw artifact mappings. They never decide a transition: the
coordinator validates every return through :mod:`orchestrator.artifacts` and
feeds only typed events to the reducer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from orchestrator.model import AgentAction


@dataclass(frozen=True)
class InvocationSpec:
    """Immutable envelope for one agent or gate invocation (architecture §6.1).

    Binds the complete Control Plane context: project, work item, manifest,
    exact base and candidate SHAs/tree, and the package/schema digests the
    lane was authorized with — so every invocation is attributable to exactly
    what it ran under.
    """

    invocation_id: str
    project_id: str
    lane_id: str
    work_item: str
    manifest: str
    scope_base: str
    current_sha: str
    current_tree: str
    role: str  # "author" | "reviewer" | "gate"
    action: AgentAction | None
    review_round: int
    revision: int
    attempt: int  # 0 first try, 1 malformed-artifact retry
    package_digests: tuple[tuple[str, str], ...]
    schemas_digest: str


@dataclass(frozen=True)
class GitFacts:
    """Deterministic repository facts the verify step establishes."""

    descends_from_base: bool
    contiguous: bool
    no_foreign_commits: bool
    files_in_scope: bool
    worktree_clean: bool


class AuthorDriver(Protocol):
    def author(self, spec: InvocationSpec) -> Mapping[str, Any]:
        """Produce an author-result/v2 artifact for the initial revision."""
        ...

    def repair(self, spec: InvocationSpec) -> Mapping[str, Any]:
        """Produce a fold/v2 artifact dispositioning every outstanding finding."""
        ...


class ReviewerDriver(Protocol):
    def review(self, spec: InvocationSpec) -> Mapping[str, Any]:
        """Produce a gating review/v2 artifact over the complete current range."""
        ...

    def guidance(self, spec: InvocationSpec) -> Mapping[str, Any]:
        """Produce a review/v2 artifact with ``lens: guidance`` and no code."""
        ...


class GateDriver(Protocol):
    def verify(self, spec: InvocationSpec) -> tuple[GitFacts, Mapping[str, Any]]:
        """Return deterministic git facts and a target-gate-result/v2 artifact."""
        ...
