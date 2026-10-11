# Contributing to Ripple

Thank you for your interest in contributing to Ripple!

## Branching Strategy (Git Flow)

We follow a Git Flow branching model:

- `main`: Production-ready code. Contains tagged releases only. Direct commits and non-release PRs are not permitted.
- `develop`: Integration branch for ongoing development and testing. All feature branches merge here.
- `feature/*`: Feature development branches. Branched from `develop` and merged back into `develop` via pull requests (e.g., `feature/transaction-graph`).
- `release/*`: Release candidate branches. Cut from `develop` when preparing a new release, and merged into both `main` (with release tag) and `develop`.
- `hotfix/*`: Emergency fixes for production. Branched directly from `main` and merged into both `main` and `develop`.

## Commit Conventions

We strictly enforce the Conventional Commits specification annotated with the module number:

```text
type(scope): description [M##]
```

### Types
- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation-only changes
- `chore`: Maintenance tasks, dependencies, tooling changes
- `test`: Adding or correcting tests
- `refactor`: Code changes that neither fix a bug nor add a feature

### Module Tag
- `[M##]`: Two-digit module number corresponding to the current milestone or task (e.g., `[M00]`, `[M01]`, `[M23]`).

### Examples
- `feat(graph): add temporal edge weighting algorithm [M04]`
- `fix(ingest): handle null timestamp in payload deserialization [M02]`
- `docs(readme): update deployment topology diagram [M12]`
- `chore(repo): initialize repository scaffolding and templates [M00]`
