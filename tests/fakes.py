"""Deterministic fake drivers and v2 artifact builders for the fast suite.

No fake ever spawns a process, touches the network, or reads a real target
repository (CLAUDE.md: drivers are behind interfaces with fake implementations).
Scripted fakes return pre-built artifacts in order; exhaustion is a test bug
and raises IndexError.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from orchestrator.agents import CandidateFacts, GitFacts, InvocationSpec
from orchestrator.model import LaneIdentity
from orchestrator.project import ProjectConfig, load_project

BASE_SHA = "b" * 40
WORK_ITEM = "sim_phase"
MANIFEST = "dil-engine/manifests/sim_phase.yaml"


def sha(n: int) -> str:
    return f"{n:040x}"


def tree(n: int) -> str:
    return f"{n + 5000:040x}"


GOOD_FACTS = GitFacts(
    descends_from_base=True,
    contiguous=True,
    no_foreign_commits=True,
    files_in_scope=True,
    worktree_clean=True,
)


def make_identity(lane_id: str = "LANE-SIM") -> LaneIdentity:
    return LaneIdentity(
        lane_id=lane_id, work_item=WORK_ITEM, scope_base=BASE_SHA, manifest=MANIFEST
    )


def make_test_project(root: Path, project_id: str = "sim-project") -> ProjectConfig:
    """Write a minimal, containment-valid project tree and load it."""
    projects = root / "projects"
    project = projects / project_id
    (project / "agents").mkdir(parents=True)
    (project / "skills").mkdir()
    (project / "policies").mkdir()
    (projects / "registry.yaml").write_text(
        f"schema_version: 1\nprojects:\n  {project_id}:\n    config: {project_id}/project.yaml\n",
        encoding="utf-8",
    )
    (project / "project.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                f"project_id: {project_id}",
                "repository:",
                "  path: c:/tmp/target-repo",
                "  integration_branch: development",
                "  manifest_root: dil-engine/manifests",
                "gate:",
                "  command: [python, gate.py, --json]",
                "agents:",
                "  author: agents/author.md",
                "skills:",
                "  design: skills/design.md",
                "policies:",
                "  lanes: policies/lanes.yaml",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for rel in ("agents/author.md", "skills/design.md"):
        (project / rel).write_text("package\n", encoding="utf-8")
    (project / "policies" / "lanes.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "lanes:",
                "  design:",
                "    author:   { agent: claude }",
                "    reviewer: { agent: codex }",
                "    accepted_gate_categories: [DOCS_INCONCLUSIVE_SCOPE_PASS, PASS]",
                "    max_rounds: 10",
                "    max_agent_invocations: 40",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project / "work-index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": project_id,
                "work_items": [
                    {"id": WORK_ITEM, "kind": "design", "manifest": "sim_phase.yaml"}
                ],
            }
        ),
        encoding="utf-8",
    )
    return load_project(projects, project_id)


def make_author_result(revision: int = 1, **overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": 2,
        "work_item": WORK_ITEM,
        "commit": sha(revision),
        "scope_base": BASE_SHA,
        "revision": revision,
        "tree_digest": tree(revision),
        "files_changed": ["docs/design/SIM_PHASE.md"],
        "tests": [{"command": "python -m pytest -q", "exit_ok": True}],
        "author": {"agent": "claude", "model": "claude-fable-5"},
    }
    result.update(overrides)
    return result


def make_finding(
    finding_id: str,
    severity: str = "P1",
    *,
    requires_ruling: bool = False,
    earlier_phase_gap: str | None = None,
    unknown_contract: bool = False,
    blocks_downstream: bool = False,
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "id": finding_id,
        "severity": severity,
        "section": "§1",
        "title": f"finding {finding_id}",
        "description": "a concrete defect",
        "required_change": "the required outcome",
        "requires_ruling": requires_ruling,
        "earlier_phase_gap": earlier_phase_gap,
        "blocks_downstream": blocks_downstream,
        "unknown_contract": unknown_contract,
    }
    if severity in {"P0", "P1", "P2"}:
        finding.update(
            root_cause="the verified root cause",
            consequence="the operational consequence",
            recommended_approach="the recommended technical approach",
            closure_evidence="the test that must prove closure",
        )
    return finding


def make_review(
    *,
    revision: int = 1,
    verdict: str = "FINDINGS",
    findings: Sequence[Mapping[str, Any]] = (),
    prior: Mapping[str, str] | None = None,
    lens: str = "gating",
    review_kind: str = "design",
    scope_observations: Sequence[str] = (),
    **overrides: Any,
) -> dict[str, Any]:
    review: dict[str, Any] = {
        "schema_version": 2,
        "reviewed_range": f"{BASE_SHA}..{sha(revision)}",
        "tree_digest": tree(revision),
        "manifest": MANIFEST,
        "review_kind": review_kind,
        "lens": lens,
        "verdict": verdict,
        "findings": list(findings),
        "prior_findings": [
            {"id": fid, "outcome": outcome} for fid, outcome in sorted((prior or {}).items())
        ],
        "scope_observations": list(scope_observations),
        "reviewer": {"agent": "codex", "model": "gpt-5.6-sol"},
    }
    review.update(overrides)
    return review


def make_guidance(
    finding_ids: Sequence[str], *, revision: int = 2, **overrides: Any
) -> dict[str, Any]:
    guidance = make_review(
        revision=revision,
        verdict="FINDINGS",
        lens="guidance",
        prior=None,
        guidance={
            "finding_ids": list(finding_ids),
            "why_failed": "the attempted repair addressed the symptom only",
            "recommended_repair": "rework the predicate so the invariant holds",
            "steps": ["revisit the root cause", "add the missing regression test"],
            "affected_components": ["reducer"],
            "invariants": ["fail closed on silence"],
            "required_tests": ["test that reproduces the original failure"],
            "unsafe_alternatives": ["suppressing the check"],
        },
    )
    guidance.update(overrides)
    return guidance


def make_fold(
    *,
    revision: int,
    dispositions: Mapping[str, str],
    **overrides: Any,
) -> dict[str, Any]:
    entries = []
    for finding_id, disposition in sorted(dispositions.items()):
        entry: dict[str, Any] = {"finding_id": finding_id, "disposition": disposition}
        if disposition == "FOLDED":
            entry.update(
                root_cause="the verified root cause",
                changed_files=["docs/design/SIM_PHASE.md"],
                tests=[{"test_id": "tests/test_sim.py::test_fix", "post_fix_result": "PASS"}],
            )
        else:
            entry["reason"] = "cited code and the governing contract"
        entries.append(entry)
    fold: dict[str, Any] = {
        "schema_version": 2,
        "commit": sha(revision),
        "revision": revision,
        "tree_digest": tree(revision),
        "folded_review_range": f"{BASE_SHA}..{sha(revision - 1)}",
        "dispositions": entries,
        "author": {"agent": "claude", "model": "claude-fable-5"},
    }
    fold.update(overrides)
    return fold


def make_gate_result(
    category: str = "DOCS_INCONCLUSIVE_SCOPE_PASS", *, revision: int = 1, **overrides: Any
) -> dict[str, Any]:
    passing = category in {"PASS", "DOCS_INCONCLUSIVE_SCOPE_PASS"}
    checks = (
        [{"name": "scope", "result": "PASS"}]
        if passing or category == "UNKNOWN"
        else [{"name": "scope", "result": "PASS"}, {"name": category.lower(), "result": "FAIL"}]
    )
    result: dict[str, Any] = {
        "schema_version": 2,
        "verdict": "PASS" if passing else "FAIL",
        "category": category,
        "resolved_range": f"{BASE_SHA}..{sha(revision)}",
        "manifest": MANIFEST,
        "tree_digest": tree(revision),
        "checks": checks,
    }
    result.update(overrides)
    return result


class FakeGit:
    """A linear lane history: BASE_SHA <- sha(1) <- sha(2) <- ... <- sha(depth).

    ``candidate_facts`` answers from that history: the claimed commit's tree is
    ``tree(n)``, and descent holds only when the candidate is strictly later
    than the previous accepted candidate — so a rollback fails.
    """

    def __init__(self, depth: int = 99) -> None:
        self._depth = depth

    def candidate_facts(
        self, spec: InvocationSpec, commit: str, previous_candidate: str
    ) -> CandidateFacts:
        try:
            n = int(commit, 16)
        except ValueError:
            n = -1
        exists = 1 <= n <= self._depth
        if previous_candidate == BASE_SHA:
            prev_n = 0
        else:
            prev_n = int(previous_candidate, 16)
        return CandidateFacts(
            exists=exists,
            tree_digest=tree(n) if exists else "",
            descends_from_scope_base=exists,
            descends_from_previous_candidate=exists and n > prev_n,
        )


def build_coordinator(
    tmp_path: Path,
    *,
    lane_id: str,
    schemas: Any,
    author: Any,
    reviewer: Any,
    gate: Any,
    clock: Any,
    git: Any = None,
    run_id: str = "RUN-001",
) -> Any:
    """A coordinator wired to a minimal Control Plane project under tmp_path.

    The lane policy is resolved from the project's registered lanes.yaml for
    the declared work-item kind — it is not a parameter.
    """
    from orchestrator.application import LaneCoordinator

    project = make_test_project(tmp_path / "control-plane")
    return LaneCoordinator(
        project=project,
        identity=make_identity(lane_id),
        run_id=run_id,
        schemas=schemas,
        author=author,
        reviewer=reviewer,
        gate=gate,
        git=git if git is not None else FakeGit(),
        clock=clock,
    )


class ScriptedAuthor:
    def __init__(
        self,
        author_results: Sequence[Mapping[str, Any]] = (),
        folds: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self._author_results = list(author_results)
        self._folds = list(folds)
        self.calls: list[str] = []

    def author(self, spec: InvocationSpec) -> Mapping[str, Any]:
        self.calls.append("author")
        return self._author_results.pop(0)

    def repair(self, spec: InvocationSpec) -> Mapping[str, Any]:
        self.calls.append("repair")
        return self._folds.pop(0)


class ScriptedReviewer:
    def __init__(
        self,
        reviews: Sequence[Mapping[str, Any]] = (),
        guidances: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self._reviews = list(reviews)
        self._guidances = list(guidances)
        self.calls: list[str] = []

    def review(self, spec: InvocationSpec) -> Mapping[str, Any]:
        self.calls.append("review")
        return self._reviews.pop(0)

    def guidance(self, spec: InvocationSpec) -> Mapping[str, Any]:
        self.calls.append("guidance")
        return self._guidances.pop(0)


class ScriptedGate:
    """Returns scripted (facts, result) pairs, then repeats the default."""

    def __init__(
        self,
        results: Sequence[tuple[GitFacts, Mapping[str, Any]]] = (),
        default_category: str = "DOCS_INCONCLUSIVE_SCOPE_PASS",
    ) -> None:
        self._results = list(results)
        self._default_category = default_category
        self.calls = 0

    def verify(self, spec: InvocationSpec) -> tuple[GitFacts, Mapping[str, Any]]:
        self.calls += 1
        if self._results:
            return self._results.pop(0)
        return GOOD_FACTS, make_gate_result(self._default_category, revision=spec.revision)
