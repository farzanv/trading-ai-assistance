# Projects — the Project Control Plane

Entry point for every project this orchestrator manages (design authority:
`docs/design/PROJECT_CONTROL_PLANE.md`). The control plane routes deterministic
work; it never replaces a target repository's technical authority. Target
source, manifests, engineering rules, and the deterministic release gate stay
in the target repository.

## How a project is found

`registry.yaml` is the only discovery mechanism. Directory scanning never
chooses a project or a work item. Each entry maps a stable `project_id` to that
project's `project.yaml`; the registry holds no runtime status.

## Registered projects

| Project ID | Target repository | Config |
|---|---|---|
| `trading-ai-engine` | `c:\Repos\trading-ai` (branch `development`) | `trading-ai-engine/project.yaml` |

## Per-project boundary (binding)

- `INDEX.md` — orientation and required reading order; never runtime status.
- `project.yaml` — repository path, integration branch, manifest root, gate,
  agent/skill/policy package references.
- `work-index.json` — declared work items and exact target-manifest references;
  never duplicates manifest scope; a listed-but-missing manifest is never
  executable and is never created by Assist or Codex.
- `agents/`, `skills/`, `policies/`, `plans/` — project-specific versioned
  packages; shared bases under `../shared/` are referenced explicitly, never
  implicitly.
- `state/` — generated, gitignored, project-local runtime data. The run ledger
  (`state/runs/<run-id>/ledger.jsonl`) is the sole authoritative runtime
  history. No project reads or writes another project's state.

V1 may register many projects but executes exactly one lane globally.
