## ADDED Requirements

### Requirement: Churn
Churn per file and module SHALL be computed over a configurable window (default 180 days) from history alone.

#### Scenario: Window
- WHEN a file changed 3 times inside and 5 times outside the window
- THEN churn is 3

### Requirement: Change coupling
Module pairs that co-change SHALL be reported with support and confidence, ignoring commits touching more than a configurable number of files (default 30).

#### Scenario: Bulk commit
- WHEN a commit touches 40 files
- THEN it contributes to no coupling

### Requirement: Hotspots
Files SHALL be ranked by churn × complexity.

#### Scenario: Ranking
- WHEN file X has churn 10, complexity 20 and Y has 2, 50
- THEN X ranks above Y

### Requirement: Hidden coupling
Module pairs with change coupling above thresholds but no import or event dependency SHALL be flagged and featured prominently.

#### Scenario: Fixture
- WHEN modules `p` and `q` co-change in 80% of commits and share no import or event
- THEN the pair appears in hidden coupling

### Requirement: Ownership
Primary authors per module SHALL be reported, flagging modules where one author exceeds a configurable share of changes. Author identifiers SHALL be hashed or emails omitted in reports unless configured.

#### Scenario: Single owner
- WHEN one author made 90% of a module's changes
- THEN the module is flagged

### Requirement: Not applicable without history
For targets with no git history of their own, change metrics SHALL be omitted, except for framework modules when `--framework` is given.

#### Scenario: Image target
- WHEN the target is an image without `--framework`
- THEN no change metrics are emitted and the report says why
