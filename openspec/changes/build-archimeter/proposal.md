## Why

Structural complexity in event-driven, generated systems is mostly invisible to import-only tools: events couple modules without imports, files co-change without any code link, and generated applications hide how much complexity came from framework modules versus generated glue. We need a standalone, read-only tool that measures all three dependency kinds (static, event, change) plus service dependencies, for source projects and for containerized generated applications. Real test targets: a composed source project and applications produced by a module-composing code generator. 
## What Changes

- New standalone Python 3.11+ CLI `archimeter` (own repo, `pipx`-installable, `typer`): `analyze`, `compare`, `trend`, `auth login|status|logout`, `init`.
- Target resolver for local paths, GitHub repos (public/private, `@ref`), registry images, image tarballs (`docker save`/OCI) and generated build-output directories.
- GitHub auth: env token → keyring token → `gh auth token` → interactive OAuth device flow; `--with-token` for fine-grained PATs.
- Daemonless, runtime-free image pull and safe layer unpacking; application code separated from base-image and third-party code.
- Pluggable extractors (Python imports, JS/TS imports, events, git change history, service graph, provenance) emitting into a hierarchical, typed, weighted `networkx` graph model behind an algorithm interface.
- Metrics: per-function/file, per-module (fan-in/out, Martin's Ca/Ce/I/A/D), whole-system (DSM, propagation cost, cycles, event inventory), change history (churn, coupling, hotspots, hidden coupling, ownership).
- Generated-app mode: provenance attribution, dangling events, dependency closure, complexity attribution.
- Outputs: versioned `metrics.json`, self-contained `report.html`, `compare.html`, trend charts.
- Static-only security model: never execute, build, install or run anything from the target; hardened archive handling; secrets never read or emitted.
- README, fixtures-based pytest suite, performance target (100k LOC < 2 min).

## Capabilities

### New Capabilities
- `target-resolution`: resolving path/GitHub/image/tarball/build-output targets to a local file tree plus optional git history; blobless cached clones.
- `github-auth`: credential precedence, device flow, PAT stdin, keyring storage, least privilege, token redaction, actionable access errors.
- `container-images`: registry pull without a runtime, credential reuse, digest cache, layer flattening with whiteouts, image-config reading, app/third-party separation.
- `untrusted-safety`: static-only guarantees, secrets denylist, env-value redaction, archive hardening, size caps.
- `graph-and-extractors`: extractor plugin interface, hierarchical typed graph model, algorithm interface, module-boundary detection.
- `static-dependencies`: Python (`ast`) and JS/TS (tree-sitter, tsconfig aliases) import extraction.
- `event-dependencies`: preset and custom event patterns, name resolution, unresolved reporting, cross-container matching.
- `change-history`: churn, change coupling, hotspots, hidden coupling, ownership.
- `service-dependencies`: compose/Kubernetes/image-config service graph and shared-infrastructure coupling.
- `provenance`: generator manifest, framework matching, per-configuration metrics for generated apps.
- `metrics`: code, module and system metrics and DSM/propagation cost/cycles/event inventory.
- `reports`: `metrics.json` schema and `report.html`.
- `compare-and-trend`: `compare` marginal complexity and `trend` snapshots.
- `configuration-cli`: `archimeter.toml`, `init`, CLI surface, no-config defaults.

### Modified Capabilities
<!-- none: greenfield repository -->

## Impact

- New repository; no changes to any analyzed project (read-only targets).
- Dependencies (proposed): typer, networkx, scipy (sparse), radon, lizard, complexipy, tree-sitter + grammars, keyring, httpx, tomli-w, jinja2 (report templating), pytest.
- External: GitHub OAuth App (client ID supplied via config), GHCR/registries, optional `gh`.
- Code generators may optionally emit the documented provenance manifest (a separate change in each generator, not part of this one).
