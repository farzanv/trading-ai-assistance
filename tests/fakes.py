"""Deterministic fake drivers and v2 artifact builders for the fast suite.

No fake ever spawns a process, touches the network, or reads a real target
repository (CLAUDE.md: drivers are behind interfaces with fake implementations).
Scripted fakes return pre-built artifacts in order; exhaustion is a test bug
and raises IndexError.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from orchestrator.agents import GitFacts, InvocationSpec

BASE_SHA = "b" * 40


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


def make_author_result(revision: int = 1, **overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": 2,
        "work_item": "sim_phase",
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
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "id": finding_id,
        "severity": severity,
        "title": f"finding {finding_id}",
        "description": "a concrete defect",
        "required_change": "the required outcome",
        "requires_ruling": requires_ruling,
        "earlier_phase_gap": earlier_phase_gap,
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
        "manifest": "dil-engine/manifests/sim_phase.yaml",
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
    result: dict[str, Any] = {
        "schema_version": 2,
        "category": category,
        "resolved_range": f"{BASE_SHA}..{sha(revision)}",
        "manifest": "dil-engine/manifests/sim_phase.yaml",
        "checks": [{"name": "scope", "result": "PASS"}],
    }
    result.update(overrides)
    return result


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
