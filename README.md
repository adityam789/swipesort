# SwipeSort

AI-assisted file sorter for macOS and Windows with a swipe / arrow-key UI. It runs locally and keeps your files private.

- `SPEC.md`: full product and technical design
- `CLAUDE.md`: build rules for Claude Code

## Kick off with Claude Code
From this folder, run `claude` and paste:

> Read CLAUDE.md and SPEC.md. Start milestone M0: scaffold the backend (uv) and frontend (Vite), the platform layer (macOS + Windows + fallback), the fixture corpus generator, and the GitHub Actions matrix. Then implement scan, trees, the manual card, the executor/journal/undo, and trash. Stop when M0's acceptance check passes on macOS and report the results.

## Prerequisites
- Python 3.12 + [uv](https://docs.astral.sh/uv/)
- Node 20+
- [Ollama](https://ollama.com) (needed from M1 onward)
