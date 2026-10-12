## ADDED Requirements

### Requirement: Runtime-free pull
Images SHALL be pulled with a pure-Python OCI registry client (no Docker daemon or container runtime), supporting public anonymous pulls, multi-arch index selection, and private registries via Docker `config.json` and credential helpers; `ghcr.io` SHALL use the GitHub token.

#### Scenario: Public image
- WHEN `docker://registry/name:tag` is public
- THEN it is pulled with no credentials

#### Scenario: Private registry
- WHEN `config.json` holds credentials for the registry
- THEN they are used and never logged

### Requirement: Digest cache
Pulled layers and manifests SHALL be cached by digest under `~/.cache/archimeter/images/`, verifying digests on read.

#### Scenario: Corrupt cache entry
- WHEN a cached blob's digest mismatches
- THEN it is discarded and re-pulled

### Requirement: Safe flattening
Layers SHALL be flattened into a temporary directory applying whiteouts and opaque whiteouts, so deleted files stay deleted; a container MUST NOT be run.

#### Scenario: Whiteout
- WHEN layer 2 whiteouts a file from layer 1
- THEN the file is absent from the flattened tree

### Requirement: Image config
The tool SHALL read entrypoint, cmd, working dir, exposed ports and environment variable names only (values discarded).

#### Scenario: Env values
- WHEN the image sets `DB_PASSWORD=x`
- THEN outputs contain `DB_PASSWORD` and not `x`

### Requirement: Application-code separation
The tool SHALL exclude OS/base-image files and third-party packages (`site-packages`, `node_modules`), identify third-party packages from `*.dist-info`, `package.json` and lockfiles into a dependency inventory, locate app code via entrypoint/workdir or configured paths, and report where separation is uncertain.

#### Scenario: Uncertain
- WHEN entrypoint and workdir do not identify app code and no config path is given
- THEN a heuristic is applied and the report lists the separation as uncertain with the evidence
