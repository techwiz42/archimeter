## 0. Gate

- [x] 0.1 User confirms design.md; no code before this (open questions resolved 2026-10-12)

## 1. Skeleton

- [x] 1.1 `pyproject.toml` (Python ≥3.11, console script, deps), src layout, `pipx install .` works
- [x] 1.2 Typer CLI with all commands wired to "not yet implemented" errors that exit non-zero (no silent stubs)
- [x] 1.3 Config loader with strict unknown-key errors and defaults; secrets denylist
- [x] 1.4 Safe file walker (denylist, size caps, binary/vendored skip)

## 2. Graph and algorithms

- [x] 2.1 Node/Edge model, hierarchy, rollup
- [x] 2.2 `GraphAlgo` protocol + networkx implementation; sparse propagation cost
- [x] 2.3 Extractor protocol, registry, entry points, dependency ordering
- [x] 2.4 Module boundary detection and config overrides

## 3. Source extractors and metrics

- [x] 3.1 Python import extractor
- [x] 3.2 JS/TS tree-sitter extractor with tsconfig aliases and workspaces
- [x] 3.3 Code metrics (radon, lizard, complexipy, logical LOC)
- [x] 3.4 Module metrics (fan-in/out, Ca/Ce/I/A/D) and abstractness per language
- [x] 3.5 DSM with clustering order, cycles, event-independent system metrics

## 4. Events

- [x] 4.1 Preset schema and six presets
- [x] 4.2 Custom pattern support
- [x] 4.3 Name resolver (literal, constants, imported constants, enums) + unresolved
- [x] 4.4 Event inventory and flags; cross-service matching

## 5. Git history

- [x] 5.1 Streaming log parser, churn, window
- [x] 5.2 Change coupling with large-commit cutoff
- [x] 5.3 Hotspots, hidden coupling, ownership (hashed identities)

## 6. Fixtures and tests for source mode

- [x] 6.1 Python fixture repo with known imports, events, cycle, hidden coupling, expected metrics
- [x] 6.2 TypeScript fixture repo, same properties
- [x] 6.3 pytest asserting exact expected values

## 7. Auth and repo access

- [x] 7.1 Credential source chain; `auth status/logout`
- [x] 7.2 Device flow and `--with-token`
- [x] 7.3 Keyring storage; Redactor on logs and exceptions
- [x] 7.4 Blobless cached clone with extraHeader, hooks off, submodules opt-in
- [x] 7.5 Access error classification (404 / no access / SSO)
- [x] 7.6 Tests with mocked GitHub APIs incl. redaction

## 8. Images and generated apps

- [x] 8.1 OCI registry client, auth challenge, Docker config/credential helpers, digest cache
- [x] 8.2 Tarball readers (docker save, OCI layout)
- [x] 8.3 Hardened layer unpacker with whiteouts and caps
- [x] 8.4 Image config reader (env names only)
- [x] 8.5 App/third-party separation, dependency inventory, uncertainty report
- [x] 8.6 Compose/Kubernetes/Dockerfile service extractor; shared-infra coupling; service metrics
- [x] 8.7 Malicious archive fixtures (traversal, symlink escape, oversized) and tests
- [x] 8.8 Two-container fixture: tarballs + compose, known cross-service event, shared DB, dangling event

## 9. Provenance

- [x] 9.1 Manifest format doc + reader
- [x] 9.2 Framework index; hash and AST-similarity classifier
- [x] 9.3 Dangling events, dependency closure, complexity attribution, framework change metrics
- [x] 9.4 Fixture framework + copied/modified/new tests

## 10. Outputs

- [x] 10.1 `metrics.json` schema (versioned) + schema doc + secret-scan test
- [x] 10.2 report.html: summary, DSM, force graph with drill-down, A-vs-I, hotspots, event table
- [x] 10.3 Generated-app report sections
- [x] 10.4 `compare` engine + `compare.html`
- [x] 10.5 `trend` with temp worktrees, SHA cache, charts

## 11. Init, docs, performance

- [x] 11.1 `init` detection and commented starter config
- [x] 11.2 README (install, auth, usage, config, security model, manifest format, metric guide)
- [x] 11.3 100k-LOC benchmark under 2 minutes

## 12. Done-criteria runs

- [x] 12.1 Full test suite green
- [x] 12.2 `analyze` on a public GitHub repo, no config
- [x] 12.3 Analyze a private GitHub repository via the `gh` token (done live); `auth login --with-token` and the device flow are verified by mocked-API tests only
- [x] 12.4 `analyze` on a registry image and on a saved tarball
- [x] 12.5 Run on a composed application with a custom event-bus config; summarize top propagation modules, cycles, hidden couplings
- [x] 12.6 `compare` two generated applications with `--framework`; summarize top modules, glue share, dangling events, shared-DB couplings

## 13. Publication

- [x] 13.1 Verify no private data in the repository (fixtures synthetic, results anonymized); LICENSE added
