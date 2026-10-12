## ADDED Requirements

### Requirement: Credential precedence
The tool SHALL obtain a GitHub token in this order: `GITHUB_TOKEN`/`ARCHIMETER_GITHUB_TOKEN`; token stored by `auth login`; `gh auth token` if `gh` is installed and authenticated; otherwise start an interactive login. Public repos SHALL clone without any credential.

#### Scenario: Env wins
- WHEN `GITHUB_TOKEN` is set and a stored token exists
- THEN the env token is used and `auth status` reports source `environment`

#### Scenario: gh fallback
- WHEN no env or stored token exists and `gh` is authenticated
- THEN the `gh` token is used and not copied into storage

### Requirement: Device flow login
`auth login` SHALL use GitHub's OAuth device flow: print verification URL and user code, poll respecting `interval`/`slow_down`, handle expiry and denial, then store the token. `--with-token` SHALL read a fine-grained PAT from stdin.

#### Scenario: Approved
- WHEN the user approves in a browser on another machine
- THEN polling succeeds and the token is stored in the OS keychain

#### Scenario: Expired code
- WHEN the device code expires
- THEN login fails with an actionable message and stores nothing

### Requirement: Configurable OAuth client
The OAuth App client ID SHALL come from config or environment and MUST NOT be hardcoded.

#### Scenario: Missing client ID
- WHEN device flow starts without a client ID
- THEN the tool explains how to register an OAuth App and set it

### Requirement: Least privilege
Requested scopes SHALL be the narrowest that can read the target (classic OAuth offers no read-only scope for private repositories, so `repo` is requested; a fine-grained PAT with Contents and Metadata read-only is the documented least-privilege route); `read:packages` SHALL be requested only when a `ghcr.io` target is used. The README SHALL document exact scopes and fine-grained PAT permissions.

#### Scenario: ghcr target
- WHEN the target is `docker://ghcr.io/...` and the token lacks `read:packages`
- THEN the tool says exactly which permission to add

### Requirement: Token storage and redaction
Tokens SHALL be stored only via `keyring`, and MUST NOT appear in config files, reports, `metrics.json`, logs, error messages or debug output.

#### Scenario: Error contains token
- WHEN a failing request's message would include the token
- THEN the displayed message has it replaced by `***`

### Requirement: Auth status and logout
`auth status` SHALL show the active source and authenticated user without the token; `logout` SHALL remove the stored token.

#### Scenario: Status
- WHEN authenticated via keyring
- THEN output shows source `keyring` and login name only

### Requirement: Distinguishable access errors
Failures SHALL distinguish repo-not-found, token-lacks-access, and organization SSO authorization required.

#### Scenario: SSO
- WHEN GitHub returns an SSO-required response
- THEN the error says to authorize the token for the organization and gives the URL from the response header
