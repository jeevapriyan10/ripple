# Contributing to Ripple

Thank you for contributing to Ripple! To maintain consistency, high code quality, and traceability across modules, all contributors must follow our branching and commit conventions.

---

## Branching Model (Git Flow)

We use the Git Flow branching workflow:

- **`main`**: Production-ready branch. Only contains thoroughly tested code and tagged releases. Direct commits are strictly prohibited.
- **`develop`**: Primary integration branch where ongoing work converges. All features and release fixes merge here before hitting `main`.
- **`feature/*`**: Feature branches branched from `develop` (`feature/<feature-name>`). Once completed and reviewed, they are merged back into `develop`.
- **`release/*`**: Release preparation branches cut from `develop` (`release/vX.Y.Z`). Used for final polishing, version bumps, and bug fixes. Merged into both `main` (with a release tag) and back into `develop`.
- **`hotfix/*`**: Urgent production fixes branched directly from `main` (`hotfix/<issue-name>`). Merged into both `main` (tagged) and `develop`.

```text
main       ---------------------------------[tag: v1.0.0]----
                       \                       ^
hotfix                  \---[hotfix/*]--------/ \
                         \                       \
develop    ---o-----------o-----------------------o----------
               \         / \                     /
feature         \-[feat]-/  \---[release/*]-----/
```

---

## Conventional Commits

All commit messages must adhere to the Conventional Commits specification extended with our module identifier tag:

```text
type(scope): description [M##]
```

### Types
- **`feat`**: A new feature or capability
- **`fix`**: A bug fix
- **`docs`**: Documentation only changes
- **`chore`**: Maintenance, build tooling, or dependency updates
- **`test`**: Adding missing tests or correcting existing tests
- **`refactor`**: Code change that neither fixes a bug nor adds a feature

### Scope
An optional noun describing the section of the codebase affected (e.g., `core`, `engine`, `graph`, `api`, `infra`).

### Module Identifier (`[M##]`)
Every commit must reference the module number under which the work is executed (e.g., `[M00]`, `[M01]`, `[M23]`).

### Examples
- `feat(graph): implement temporal edge aggregator [M03]`
- `fix(engine): resolve race condition in transaction ingestion [M05]`
- `docs(readme): update system prerequisites and setup guide [M01]`
- `chore(deps): update networkx dependency to latest stable [M02]`

---

## Pull Request Guidelines

1. Ensure your branch is branched from the correct base (`develop` for features/releases, `main` for hotfixes).
2. Complete all checklist items in the pull request template.
3. Make sure all commit messages conform to the conventional commits specification above.
4. Keep pull requests focused on a single module or scope.
