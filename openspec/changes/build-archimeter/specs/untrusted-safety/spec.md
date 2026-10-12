## ADDED Requirements

### Requirement: Static analysis only
The tool SHALL parse source only with `ast` or tree-sitter and MUST NOT import, execute, build, install or test the target, run containers, or run code generators.

#### Scenario: Setup script present
- WHEN the target has a `setup.py` that writes a marker file
- THEN the marker is never created

### Requirement: Archive hardening
Archive extraction SHALL reject absolute paths and `..` traversal, SHALL NOT follow or create symlinks escaping the extraction root, SHALL NOT create device nodes, FIFOs or sockets (they are skipped and counted), and SHALL cap total extracted size.

#### Scenario: Traversal layer
- WHEN a layer contains `../../etc/x`
- THEN extraction aborts with an error and writes nothing outside the root

#### Scenario: Escaping symlink
- WHEN a layer symlinks `link -> /etc` then writes `link/passwd`
- THEN the write is rejected

#### Scenario: Oversized layer
- WHEN extracted bytes exceed the cap
- THEN extraction aborts

### Requirement: Secrets and environment values
Files matching the secrets denylist (`.env*`, key files, credential files, database files, configurable additions) SHALL NOT be read, and environment values from images, compose files and manifests SHALL be discarded, recording names only.

#### Scenario: .env present
- WHEN a project contains `.env`
- THEN it is not opened and no value appears in any output

### Requirement: Resource caps
File size caps SHALL apply; binaries and vendored code SHALL be skipped by default.

#### Scenario: Huge file
- WHEN a source file exceeds the cap
- THEN it is skipped and listed in `skipped` in metrics.json
