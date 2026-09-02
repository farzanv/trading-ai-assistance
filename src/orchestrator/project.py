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

from orchestrator.model import (
    GateCategory,
    HARD_MAX_AGENT_INVOCATIONS,
    HARD_MAX_ROUNDS,
    LaneIdentity,
    LanePolicy,
    REVIEW_KIND_FOR_LANE_KIND,
)


class ProjectError(Exception):
    """Registry/config/work-index loading failed. Fail closed; never guess."""


#: Safe identifier for run/lane IDs: no path separators, no traversal, no
#: reserved names — an identifier is never allowed to steer a filesystem path.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def validate_identifier(kind: str, value: str) -> str:
    if not _SAFE_ID_RE.fullmatch(value) or ".." in value:
        raise ProjectError(f"unsafe {kind} identifier: {value!r}")
    return value


def resolve_lane_binding(project: ProjectConfig, identity: LaneIdentity) -> tuple[LanePolicy, str]:
    """The complete lane-identity preflight, returning (policy, review_kind).

    Shared verbatim by the coordinator (opening a lane) and the replay
    context (replaying one), so replay enforces every invariant the
    coordinator does: safe lane identifier, project binding, full-SHA scope
    base, declared work item, the manifest REGISTERED for that work item,
    the registered lane policy, and the review kind for the lane kind.
    """
    validate_identifier("lane", identity.lane_id)
    if identity.project_id != project.project_id:
        raise ProjectError(
            f"lane identity project {identity.project_id!r} is not the registered "
            f"project {project.project_id!r}"
        )
    if not _FULL_SHA_RE.fullmatch(identity.scope_base):
        raise ProjectError("scope_base must be a full 40-hex SHA")
    work_item = project.work_item(identity.work_item)  # must be declared, never inferred
    expected_manifest = f"{project.manifest_root}/{work_item.manifest}"
    if identity.manifest != expected_manifest:
        raise ProjectError(
            f"manifest {identity.manifest!r} does not match the declared work item "
            f"({expected_manifest!r})"
        )
    review_kind = REVIEW_KIND_FOR_LANE_KIND.get(work_item.kind)
    if review_kind is None:
        raise ProjectError(f"work item kind {work_item.kind!r} has no review kind")
    return project.lane_policy(work_item.kind), review_kind


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
    lane_policies: tuple[tuple[str, LanePolicy], ...]  # (lane kind, policy)
    config_digest: str  # sha256 of project.yaml
    work_index_digest: str  # sha256 of work-index.json
    work_items: tuple[WorkItem, ...]

    def lane_policy(self, lane_kind: str) -> LanePolicy:
        """The registered policy for a lane kind — the only policy source."""
        for kind, policy in self.lane_policies:
            if kind == lane_kind:
                return policy
        raise ProjectError(
            f"lane kind {lane_kind!r} has no registered policy in {self.project_id}"
        )

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

    lanes_path = dict(package_paths).get("policies.lanes")
    if lanes_path is None:
        raise ProjectError(f"{config_path}: policies.lanes is required")
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
        lane_policies=_load_lane_policies(lanes_path),
        config_digest=_sha256_file(config_path),
        work_index_digest=_sha256_file(project_dir / "work-index.json"),
        work_items=work_items,
    )


def _bounded_int(value: object, ceiling: int) -> int:
    """A genuine positive integer at or below the hard ceiling.

    Booleans, floats, and numeric strings are rejected — no coercion may loosen
    a fail-closed bound, and a configured bound may only tighten the ceiling.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"bound must be an integer, got {type(value).__name__}")
    if value < 1:
        raise ValueError(f"bound must be positive, got {value}")
    if value > ceiling:
        raise ValueError(f"bound {value} exceeds the hard ceiling {ceiling}")
    return value


def _load_lane_policies(lanes_path: Path) -> tuple[tuple[str, LanePolicy], ...]:
    """Parse the project's lane policy file into typed, validated policies."""
    data = _load_yaml(lanes_path)
    lanes = data.get("lanes")
    if not isinstance(lanes, dict) or not lanes:
        raise ProjectError(f"{lanes_path}: missing lanes block")
    policies: list[tuple[str, LanePolicy]] = []
    for kind, cfg in sorted(lanes.items()):
        if not isinstance(cfg, dict):
            raise ProjectError(f"{lanes_path}: lane {kind!r} is not a mapping")
        try:
            accepted = frozenset(
                GateCategory(name) for name in cfg["accepted_gate_categories"]
            )
            max_rounds = _bounded_int(cfg["max_rounds"], HARD_MAX_ROUNDS)
            max_invocations = _bounded_int(
                cfg["max_agent_invocations"], HARD_MAX_AGENT_INVOCATIONS
            )
            author_agent = str(cfg["author"]["agent"])
            reviewer_agent = str(cfg["reviewer"]["agent"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectError(f"{lanes_path}: lane {kind!r} policy invalid: {exc}") from None
        if author_agent == reviewer_agent:
            # Non-authoring review is structural: the same agent can never
            # hold both roles in one lane kind.
            raise ProjectError(f"{lanes_path}: lane {kind!r} author equals reviewer")
        policies.append(
            (
                kind,
                LanePolicy(
                    lane_kind=kind,
                    accepted_gate_categories=accepted,
                    max_rounds=max_rounds,
                    max_agent_invocations=max_invocations,
                    author_agent=author_agent,
                    reviewer_agent=reviewer_agent,
                ),
            )
        )
    return tuple(policies)


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
