## ADDED Requirements

### Requirement: Extractor plugin interface
Extractors SHALL implement one interface (declares name, applicability to a target, and `extract(ctx, graph)` emitting typed weighted edges and node attributes), discovered via entry points and a built-in registry, so adding a language or dependency source is a single new file.

#### Scenario: New extractor
- WHEN a file defining one extractor class is added to the extractors package
- THEN it runs on the next analysis with no other code changes

### Requirement: Hierarchical typed graph
The graph SHALL have nodes at function, file, module, package and service levels, edges typed `import`, `event`, `change` or `service`, with weights and optional evidence, and per-node provenance when known. Metrics SHALL roll up at each level.

#### Scenario: Rollup
- WHEN file A imports file B in another module
- THEN a module-level import edge exists with weight 1 per file edge

### Requirement: Swappable algorithms
Graph algorithms (SCC, transitive closure, centrality) SHALL sit behind an interface with a `networkx` implementation; metrics code MUST NOT call `networkx` directly.

#### Scenario: Alternate backend
- WHEN a test backend implements the interface
- THEN metrics run unchanged

### Requirement: Module boundary detection
Boundaries SHALL be auto-detected (Python packages, top-level `src/` directories, JS/TS package boundaries in monorepos) and config SHALL override, group or exclude them.

#### Scenario: Config grouping
- WHEN config groups `a/*` into module `core`
- THEN all nodes under `a/` roll up into `core`
