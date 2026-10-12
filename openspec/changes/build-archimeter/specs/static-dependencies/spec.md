## ADDED Requirements

### Requirement: Python imports
The Python extractor SHALL use stdlib `ast` to resolve absolute, relative and `__init__` re-export imports to in-project files, ignoring stdlib and third-party targets (recorded as external).

#### Scenario: Cycle fixture
- WHEN `a` imports `b` and `b` imports `a`
- THEN both edges exist and the cycle is reported

### Requirement: JS/TS imports
The JS/TS extractor SHALL use tree-sitter to read `import`, `export ... from`, `require` and dynamic `import()` with literal specifiers, and resolve `tsconfig.json` `paths`/`baseUrl` aliases, extension and index resolution, and workspace packages.

#### Scenario: Alias
- WHEN `@app/x` maps to `src/x` in tsconfig
- THEN the edge targets `src/x/index.ts`

### Requirement: Parse failures are reported
Files that fail to parse SHALL be recorded as errors and skipped, not guessed.

#### Scenario: Syntax error
- WHEN a file has a syntax error
- THEN analysis continues and `parse_errors` lists it
