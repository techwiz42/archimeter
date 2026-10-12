## ADDED Requirements

### Requirement: Service graph
From compose files, Kubernetes manifests and image configs, containers SHALL become service nodes with edges for `depends_on`, links/networks, shared volumes, and shared message channels.

#### Scenario: depends_on
- WHEN service `web` depends_on `db`
- THEN edge `web -> db` of kind service exists

### Requirement: Shared infrastructure coupling
Services using the same database, Redis channel/stream, queue or topic SHALL be linked as indirectly coupled, and shared-database coupling SHALL be flagged prominently.

#### Scenario: Shared DB
- WHEN two services reference the same database host/name via env var names and compose wiring
- THEN a shared-database finding is emitted listing both services

### Requirement: Service-level metrics
Standard metrics (DSM, propagation cost, cycles, fan-in/out) SHALL be computed at service level and within each container.

#### Scenario: Cycle
- WHEN service A depends on B and B on A
- THEN a service cycle is reported
