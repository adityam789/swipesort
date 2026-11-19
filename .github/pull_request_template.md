## What & why

## How it was verified

- [ ] `uv run pytest`, `mypy`, `ruff` pass
- [ ] `npm test`, `tsc -b`, `oxlint`, `npm run build` pass
- [ ] CI is green on macOS **and** Windows
- [ ] File operations go through the executor; no `sys.platform` checks outside `platform/`
- [ ] `CHANGELOG.md` updated
