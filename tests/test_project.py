"""Project Control Plane loading: explicit discovery, containment, digests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.model import GateCategory
from orchestrator.project import ProjectError, load_project, load_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS = REPO_ROOT / "projects"


def test_registry_lists_the_engine_project() -> None:
    registry = load_registry(PROJECTS)
    assert set(registry) == {"trading-ai-engine"}
    assert registry["trading-ai-engine"].name == "project.yaml"


def test_unregistered_project_is_refused() -> None:
    with pytest.raises(ProjectError, match="not registered"):
        load_project(PROJECTS, "trading-ai-ui")


def test_engine_project_loads_with_digest_snapshot() -> None:
    config = load_project(PROJECTS, "trading-ai-engine")
    assert config.project_id == "trading-ai-engine"
    assert config.integration_branch == "development"
    assert config.manifest_root == "dil-engine/manifests"
    assert config.gate_contract_status == "BLOCKED_UNTIL_T0"
    assert "--json" in config.gate_command

    names = {name for name, _ in config.package_paths}
    assert {
        "agents.author",
        "agents.repair",
        "agents.reviewer",
        "skills.design",
        "skills.implementation",
        "skills.bookkeeping",
        "policies.lanes",
        "policies.limits",
        "policies.monitoring",
        "policies.persistence",
        "policies.safety",
    } <= names
    digests = dict(config.package_digests)
    assert all(len(d) == 64 for d in digests.values())

    # The shared safety package is explicitly bound, resolved under shared/.
    safety = dict(config.package_paths)["policies.safety"]
    assert safety.is_relative_to((REPO_ROOT / "shared").resolve())

    assert [w.work_id for w in config.work_items] == ["ca_platform_design_phase9"]
    assert config.state_root() == PROJECTS / "trading-ai-engine" / "state"


def _write_minimal_project(root: Path, agents_author: str) -> Path:
    projects = root / "projects"
    project = projects / "p1"
    project.mkdir(parents=True)
    (projects / "registry.yaml").write_text(
        "schema_version: 1\nprojects:\n  p1:\n    config: p1/project.yaml\n", encoding="utf-8"
    )
    (project / "project.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "project_id: p1",
                "repository:",
                "  path: c:/tmp/repo",
                "  integration_branch: main",
                "  manifest_root: manifests",
                "gate:",
                "  command: [python, gate.py]",
                "agents:",
                f"  author: {agents_author}",
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
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    lanes = project / "policies" / "lanes.yaml"
    lanes.parent.mkdir(parents=True, exist_ok=True)
    lanes.write_text(
        "\n".join(
            [
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
        json.dumps({"schema_version": 1, "project_id": "p1", "work_items": []}),
        encoding="utf-8",
    )
    return projects


def test_minimal_project_loads(tmp_path: Path) -> None:
    projects = _write_minimal_project(tmp_path, "agents/author.md")
    config = load_project(projects, "p1")
    assert dict(config.package_digests)["agents.author"]


def test_package_path_escape_fails_closed(tmp_path: Path) -> None:
    projects = _write_minimal_project(tmp_path, "../../../outside.md")
    (tmp_path / "outside.md").write_text("x\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="escapes"):
        load_project(projects, "p1")


def test_missing_package_file_fails_closed(tmp_path: Path) -> None:
    projects = _write_minimal_project(tmp_path, "agents/missing.md")
    with pytest.raises(ProjectError, match="does not exist"):
        load_project(projects, "p1")


def test_work_index_project_id_mismatch_fails_closed(tmp_path: Path) -> None:
    projects = _write_minimal_project(tmp_path, "agents/author.md")
    index = projects / "p1" / "work-index.json"
    index.write_text(
        json.dumps({"schema_version": 1, "project_id": "other", "work_items": []}),
        encoding="utf-8",
    )
    with pytest.raises(ProjectError, match="mismatch"):
        load_project(projects, "p1")


def test_registry_config_escape_fails_closed(tmp_path: Path) -> None:
    projects = _write_minimal_project(tmp_path, "agents/author.md")
    (projects / "registry.yaml").write_text(
        "schema_version: 1\nprojects:\n  p1:\n    config: ../p1.yaml\n", encoding="utf-8"
    )
    with pytest.raises(ProjectError, match="escapes"):
        load_registry(projects)


def test_run_dir_is_containment_checked_and_project_local(tmp_path: Path) -> None:
    projects = _write_minimal_project(tmp_path, "agents/author.md")
    config = load_project(projects, "p1")
    run_dir = config.run_dir("RUN-001")
    assert run_dir.is_relative_to((projects / "p1" / "state" / "runs").resolve())


@pytest.mark.parametrize(
    "bad_run_id",
    ["../other", "..", "a/b", "a\\b", ".hidden", "", "x" * 65, "run id"],
)
def test_unsafe_run_identifiers_fail_closed(tmp_path: Path, bad_run_id: str) -> None:
    projects = _write_minimal_project(tmp_path, "agents/author.md")
    config = load_project(projects, "p1")
    with pytest.raises(ProjectError, match="unsafe run identifier"):
        config.run_dir(bad_run_id)


def test_undeclared_work_item_fails_closed(tmp_path: Path) -> None:
    projects = _write_minimal_project(tmp_path, "agents/author.md")
    config = load_project(projects, "p1")
    with pytest.raises(ProjectError, match="not declared"):
        config.work_item("ghost_item")


def test_lane_policies_resolve_from_the_registered_project(tmp_path: Path) -> None:
    projects = _write_minimal_project(tmp_path, "agents/author.md")
    config = load_project(projects, "p1")
    policy = config.lane_policy("design")
    assert policy.max_rounds == 10 and policy.max_agent_invocations == 40
    assert policy.author_agent == "claude" and policy.reviewer_agent == "codex"
    assert GateCategory.DOCS_INCONCLUSIVE_SCOPE_PASS in policy.accepted_gate_categories
    with pytest.raises(ProjectError, match="no registered policy"):
        config.lane_policy("implementation")


def test_engine_project_declares_all_three_lane_policies() -> None:
    config = load_project(PROJECTS, "trading-ai-engine")
    kinds = {kind for kind, _ in config.lane_policies}
    assert kinds == {"design", "implementation", "bookkeeping"}
    design = config.lane_policy("design")
    assert design.max_rounds == 10 and design.max_agent_invocations == 40
    assert config.lane_policy("bookkeeping").max_rounds == 2
    assert len(config.config_digest) == 64 and len(config.work_index_digest) == 64


def test_lane_policy_author_equal_reviewer_fails_closed(tmp_path: Path) -> None:
    projects = _write_minimal_project(tmp_path, "agents/author.md")
    lanes = projects / "p1" / "policies" / "lanes.yaml"
    lanes.write_text(
        lanes.read_text(encoding="utf-8").replace("agent: codex", "agent: claude"),
        encoding="utf-8",
    )
    with pytest.raises(ProjectError, match="author equals reviewer"):
        load_project(projects, "p1")


def test_lane_policy_unknown_gate_category_fails_closed(tmp_path: Path) -> None:
    projects = _write_minimal_project(tmp_path, "agents/author.md")
    lanes = projects / "p1" / "policies" / "lanes.yaml"
    lanes.write_text(
        lanes.read_text(encoding="utf-8").replace("PASS]", "LOOKS_FINE]"),
        encoding="utf-8",
    )
    with pytest.raises(ProjectError, match="policy invalid"):
        load_project(projects, "p1")


def test_missing_lanes_block_fails_closed(tmp_path: Path) -> None:
    projects = _write_minimal_project(tmp_path, "agents/author.md")
    (projects / "p1" / "policies" / "lanes.yaml").write_text("x: 1\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="missing lanes block"):
        load_project(projects, "p1")
