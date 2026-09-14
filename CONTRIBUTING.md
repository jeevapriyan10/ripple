# Contributing to Ripple

## Branching model

Ripple uses Git Flow:

- `main` is the production branch. Only tagged releases are merged here.
- `develop` is the integration branch for completed work.
- `feature/*` branches from `develop` for new work and merges back into `develop`.
- `release/*` is cut from `develop` to prepare a release.
- `hotfix/*` branches from `main` to address production issues.

## Commit messages

Use Conventional Commits in this format:

```
type(scope): description [M##]
```

Allowed types are `feat`, `fix`, `docs`, `chore`, `test`, and `refactor`. Replace
`M##` with the relevant module number.

Example:

```
feat(graph): add transaction relationship schema [M01]
```
