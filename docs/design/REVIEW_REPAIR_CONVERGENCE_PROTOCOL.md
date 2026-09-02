# Review, Repair, and Convergence Protocol

**Status:** PROPOSED rev 3, 2026-08-31 — independent review and operator confirmations
folded: no-code guidance, cross-lane one-way authorship handoff, non-executable V0
counterexamples, precise ping-pong, and 40-agent-invocation cap; design section for integration with
`ORCHESTRATED_EXECUTION_DESIGN.md`. No implementation or target-repository action is
authorized by this document.

## 1. Purpose

This protocol governs the repeated Claude author/implementor ↔ Codex reviewer cycle. Its
purpose is to ensure that:

- every blocking finding is addressed and independently verified;
- no finding is skipped, forgotten, silently downgraded, or lost between revisions;
- a repair is reviewed for both finding closure and new regressions;
- repeated partial repairs trigger clearer technical assistance instead of a dead loop;
- a persistent repair can transfer to a separately authorized Codex-owned lane without
  sacrificing non-authoring review;
- long review histories do not cause either agent to rely on stale or hallucinated context; and
- the operator is involved only when technical collaboration cannot resolve the issue, a ruling
  or expanded authorization is required, or a deterministic stop condition fires.

The orchestrator routes and verifies the cycle. It never decides whether prose "looks good" and
never treats an agent's claim as evidence without checking the corresponding structured artifact,
Git state, and deterministic gates.

## 2. Normal review and repair cycle

```text
AUTHOR
  |
  v
VERIFY COMMIT / SCOPE / PERSISTENCE SAFETY / TARGET GATE
  |
  v
REVIEW COMPLETE CURRENT RANGE
  |-- CLEAN ------------------------------------------> CONVERGED
  |
  `-- FINDINGS
        |
        v
      REPAIR ATTEMPT
        |
        v
      VERIFY REPAIR
        |
        v
      RE-REVIEW COMPLETE CURRENT RANGE
        |-- all resolved -----------------------------> CONVERGED
        |-- progress made ----------------------------> next repair round
        |-- same finding persists --------------------> GUIDANCE MODE
        |-- prior problem returns --------------------> REOPEN finding
        |-- author/reviewer disagreement -------------> ADJUDICATION PATH
        `-- no viable in-scope solution --------------> HUMAN GATE
```

Every re-review examines the complete current `scope_base..current_sha` range. Codex must:

1. verify every prior finding's disposition;
2. confirm that the claimed fix satisfies the required outcome;
3. check that the repair did not reintroduce a previously resolved problem;
4. inspect the repair's regression surface for new blocking defects;
5. verify that tests exercise the real failure rather than merely pass; and
6. bind the review to the exact current commit and tree digest.

## 3. Finding identity and lifecycle

Every finding has a stable ID for the life of the lane. Reopening the same defect reuses the
same ID; wording changes do not create a new finding.

```text
OPEN -> FIX_CLAIMED -> VERIFIED_RESOLVED

OPEN -> FIX_CLAIMED -> STILL_PRESENT -> GUIDANCE_REQUIRED
     -> FIX_CLAIMED -> VERIFIED_RESOLVED

OPEN -> REJECTED_WITH_REASON -> REVIEWER_ACCEPTS_REJECTION
     -> CLOSED_NOT_A_DEFECT

OPEN -> REJECTED_WITH_REASON -> REVIEWER_DISAGREES
     -> TECHNICAL_CLARIFICATION -> SECOND_AUTHOR_DISPOSITION
     -> RESOLVED | ADJUDICATION_REQUIRED

VERIFIED_RESOLVED -> REOPENED -> REPAIR_REQUIRED
```

`REJECTED_WITH_REASON` means the author believes the finding is factually incorrect, already
covered by an enforceable mechanism, conflicts with a binding contract, or would create a more
serious defect. The rejection must cite concrete code, tests, or governing contracts.

Codex closes the item as `CLOSED_NOT_A_DEFECT` only after independently verifying that evidence.
If Codex disagrees, it must explain the missing technical fact and provide clarification before a
second author disposition. Two consecutive reasoned rejections of the same finding after
clarification produce `ADJUDICATION_REQUIRED`; the orchestrator never chooses between the two
agents.

## 4. Completeness enforcement

For every review containing blocking findings, the orchestrator requires exact set equality:

```text
outstanding P0-P2 finding IDs == author disposition finding IDs
```

Missing, duplicate, or unknown IDs make the fold artifact malformed and STOP the transition.
A finding cannot disappear because a later response omits it.

For every blocking finding, the author's structured disposition records:

- stable finding ID;
- disposition;
- verified root cause;
- changed files and change summary;
- tests and other evidence;
- commit containing the repair;
- any deviation from Codex guidance; and
- any required scope expansion, unknown contract, or operator action.

Allowed dispositions are:

- `FOLDED`;
- `REJECTED_WITH_REASON`;
- `BLOCKED_NEEDS_TECHNICAL_GUIDANCE`;
- `UNKNOWN_CONTRACT`;
- `REQUIRES_SCOPE_EXPANSION`; and
- `REQUIRES_OPERATOR_ACTION`.

The schema must prohibit `CLEAN` while any P0-P2 finding is open, still present, reopened, or
awaiting adjudication.

## 5. Required quality of a Codex finding

A blocking finding must be technically actionable. It records:

- severity and stable ID;
- exact location and failure mode;
- root cause;
- operational or correctness consequence;
- required outcome;
- recommended technical approach;
- affected components;
- invariants that must remain true;
- tests and evidence required for closure; and
- rejected alternatives when they are likely to be attempted but unsafe.

Codex remains non-authoring during ordinary review, but non-authoring review must not be vague.
The author must receive enough technical clarity to implement a correct repair.

## 6. Guidance mode

Guidance mode begins when:

- the author returns `BLOCKED_NEEDS_TECHNICAL_GUIDANCE`;
- a claimed repair leaves the same finding materially unchanged; or
- the first repair addressed symptoms but not the verified root cause.

The orchestrator sends Codex the exact current SHA, current code, the finding's structured
history, the attempted repair, the author's explanation, and current test evidence. Codex must
not merely repeat the finding. It returns:

1. why the attempted approach failed;
2. a concrete recommended repair;
3. ordered implementation steps;
4. affected components and contracts;
5. invariants to preserve;
6. required failure and regression tests; and
7. alternatives that would remain unsafe.

GUIDANCE may name functions, interfaces, SQL contracts, and ordered edits, but it must not
contain a patch, replacement function/body, or other code intended for direct integration.
Crossing that line requires the separately authorized authorship handoff in §7.

The author then validates the diagnosis against the current code, implements the guidance, runs
the required tests, and records any justified deviation. Blind copying and silent substitution
are both prohibited.

## 7. Cross-lane authorship handoff

GUIDANCE is intentionally non-authoring: Codex explains why the repair failed, identifies the
required outcome and invariants, and names the tests, but supplies no patch or replacement code.
If the same finding remains after the guided repair, the lane records `HANDOFF_REQUIRED` and
stops at its verified tip. Roles never mutate inside an authorized lane.

After explicit operator authorization, Claude first prepares the handoff manifest in a
separate manually initiated/reviewed manifest-preparation slice rooted at the stopped tip.
Codex reviews that control-contract slice; it does not claim the transferred defect is fixed.
The Codex-owned repair lane then opens with:

- literal `scope_base` equal to the accepted manifest-preparation commit, whose parent chain
  contains the stopped lane's verified tip;
- owner Codex and reviewer Claude;
- manifest fields naming the stopped lane/tip, transferred stable finding IDs, and complete
  attempt history;
- scope limited to the repair, necessary production files, and regression tests;
- dependency on the stopped lane and its exact tree digest; and
- one-way handoff policy—ownership cannot swap back for this work item.

The original lane and manifest-preparation slice do not land independently. Claude reviews the complete dependent-lane
range and explicitly resolves every transferred finding. Only after all three ranges' deterministic
gates pass, the dependent range is CLEAN, all transferred findings are `VERIFIED_RESOLVED`, the
integration tip is unchanged, and the final combined tree digest matches may the contiguous
ranges land locally as one audited handoff chain.

Codex may provide a post-repair self-check, but its review artifact must use `lens: advisory`.
The schema and reducer structurally prohibit an advisory artifact from supplying `CLEAN` or a
landing predicate. If the Codex-owned lane fails Claude review and cannot repair within its
bounds, it stops for the operator; it never hands ownership back to Claude.

## 8. Dead-loop and regression detection

Revision count alone is not evidence of a dead loop. A complex work item may legitimately reach
revision 10 when each round makes material progress or exposes a distinct valid defect. Loop
controls therefore operate primarily per finding.

### 8.1 Stagnation

Stagnation occurs when the same finding remains open without materially new code or evidence, a
`FOLDED` disposition produces no relevant tree change, or substantially the same failed repair is
repeated. First occurrence enters guidance mode; the next unsuccessful guided repair stops
`HANDOFF_REQUIRED` for the separately authorized lane in §7.

### 8.2 Ping-pong

Ping-pong occurs when a finding previously recorded `VERIFIED_RESOLVED` is later recorded
`REOPENED` twice. It indicates an unstable fix or unresolved contract interpretation and
produces a STOP for adjudication.

### 8.3 Oscillation

Oscillation occurs when repairing finding A repeatedly recreates finding B, or revisions
alternate between incompatible implementations. Codex must provide a combined solution that
satisfies both invariants. A required contract change or scope expansion produces a human gate.

### 8.4 Regression

If a previously resolved defect returns, its original ID is marked `REOPENED`; it is never
reported as a new minor finding or silently tolerated. `CLEAN` is impossible until it is verified
resolved again.

## 9. Context and hallucination controls

Long chat history is never the source of truth. Each invocation receives a generated evidence
package containing:

- project ID plus the immutable project `INDEX.md`, configuration, role-package, skill,
  policy, and schema digests;
- exact base, current commit, and tree digest;
- manifest and authorization snapshot;
- complete current diff;
- structured finding ledger and lifecycle state;
- the author's dispositions;
- test, RVA, scope, and structured persistence-safety evidence; and
- applicable governing contracts.

The lane reviewer examines current repository state, never memory of an earlier revision. Every
gating review uses a fresh reviewer process and the complete current lane range plus structured
finding history. A handoff lane therefore receives a fresh Claude reviewer process; Codex's
optional self-check is separate and advisory.

Session resume is an efficiency optimization for the author, never a correctness dependency.
Every resumed author prompt still carries the current immutable evidence package.

## 10. Test integrity and reviewer counterexamples

Green tests are evidence only for the scenarios they exercise. They do not prove that the
implementation is correct outside those scenarios. Test quality is therefore reviewed and
verified independently; the orchestrator never treats a passing count as sufficient proof.

### 10.1 Regression-test proof

For every testable defect, the preferred proof is:

```text
new or changed test FAILS on pre-fix production code
                        |
                        v
                 repair is applied
                        |
                        v
same test PASSES on the repaired revision
```

The orchestrator performs this comparison in isolated worktrees and records both command results
and output digests. If a regression test already passes against the pre-fix production code, it
does not normally prove the reported defect was repaired and requires an explicit explanation.

The author test-evidence artifact records:

- finding ID and test node ID;
- behavior or invariant proved;
- validation kind (unit, integration, database, concurrency, provider-capture, property, or
  static proof);
- pre-fix result and expected failure reason;
- post-fix result;
- command and output digest; and
- any environmental grant or fixture required.

### 10.2 Prohibited test manipulation

The orchestrator inspects the test diff and flags:

- added `skip`, `skipif`, or `xfail` markers;
- deleted or commented-out tests or assertions;
- weakened expected values, boundaries, or tolerances;
- exceptions caught and ignored;
- replacement of the component under test with a mock;
- removal of negative or failure-path coverage;
- tests that assert only "no exception" or an always-true condition;
- unexplained modifications to an existing test; and
- an unexpected reduction in collected test count.

A skip, deletion, bypass, or unexplained weakening is a STOP. If the author believes an existing
test is wrong, it must return a reasoned objection with code and contract evidence and wait for
review; it may not silently change the test to make production behavior pass.

### 10.3 Codex test-quality review

Codex reviews tests as carefully as production code. For each repaired finding it checks:

1. whether the test reproduces the original failure;
2. whether it exercises the real production path;
3. whether assertions measure observable required behavior rather than incidental internals;
4. whether mocks hide the component or integration boundary containing the defect;
5. whether relevant success, failure, retry, boundary, idempotency, and concurrency cases are
   covered;
6. whether reverting the essential repair would make the test fail; and
7. whether a real database, provider-shaped capture, or other integration test is required
   because a unit mock cannot prove the behavior.

Inadequate test evidence is itself a review finding even when the production change appears
correct and the suite is green.

### 10.4 Reviewer counterexample evidence

When Codex finds a P1/P2 that the author's successful tests missed, it provides a concrete,
non-executed counterexample in `review.json`. It records:

- why the existing tests missed the defect;
- exact inputs and preconditions;
- expected and actual behavior;
- the governing requirement or safety invariant;
- a proposed test shape, query expectation, concurrency schedule, captured-payload facts, or
  deterministic reproduction description; and
- the permanent behavior the repository test must protect.

The author then:

1. validates that the counterexample represents a real approved contract;
2. fixes the production code;
3. adds an appropriate permanent regression test to the repository;
4. explains any justified difference from the proposed test shape; and
5. runs the permanent repository tests through the restricted verify worker.

The original counterexample remains digest-bound evidence across later revisions. The
orchestrator never executes reviewer-supplied code, SQL, or commands in V0. Executable external
challenges and targeted mutation testing are deferred until a dedicated restricted-execution
contract exists. A concern need not include executable code, but it cannot rely on unsupported
speculation.

### 10.5 Challenging reviewer evidence

The author may return `REJECTED_WITH_REASON` when the counterexample contradicts an approved
contract, invokes a non-production path, depends on an invalid assumption, or expects behavior
outside scope. The reviewer independently corrects/withdraws it or explains why the defect
remains. Continued disagreement follows §3; the counterexample is closure evidence only when it
accurately represents an approved requirement and the permanent repository test proves it.

## 11. Prompt obligations

### 11.1 Codex review prompt

The reviewer prompt requires Codex to:

- apply the exact project-selected reviewer role and skill package whose digests are in
  the invocation, together with the target repository's governing rules;
- review the exact complete range;
- reconcile every prior finding explicitly;
- reuse stable IDs for persistent or reopened defects;
- inspect for regressions and new blockers;
- provide root cause, required outcome, technical guidance, and closure tests;
- enter concrete no-code guidance when requested and emit `HANDOFF_REQUIRED` when it fails;
- avoid unrelated redesign during correction rounds unless a genuine P0/P1 is discovered; and
- return `CLEAN` only when the structured convergence predicate is satisfied;
- review the quality of author-added or modified tests; and
- provide reproducible counterexample evidence and a required permanent-test shape for a
  testable missed defect, without executable artifact code.

### 11.2 Claude repair prompt

The author prompt requires Claude to:

- apply the exact project-selected author/repair role and skill package whose digests are
  in the invocation, together with the target repository's governing rules;
- disposition every outstanding P0-P2 finding exactly once;
- verify root causes instead of patching symptoms;
- implement guidance completely or reject it with evidence;
- never skip, weaken, delete, or rewrite tests merely to obtain green output;
- provide pre-fix-fail/post-fix-pass evidence for regression tests;
- validate the immutable reviewer counterexample and add the permanent repository test;
- never widen scope or use new authority silently;
- run the required focused and regression tests;
- commit the repair; and
- return a schema-valid fold artifact bound to that commit.

### 11.3 Handoff-lane prompt

The Codex author prompt receives the transferred findings but no authority beyond the new
manifest. Claude's gating review covers the entire handoff-lane range. Any Codex self-check uses
`lens: advisory`; prompts and schema both state that it cannot return gating CLEAN.

## 12. Counters and escalation

Recommended defaults:

- first unsuccessful repair: detailed guidance;
- finding still present after the guided repair: `HANDOFF_REQUIRED` and stop;
- separately authorized handoff: new Codex-owned/Claude-reviewed lane, one-way;
- two consecutive reasoned rejections after clarification: adjudication;
- a previously `VERIFIED_RESOLVED` finding later `REOPENED` twice: ping-pong STOP;
- lane revision limit: 10, never forced acceptance;
- agent invocation limit: 40; increment immediately before every spawned Claude/Codex
  process, including malformed-output retries and post-pause retries; and
- provider-request budgets: lane-configured hard STOPs. V1 has no token or total lane
  wall-clock budget; a no-progress child timeout is process recovery, not convergence.

Subscription limits and service outages are not repair failures and do not increment
review/finding counters. They enter `PAUSED_LIMIT`, persist the exact interrupted action, follow
the hourly/weekly/outage schedules in the governing execution design, and return to the
same action after a scheduled or operator-requested `retry-now`; each newly spawned agent
process still increments the invocation cap. Authentication/account
failure is separate and may require the operator; no API-billing fallback is allowed.

The Human Gate Brief includes the finding history, attempts, why each failed, the latest proposed
technical solution, evidence, the exact decision or authorization required, and what answering
does not authorize.

## 13. Convergence predicate

The lane is `CLEAN` only when all of the following hold on the exact current revision:

- no P0-P2 finding remains open, still present, reopened, or awaiting adjudication;
- every historical blocking finding is `VERIFIED_RESOLVED` or `CLOSED_NOT_A_DEFECT`;
- author dispositions exactly match the outstanding finding set;
- required tests and deterministic target verification satisfy the lane policy;
- regression tests fail on the pre-fix behavior and pass on the repaired behavior where the
  finding is testable;
- reviewer counterexample evidence is addressed by an adequate permanent repository test;
- no prohibited test manipulation or unexplained test-count reduction exists;
- scope, clean-tree, dependency, and structured-persistence safety checks pass;
- no regression blocker was introduced; and
- the reviewed commit and tree digests equal the revision eligible for landing.

For an authorship handoff chain, the original lane is terminal `HANDOFF_REQUIRED`, never
independently CLEAN or landable. Composite landing additionally requires: the dependent lane's
gating review is by Claude with `lens: gating`; all transferred findings are
`VERIFIED_RESOLVED`; both contiguous ranges have accepted machine-readable gates; no role swaps
back; and the final combined tree digest equals the candidate passed to the local land worker.

No process can guarantee the absence of every possible bug. This protocol guarantees the
enforceable standard: no known blocking defect remains, every identified defect has a verified
disposition, required contracts and tests pass, and the exact reviewed revision is the revision
eligible to land.
