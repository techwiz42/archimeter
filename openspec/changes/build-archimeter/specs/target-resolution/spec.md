## ADDED Requirements

### Requirement: Target kinds
The resolver SHALL accept a local path, GitHub URL or `owner/repo` shorthand with optional `@ref`, `docker://` image reference, `oci:` tarball path, or a generated build-output directory, and SHALL produce a local file tree, target identity (commit SHA or image digest) and git history when available.

#### Scenario: Shorthand with ref
- WHEN the target is `owner/repo@v1.2`
- THEN the resolver clones `https://github.com/owner/repo`, checks out `v1.2`, and records the resolved commit SHA

#### Scenario: Unrecognized target
- WHEN the target matches no kind
- THEN the tool exits non-zero naming the accepted forms

### Requirement: Cached blobless clone
Remote repos SHALL be cloned with `--filter=blob:none` into `~/.cache/archimeter/<owner>/<repo>`, reused and updated by `fetch`, checking out only the analyzed ref, with hooks disabled and submodules not recursed unless opted in.

#### Scenario: Second run reuses cache
- WHEN the same repo is analyzed twice
- THEN the second run performs `fetch` and no fresh clone

#### Scenario: Hostile hooks
- WHEN the cloned repo contains hook scripts
- THEN none execute

### Requirement: Credentials not persisted by git
Credentials SHALL reach git only via a credential helper or an `http.extraHeader` set for the single command, and MUST NOT appear in `.git/config`.

#### Scenario: Private clone
- WHEN a private repo is cloned with a token
- THEN `.git/config` and the process environment of later commands contain no token

### Requirement: Build-output directory
A build-output directory SHALL be read in place as files, discovering compose files, Kubernetes manifests and Dockerfiles without executing them.

#### Scenario: Compose discovered
- WHEN a directory contains `docker-compose.yml`
- THEN the service extractor receives it
