## ADDED Requirements

### Requirement: Commands
The CLI SHALL provide `analyze`, `compare`, `trend`, `auth login|status|logout` and `init` with the argument forms in the brief.

#### Scenario: Help
- WHEN `archimeter --help` runs
- THEN all commands are listed

### Requirement: Configuration
`archimeter.toml` (target root or `--config`) SHALL support module roots/grouping/exclusions, event presets and patterns, image app-code locations, provenance settings, thresholds, git window and large-commit cutoff, and secrets denylist additions. Unknown keys SHALL produce an error naming the key.

#### Scenario: Typo
- WHEN config contains `git_windw`
- THEN the tool fails naming the key

### Requirement: Works without config
The tool SHALL run with no config using defaults.

#### Scenario: Bare run
- WHEN no config exists
- THEN analysis completes with defaults recorded in metrics.json

### Requirement: init
`init <target>` SHALL write a commented starter config from detected languages, modules, event mechanisms and (for images) app paths, and MUST NOT overwrite an existing file without confirmation.

#### Scenario: Existing file
- WHEN `archimeter.toml` exists
- THEN init asks or requires `--force`

### Requirement: Project-agnostic
Nothing specific to any one analyzed project or generator SHALL be hardcoded; such needs live in per-project config.

#### Scenario: Grep
- WHEN the source is searched for the names of the projects it was developed against
- THEN no behavior depends on them
