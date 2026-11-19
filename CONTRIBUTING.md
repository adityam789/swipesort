# Contributing

Thanks for helping. [docs/architecture](docs/architecture/HLD.md) explains how the code fits together.
The rules below apply to every change.

## Setup and checks

```bash
make setup        # Windows: .\scripts\swipesort.ps1 setup
make test lint    # run before opening a PR
```

CI runs the same checks on **macOS and Windows**. A change isn't done until both pass.

## Hard rules (summary)

- Never hard-delete user files. All filesystem changes go through `exec/executor.py` and are journaled.
- Never read or write outside the session roots, and never follow symlinks.
- Only `swipesort/platform/` may check `sys.platform`. Use `pathlib`, and sanitize generated names for Windows.
- The UI must never block on ML. Heavy work belongs in the pipeline.
- Thresholds, weights and model names go in `config.py`, never hard-coded.
- Tests must pass with the fake model provider. No network, no models.
- No AGPL dependencies.

## Commits and PRs

- Keep PRs focused. Describe the behavior change and how you verified it.
- Add or update tests next to the code you change. Behavior covered by a milestone needs an acceptance test.
- Update `CHANGELOG.md` under an `Unreleased` heading.
