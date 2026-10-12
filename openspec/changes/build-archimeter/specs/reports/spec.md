## ADDED Requirements

### Requirement: metrics.json
`metrics.json` SHALL carry a documented, versioned schema (`schema_version`) including target identity (repo+SHA or image digest), tool version, effective config, and all computed values, and MUST NOT contain tokens or environment values.

#### Scenario: Secret scan
- WHEN a run uses a token and an image with env values
- THEN a grep of the output directory finds neither

### Requirement: report.html
`report.html` SHALL be one self-contained file (inline JS/CSS; libraries only from cdnjs or jsdelivr) with: summary panel (propagation cost by kind, cycle count, hidden-coupling count, top 10 hotspots); DSM heatmap switchable by kind and level; force-directed graph with edge color by kind, node size by LOC, node color by instability and service→package→module drill-down; Martin A-vs-I scatter with main sequence; churn-vs-complexity scatter; sortable event inventory.

#### Scenario: Offline structure
- WHEN the report is opened
- THEN it loads no resources other than cdnjs/jsdelivr libraries

### Requirement: Generated-app sections
For generated applications the report SHALL add a provenance breakdown, the service graph and the dangling-event list.

#### Scenario: Source project
- WHEN the target is a source project
- THEN those sections are absent
