"""Project Control Plane loading: registry, project config, work index.

Authority: docs/design/PROJECT_CONTROL_PLANE.md. Discovery is explicit — the
registry maps project IDs to configuration; directory scanning never chooses a
project or work item. Every referenced package path must resolve inside the
project's own folder or the repository's ``shared/`` root; traversal or
symlink escape fails closed. Referenced packages are digest-snapshotted so a
lane can pin exactly what it ran with.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


class ProjectError(Exception):
    """Registry/config/work-index loading failed. Fail closed; never guess."""


#: Safe identifier for run/lane IDs: no path separators, no traversal, no
#: reserved names — an identifier is never allowed to steer a filesystem path.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_identifier(kind: str, value: str) -> str:
    if not _SAFE_ID_RE.fullmatch(value) or ".." in value:
        raise ProjectError(f"unsafe {kind} identifier: {value!r}")
    return value


@dataclass(frozen=True)
class WorkItem:
    work_id: str
    kind: str
    manifest: str
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class ProjectConfig:
    project_id: str
    display_name: str
    root: Path
    repository_path: str
    integration_branch: str
    manifest_root: str
    gate_command: tuple[str, ...]
    gate_contract_status: str
    package_paths: tuple[tuple[str, Path], ...]  # (ref name, resolved path)
    package_digests: tuple[tuple[str, str], ...]  # (ref name, sha256)
    work_items: tuple[WorkItem, ...]

    def state_root(self) -> Path:
        return self.root / "state"

    def run_dir(self, run_id: str) -> Path:
        """Containment-checked project-local run directory (PCP §2, §9).

        Every runtime record of a run lives below this project's own
        ``state/runs/<run-id>/``; an identifier that would resolve anywhere
        else fails closed.
        """
        validate_identifier("run", run_id)
        state_root = self.state_root().resolve()
        run_dir = (state_root / "runs" / run_id).resolve()
        if not run_dir.is_relative_to(state_root / "runs"):
            raise ProjectError(f"run directory escapes the project state root: {run_id!r}")
        return run_dir

    def work_item(self, work_id: str) -> WorkItem:
        for item in self.work_items:
            if item.work_id == work_id:
                return item
        raise ProjectError(
            f"work item {work_id!r} is not declared in {self.project_id}'s work index"
        )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_yaml(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise ProjectError(f"cannot read {path}: {exc}") from None
    except yaml.YAMLError as exc:
        raise ProjectError(f"invalid YAML in {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ProjectError(f"{path}: expected a mapping at the top level")
    return data


def _contained(path: Path, roots: tuple[Path, ...]) -> bool:
    resolved = path.resolve()
    return any(resolved.is_relative_to(root.resolve()) for root in roots)


def load_registry(projects_root: Path) -> dict[str, Path]:
    """Return project_id -> resolved project.yaml path (containment-checked)."""
    registry = _load_yaml(projects_root / "registry.yaml")
    if registry.get("schema_version") != 1:
        raise ProjectError("registry.yaml: unsupported schema_version")
    projects = registry.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise ProjectError("registry.yaml: no projects declared")
    result: dict[str, Path] = {}
    for project_id, entry in projects.items():
        if not isinstance(entry, dict) or "config" not in entry:
            raise ProjectError(f"registry.yaml: project {project_id!r} lacks a config path")
        config_path = projects_root / entry["config"]
        if not _contained(config_path, (projects_root,)):
            raise ProjectError(f"registry.yaml: project {project_id!r} config escapes projects/")
        result[project_id] = config_path
    return result


def load_project(projects_root: Path, project_id: str) -> ProjectConfig:
    registry = load_registry(projects_root)
    if project_id not in registry:
        raise ProjectError(f"project {project_id!r} is not registered")
    config_path = registry[project_id]
    config = _load_yaml(config_path)
    if config.get("schema_version") != 1:
        raise ProjectError(f"{config_path}: unsupported schema_version")
    if config.get("project_id") != project_id:
        raise ProjectError(
            f"{config_path}: project_id {config.get('project_id')!r} != registry id {project_id!r}"
        )
    project_dir = config_path.parent
    shared_root = projects_root.parent / "shared"
    allowed_roots = (project_dir, shared_root)

    repository = config.get("repository")
    if not isinstance(repository, dict):
        raise ProjectError(f"{config_path}: missing repository block")
    gate = config.get("gate")
    if not isinstance(gate, dict) or not isinstance(gate.get("command"), list):
        raise ProjectError(f"{config_path}: missing gate.command")

    package_paths: list[tuple[str, Path]] = []
    for section in ("agents", "skills", "policies"):
        block = config.get(section)
        if not isinstance(block, dict) or not block:
            raise ProjectError(f"{config_path}: missing {section} block")
        for name, rel in sorted(block.items()):
            path = (project_dir / rel).resolve()
            if not _contained(path, allowed_roots):
                raise ProjectError(
                    f"{config_path}: {section}.{name} escapes the project/shared roots: {rel}"
                )
            if not path.is_file():
                raise ProjectError(f"{config_path}: {section}.{name} does not exist: {rel}")
            package_paths.append((f"{section}.{name}", path))

    work_items = _load_work_index(project_dir, project_id)
    return ProjectConfig(
        project_id=project_id,
        display_name=str(config.get("display_name", project_id)),
        root=project_dir,
        repository_path=str(repository.get("path", "")),
        integration_branch=str(repository.get("integration_branch", "")),
        manifest_root=str(repository.get("manifest_root", "")),
        gate_command=tuple(str(part) for part in gate["command"]),
        gate_contract_status=str(gate.get("contract_status", "")),
        package_paths=tuple(package_paths),
        package_digests=tuple((name, _sha256_file(path)) for name, path in package_paths),
        work_items=work_items,
    )


def _load_work_index(project_dir: Path, project_id: str) -> tuple[WorkItem, ...]:
    index_path = project_dir / "work-index.json"
    try:
        with index_path.open(encoding="utf-8") as fh:
            index = json.load(fh)
    except OSError as exc:
        raise ProjectError(f"cannot read {index_path}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise ProjectError(f"invalid JSON in {index_path}: {exc}") from None
    if index.get("schema_version") != 1 or index.get("project_id") != project_id:
        raise ProjectError(f"{index_path}: schema_version/project_id mismatch")
    items: list[WorkItem] = []
    seen: set[str] = set()
    for raw in index.get("work_items", []):
        work_id = raw.get("id")
        if not work_id or work_id in seen:
            raise ProjectError(f"{index_path}: missing or duplicate work item id {work_id!r}")
        seen.add(work_id)
        items.append(
            WorkItem(
                work_id=work_id,
                kind=str(raw.get("kind", "")),
                manifest=str(raw.get("manifest", "")),
                depends_on=tuple(raw.get("depends_on", [])),
            )
        )
    return tuple(items)
