## ADDED Requirements

### Requirement: Manifest format
A documented provenance manifest format (generated file → source module, template, version) SHALL be defined and read when present at the configured path.

#### Scenario: Manifest present
- WHEN a manifest maps `app/glue.py` to template `glue.j2` of module `core@1.2`
- THEN the node carries that provenance with source `manifest`

### Requirement: Framework matching
Without a manifest, given `--framework`, each generated file SHALL be classified `copied` (content hash equal), `modified` (AST similarity at or above a configurable threshold) or `new` (generated glue).

#### Scenario: Three classes
- WHEN fixtures contain one identical, one edited and one unrelated file
- THEN they classify as copied, modified, new respectively

### Requirement: Unattributable files
Files that cannot be attributed SHALL be reported as such.

#### Scenario: Ambiguous
- WHEN a file matches two framework files equally
- THEN it is listed as unattributed with both candidates

### Requirement: Per-configuration metrics
For a generated application the tool SHALL report dangling events (published without subscriber, subscribed without publisher, in that application), dependency closure (selected versus transitively pulled in), and complexity attribution by provenance (framework module, modified module, generated glue).

#### Scenario: Dangling
- WHEN module M publishes `x` and no included module subscribes
- THEN `x` is a dangling event for this application even if another configuration subscribes

### Requirement: Framework change metrics
When `--framework` is given, change metrics (churn, ownership and hidden coupling) SHALL be reported for the framework modules included in the application, from the framework's history.

#### Scenario: Included only
- WHEN the app includes modules A and B of framework modules A, B, C
- THEN change metrics cover A and B only
