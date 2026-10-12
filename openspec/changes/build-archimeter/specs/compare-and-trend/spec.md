## ADDED Requirements

### Requirement: Compare
`compare` SHALL take two or more generated applications and emit `compare.html` and JSON with side-by-side headline metrics, marginal complexity per module (counterfactual propagation-cost contribution, coupling, LOC, averaged across the applications that include it, plus a labelled-confidence difference-of-means estimate), modules consistently increasing cycles or dangling events, and the most and least complex configurations.

#### Scenario: Two apps
- WHEN app 2 = app 1 plus module M
- THEN M's marginal contribution equals the metric difference

#### Scenario: Insufficient data
- WHEN fewer configurations exist than needed for a stable estimate
- THEN estimates are labeled low-confidence

### Requirement: Trend
`trend` SHALL compute metrics at historical commits (`--since`, `--step weekly|monthly`) in temporary worktrees without touching any user working tree, cache each snapshot by commit SHA, and chart propagation cost, cycle count, hidden coupling and mean complexity.

#### Scenario: Cache hit
- WHEN a snapshot for a SHA exists
- THEN it is not recomputed

#### Scenario: User tree untouched
- WHEN trend runs on a local repo with uncommitted changes
- THEN those changes and the checked-out ref are unchanged
