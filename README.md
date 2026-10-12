# archimeter

Measure the structural complexity of **any software project**, and of the **applications a code generator produces**, and draw it.

Most tools only see imports. archimeter measures four kinds of dependency:

| Kind | What it is | Why it matters |
|---|---|---|
| **static** | module A imports module B | the visible structure |
| **event** | A publishes an event that B subscribes to | invisible to imports; a major source of hidden complexity in event-driven systems |
| **change** | A and B change together in git commits | coupling nobody wrote down |
| **service** | container A depends on container B (compose / Kubernetes / shared infrastructure) | coupling between deployables |

archimeter is **read-only static analysis**. It never imports, builds, installs or runs the target, and never runs a container.

```
archimeter analyze <target> [--config archimeter.toml] [--out ./report] [--framework <source target>]
archimeter compare <target> <target> [<target> ...] [--framework <source target>] [--out ./report]
archimeter trend   <target> --since 2026-01-01 --step weekly|monthly
archimeter auth login | status | logout
archimeter init <target>      # writes a starter archimeter.toml from what it detects
```

## Install

Python 3.11+.

```
pipx install .            # from a checkout
# or: pip install .
```

No Docker, no container runtime, and no `crane`/`skopeo` are needed. `git` must be on `PATH` for GitHub targets and change history.

## Targets

`<target>` is one of:

| Form | Example |
|---|---|
| local path | `.`, `/path/to/project` |
| GitHub repository | `owner/repo`, `https://github.com/owner/repo`, either with `@branch`, `@tag` or `@commit` |
| registry image | `docker://registry/name:tag`, `docker://registry/name@sha256:...` |
| saved image | `oci:/path/to/image.tar` (`docker save` tarball, OCI image-layout tarball, or OCI layout directory) |
| generated build output | a directory containing `docker-compose.yml`, Kubernetes manifests or Dockerfiles; saved image tarballs next to the compose file are unpacked for services whose `image:` matches a tarball's tag |

## Authentication

Public repositories and public images need no authentication. For private GitHub repositories archimeter finds a credential in this order:

1. **Environment**: `ARCHIMETER_GITHUB_TOKEN` or `GITHUB_TOKEN` (for CI).
2. **Stored token** from a previous `archimeter auth login` (OS keychain).
3. **GitHub CLI**: if `gh` is installed and authenticated, `gh auth token` is used. Nothing is copied into archimeter's own storage.
4. Otherwise, start an interactive login.

```
archimeter auth login                  # OAuth device flow: prints a URL and a one-time code, polls until approved
archimeter auth login --with-token     # reads a fine-grained personal access token from stdin
archimeter auth status                 # active credential source and user; never prints the token
archimeter auth logout                 # removes the stored token
```

The device flow works over SSH and on machines with no browser: open the printed URL anywhere and enter the code.

**OAuth App.** The device flow needs an OAuth App client ID (a public identifier, not a secret). Register one at <https://github.com/settings/developers>, enable **Device Flow**, and give archimeter the ID in `archimeter.toml` (`[github] oauth_client_id`), the `ARCHIMETER_OAUTH_CLIENT_ID` environment variable, or `--client-id`. It is not hardcoded, so you can use your own.

**Permissions requested (least privilege).**

| Credential | Permission | Notes |
|---|---|---|
| Fine-grained PAT (**recommended**) | Repository **Contents: Read** and **Metadata: Read** on the repositories you analyze | true read-only access |
| Classic OAuth token (device flow) | scope `repo` | GitHub offers no narrower classic/OAuth scope that can read private repositories. It also grants write access, which archimeter never uses. Prefer a fine-grained PAT. Public repositories need no scope. |
| ghcr.io images | additionally `read:packages` (classic token) | requested only when a `ghcr.io` target is used. Fine-grained PATs cannot read ghcr.io packages. |

**Errors tell you what is wrong.** Repository not found because the owner does not exist, the token lacks access to this repository (with the token's scopes), and an organization requiring **SSO authorization** (with the URL to authorize) are reported as three different messages.

**Token handling.** Tokens live in the OS keychain through `keyring`, never in config files, `metrics.json`, the HTML report, logs, or the cloned repository's `.git/config`. On a machine without a keychain `auth login` fails with instructions rather than writing a plaintext file; use the environment variable instead. Git receives the credential only through environment-scoped config (`GIT_CONFIG_COUNT`), scoped to `https://github.com/`, for the one command; it is not in `argv`, not in `.git/config`. Tokens are redacted from every log record and error message.

### Container registries

Images are pulled with a built-in OCI/Docker registry client: no daemon, no runtime. Private registries reuse your existing Docker credentials (`config.json` `auths`, `credsStore` and `credHelpers`). Pulled layers are cached by digest in `~/.cache/archimeter/images/` and re-verified on every use.

### Cloning

Repositories are cloned into `~/.cache/archimeter/<owner>/<repo>` as **blobless partial clones** (`--filter=blob:none`): change-coupling analysis needs only commit and file names, so large histories are cheap. Only the analyzed ref is checked out. Later runs `fetch`. Hooks are disabled and submodules are not fetched unless you pass `--submodules`. Set `ARCHIMETER_CACHE_DIR` to move the cache.

## Quick start

```
archimeter analyze .                                    # a local project, no config needed
archimeter analyze owner/repo                           # a public GitHub repo
archimeter analyze docker://python:3.12-slim --out r    # an image from a registry
archimeter analyze oci:./app.tar                        # a saved image
archimeter analyze ./build-output --framework ./framework-src
archimeter compare ./app-a ./app-b --framework ./framework-src
archimeter trend . --since 2026-01-01 --step monthly
```

`analyze` writes `metrics.json` and `report.html` into `--out` (default `./report`). `compare` writes `compare.json`, `compare.html` and one subdirectory per application. `trend` writes `trend.json` and `trend.html`.

## Configuration

`archimeter.toml` is found in the target's root or passed with `--config`. **No config is required**; `archimeter init <target>` writes a commented starter file from what it detects. Unknown keys are errors naming the key.

```toml
[modules]
roots   = []                  # directories whose children are modules, e.g. ["packages"]
group   = {}                  # { core = ["src/core/**", "src/shared/**"] }
exclude = ["tests/**", "**/migrations/**"]   # tests, migrations, vendored or demo data (defaults shown in `init`)

[analysis]
complexity_threshold = 10     # "functions over threshold"
skip_generated = true         # off automatically for generated applications
max_file_bytes = 1000000

[events]
presets = ["redis"]           # redis, celery, django-signals, blinker, kafka, node-eventemitter

[[events.patterns]]           # your own bus
role = "publish"              # or "subscribe"
call = "bus.publish"          # dotted suffix of the callee (or decorator)
arg = 0                       # positional index of the event name; arg = -1 takes every positional argument
kwarg = "event_type"          # keyword holding the name; takes precedence over `arg`
# kind = "decorator"          # match decorators instead of calls
# name_from = "function"      # decorator: the decorated function's name is the event; "receiver": the object the method is called on
# event = "entity.created"    # the call itself denotes one fixed event

[git]
window_days = 180             # churn window, counted back from the newest commit
max_commit_files = 30         # larger commits are ignored for change coupling
min_support = 3               # shared commits for "hidden coupling"
min_confidence = 0.5
owner_share = 0.7             # one author above this share flags a module
include_author_names = false  # authors are hashed unless true

[image]
app_paths = []                # application code inside images when WorkingDir/entrypoint are unclear, e.g. ["/srv/app"]
platform = "linux/amd64"

[provenance]
manifest = ""                 # generator manifest (see below)
framework = ""                # framework source tree; `--framework` overrides
similarity_threshold = 0.6    # AST similarity for "modified from"
selected_modules = []         # modules the customer selected, if the manifest does not say

[security]
secrets_denylist = []         # extra file patterns that must never be read

[github]
oauth_client_id = ""
```

## Event dependencies

Event coupling looks different in every code base, so extraction is pattern-based.

* **Presets** (`[events] presets`): `redis` (`publish`, `xadd`, `subscribe`, `psubscribe`, `xread`, `xreadgroup`), `celery` (task definitions and `.delay`/`.apply_async`/`send_task`), `django-signals`, `blinker`, `kafka` (producer `send`/`produce`, consumer `subscribe`, `KafkaConsumer(...)`, kafkajs), `node-eventemitter`. Presets that guess on generic method names (`send`, `on`, `emit`) only fire on receivers they can prove are signals or emitters (declared in the project), or in files that mention the library.
* **Custom patterns** as above.
* **Names** are resolved from string literals, module-level constants (same file, or a project-unique definition), and enum or class-attribute members (an enum member resolves to its *value*, so `EventType.ORDER_CREATED` and `"order.created"` are the same event). Anything built at runtime (f-strings, loop variables, parameters) is reported as **unresolved** with the reason, and is **never guessed**.
* A publisher in one service and a subscriber in another becomes a service-level edge.

The inventory lists every event with publishers and subscribers and flags **no subscriber**, **never published** and unresolved sites. `init` prints the call names in your code that look like event APIs, as candidates for patterns.

## Generated applications

The aim is to measure what a framework *produces*, not just the framework.

* **Images** are unpacked as filesystem layers and never run. Whiteouts are applied. The image config gives entrypoint, command, working directory, ports and environment variable **names** (values are discarded). The entrypoint and working directory locate the application code; the base image's OS files and installed third-party packages (`site-packages`, `node_modules`) are **not analyzed** but recorded as a dependency inventory from `*.dist-info`, `package.json` and lockfiles. Where the separation is uncertain the report says so and why (`uncertainty` in `metrics.json`, the Notes section of the report). Generated code is the target here, so "skip generated files" is off.
* **Multi-container applications** (compose files, Kubernetes manifests, several images) become a **service graph** with edges from `depends_on`, `links`, environment variables that name another service, shared volumes, and shared channels. Services sharing a database are flagged prominently as **shared-database coupling**; shared Redis or brokers are reported too.
* **Provenance** attributes every generated file to a source, in this order: (1) a provenance manifest; (2) with `--framework`, a match against the framework's source by content hash and AST similarity: **copied** (identical), **modified** (similar above the threshold, with the similarity), or **new** (generated glue); (3) files that cannot be attributed (for example equally similar to files in two framework modules) are reported as **unattributed**.
* **Per-configuration metrics**: dangling events *in this assembly*, dependency closure (selected versus pulled in transitively), and complexity attribution by provenance (framework module, modified module, generated glue), including each group's share of the transitive-closure reach.
* **Change metrics** do not exist for an application without history of its own. With `--framework`, archimeter reports churn, ownership and hidden coupling of the framework modules the application includes, from the framework's history.
* `archimeter compare` reports headline metrics side by side, the **marginal complexity** of each module (see below), modules in a dependency cycle or with dangling events in every application that includes them, and the most and least complex configuration.

### Provenance manifest format

A generator can describe where each generated file came from. archimeter reads `archimeter-provenance.json` (or `provenance.json`, `.archimeter/provenance.json`, or the path in `[provenance] manifest`) at the root of the generated output:

```json
{
  "format": "archimeter-provenance/1",
  "generator": { "name": "my-framework", "version": "1.4.0" },
  "selected_modules": ["billing", "crm"],
  "files": {
    "backend/main.py":          { "module": "core",    "template": "main.py.j2", "version": "1.4.0", "kind": "rendered"  },
    "modules/billing/api.py":   { "module": "billing", "source": "modules/billing/api.py", "version": "2.0.1", "kind": "copied" },
    "backend/wiring.py":        { "module": null,      "template": "wiring.py.j2", "kind": "generated" }
  }
}
```

`kind` is `copied` (taken from a framework module unchanged), `rendered` (produced from a template or modified: counted as *modified*) or `generated` (new glue). Paths are relative to the output root. Files not listed fall through to `--framework` matching.

## Metrics, and how to read them

**Per function and file.** *Cyclomatic complexity* and *maintainability index* (`radon` for Python; `lizard` for JS/TS and as the fallback for other languages). *Cognitive complexity* for Python via `complexipy` (a fast implementation of SonarSource's cognitive complexity, which penalizes nesting the way a reader feels it). *Logical lines of code*: statements, excluding blanks, comments and docstrings. The maintainability index has a 2-second budget per file; a pathological file (for example a huge generated literal) reports no index and a note instead of stalling the run.

**Per module.** *Fan-in / fan-out* per dependency kind and combined (distinct neighbours). Robert Martin's metrics: *afferent coupling* **Ca** (modules depending on this one), *efferent coupling* **Ce** (modules it depends on), *instability* **I = Ce / (Ca + Ce)** (0 = everyone depends on it, 1 = it depends on everything; undefined for isolated modules), *abstractness* **A** (abstract classes, Protocols and interfaces over all classes; undefined without classes), and *distance from the main sequence* **D = |A + I − 1|**. A stable module should be abstract, an unstable one may be concrete; high D marks rigid-and-concrete or abstract-but-unused modules. Aggregates: total LOC, mean and max complexity, functions over the threshold.

**Whole system.**
* **Design structure matrix (DSM)**: who depends on whom, per kind and combined, at module and service level, ordered by clustering so tightly connected groups appear as blocks on the diagonal.
* **Propagation cost** (MacCormack, Rusnak and Baldwin): the density of the transitive closure of the dependency matrix: the share of module pairs where a change *could* propagate. 5% is a well-contained system; 50% or more means almost anything can reach almost anything. "Combined" uses the directed kinds (import, event, service); change coupling is symmetric and reported separately. Sparse boolean matrices keep it fast on thousands of nodes.
* **Dependency cycles**: strongly connected components with more than one module or service, plus the **direct two-way dependencies** inside them, which are the first edges to untangle.
* **Event inventory** with the flags above.
* **Marginal complexity** (counterfactual): for each module, the share of the system's dependency matrix that is reachable *only because that module is present*, its coupling (edges) and LOC, averaged over the compared applications. `comparison_estimate` additionally gives difference-of-means across applications, and is labelled low-confidence unless applications differ only by that module.

**Change history (source projects).** *Churn* per file and module over the window. *Change coupling*: module pairs that co-change with **support** (shared commits) and **confidence** (P(B changes | A changes)), ignoring commits that touch more than `max_commit_files` files. *Hotspots*: files ranked by churn × complexity: complex code that keeps changing. *Hidden coupling*: strong change coupling with **no** import or event dependency, usually the most important finding. *Ownership*: primary authors per module and modules where one author made most changes (author identities are hashed by default).

## Security model

archimeter is built to be pointed at code you did not write.

* **Static analysis only.** Source is parsed with `ast` or tree-sitter. The target and its dependencies are never imported, executed, built or installed: no `setup.py`, no `npm install`, no tests. **Containers are never run**; images are read as files. Code generators are never run.
* **Secrets are never read.** `.env*`, key and certificate files, credential files, database files, and anything in the denylist (extendable with `[security] secrets_denylist`) are not opened, not extracted from images, and not listed by content. Environment values in images, compose files and manifests are used at most transiently to match a service name and are never stored: only variable *names* appear in outputs.
* **Hardened unpacking.** Image layers are streamed, never `extractall`ed. Absolute paths, `..` traversal and NUL bytes are rejected. Symlinks are never created on disk, and any later member that would be written *through* a recorded symlink aborts the unpack. Device nodes, FIFOs and sockets are skipped. Hardlinks are honoured only inside the root. Total uncompressed bytes (including skipped members) and file counts are capped, which defeats decompression bombs. Layer and blob digests are verified.
* **Git** runs with hooks disabled, no user or system config, `core.fsmonitor` off, no recursion into submodules unless asked, and `file`/`ext` protocols denied for remotes.
* **Caps.** File-size caps apply; binaries and vendored code are skipped by default.
* **Tokens**: see Authentication. Permissions requested are listed there.
* Trend mode works in a private clone and temporary worktrees; your working tree and `.git` are never touched.

## Reports

`report.html` is one self-contained file (inline JS and CSS; D3 from cdnjs) with: a summary (propagation cost per kind, cycles, hidden couplings, top-10 hotspots); a **DSM heatmap** switchable by kind and level; an interactive **force-directed dependency graph** (edges coloured by kind, node size = LOC, node colour = instability, drill-down service → package → module); the **A-vs-I scatter** with the main sequence; a **churn-vs-complexity scatter**; a sortable **event inventory**; and, for generated applications, the **provenance breakdown**, service graph, shared-infrastructure findings and dangling events. Charts have table alternatives, a legend, and light and dark themes.

`metrics.json` carries `schema_version` (currently `1.0`), the target (repository and commit SHA, or image digest), the tool version, the config used, and every computed value. It never contains tokens or environment values.

## Performance

A 100,000-line project (400 files, 10,000 functions) analyzes in about 5 seconds on 8 cores (excluding clone and image pull); a 240,000-line, 1,500-file application with events and service graph takes under a minute. Per-file analysis runs in worker processes.

## Development

```
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

Tests use small synthetic fixtures with known expected values: a Python repo and a TypeScript repo (known imports, events, a cycle and a hidden coupling), a two-container generated application as image tarballs plus a compose file (cross-service event, shared database, dangling events, copied/modified/new files), hostile archives (traversal, escaping symlinks, oversized layers), and mocked GitHub and registry APIs for the authentication flows.

Adding an extractor means adding one file to `archimeter/extractors/` (or an `archimeter.extractors` entry point) that registers a class with `name`, `requires`, `applies(ctx)` and `extract(ctx)`; it emits typed, weighted edges and node attributes into the graph.

## License

MIT
