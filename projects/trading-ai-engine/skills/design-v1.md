# Skill: design lane v1 (trading-ai-engine)

The reusable protocol for a design work item (a phase document). Selected
explicitly by the invocation envelope; never inferred.

- Author the phase document per the epic matrix row and the approved
  predecessors' seam sections supplied in the evidence package — no free-form
  summarization of predecessors.
- Tools: file edit, git, pytest, pyflakes. No database, no provider traffic,
  no web (structural: the session grants none).
- Record each review round in the document's round-record section
  (`## 11. Review round record`) and the manifest `status` block.
- Open decisions belong in the document's operator section; only the operator
  rules them. `requires_ruling` findings stop the lane for a human gate.
- Convergence for a docs-only slice requires reviewer CLEAN (`lens: gating`)
  plus target-gate category `DOCS_INCONCLUSIVE_SCOPE_PASS` or `PASS`.
