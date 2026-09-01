# Skill: bookkeeping lane v1 (trading-ai-engine)

The reusable protocol for a bookkeeping work item — a separate operator-started
run whose manifest was prepared against the landed phase tip. Selected
explicitly by the invocation envelope; never inferred.

- Facts (ranges, round counts, severities, SHAs, dates) are rendered from the
  completed phase ledger and supplied in the evidence package; the author
  writes the prose and the commit. Rendered facts are never re-derived by
  hand.
- Update exactly the three record locations the target requires: the doc
  header `Status:` line, the manifest `status:` block + `verified_slices`, and
  the STATUS/handoff rollups.
- Tools: file edit, git only.
- A bookkeeping lane never claims phase completion beyond what the ledger
  records and never touches design content.
