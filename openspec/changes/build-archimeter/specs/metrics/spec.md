## ADDED Requirements

### Requirement: Function and file metrics
Cyclomatic complexity and maintainability index (radon for Python; lizard for JS/TS and fallback), cognitive complexity for Python (complexipy), and logical LOC excluding blanks and comments SHALL be computed.

#### Scenario: Known function
- WHEN a fixture function has a known branch count
- THEN reported cyclomatic complexity equals the expected value

### Requirement: Module metrics
The tool SHALL compute per module: fan-in/fan-out per kind and combined; Ca, Ce, I = Ce/(Ca+Ce), abstractness A (abstract classes, Protocols, interfaces over total types), D = |A+I−1|; total LOC, mean and max complexity, count over threshold (default 10).

#### Scenario: Isolated module
- WHEN Ca+Ce is 0
- THEN I is reported as null, not 0 or 1

### Requirement: DSM
A DSM SHALL be produced for each dependency kind and combined, at module and service level, ordered by clustering.

#### Scenario: Ordering
- WHEN two clusters exist with few cross edges
- THEN rows of each cluster are contiguous

### Requirement: Propagation cost
Propagation cost (density of the transitive closure, per MacCormack et al.) SHALL be computed per kind and combined using sparse boolean matrices.

#### Scenario: Chain
- WHEN modules form a chain A→B→C
- THEN visibility counts include the diagonal and density is 6/9

### Requirement: Cycles
Strongly connected components with more than one module or service SHALL be reported.

#### Scenario: Self-import only
- WHEN a module imports itself
- THEN no cycle is reported

### Requirement: Event inventory
Every event SHALL be listed with publishers and subscribers, flagging no-subscriber events, never-published subscriptions, and unresolved names.

#### Scenario: Flags
- WHEN an event has a publisher only
- THEN it is flagged `no_subscriber`

### Requirement: Performance
A 100k-line project SHALL analyze in under 2 minutes excluding trend mode, clone and image pull.

#### Scenario: Benchmark
- WHEN a generated 100k LOC project is analyzed
- THEN wall time is under 120 s on the reference machine
