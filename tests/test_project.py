"""Project Control Plane loading: explicit discovery, containment, digests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    for rel in ("agents/author.md", "skills/design.md", "policies/lanes.yaml"):
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
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
