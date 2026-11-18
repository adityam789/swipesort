# CLAUDE.md — SwipeSort

Read `SPEC.md` first. It is the source of truth for product behavior, architecture and milestones.

## How to work
- Build **milestone by milestone** (SPEC §14), in order. Don't start M(n+1) until M(n)'s acceptance check passes. Report the result of that check when you finish a milestone.
- Build the fixture corpus generator (`backend/tests/fixtures/generate_corpus.py`) during M0. Every later milestone is measured against it.
- Keep model names, thresholds and weights in `config.py` / `config.toml`. Never hard-code them.

## Hard rules
- **Never hard-delete user files.** "Trash" means moving the file to `.swipesort/trash/` through the executor. Only an explicit purge command deletes.
- **Every filesystem mutation goes through `exec/executor.py`** and is journaled (intent → done) so it can be undone. No `os.rename`/`shutil` calls elsewhere.
- Never read or write outside the session's input and output roots. Don't follow symlinks.
- Never scatter bundle folders (`.git`, `package.json`, `.xcodeproj`, `.app`, …). Move them as one unit.
- The UI must never block on ML. Endpoints return precomputed READY cards. Heavy work lives in the scheduler.
- Don't use AGPL dependencies (use `pypdfium2`, not PyMuPDF).

## Platform
- **macOS and Windows** are both first-class. All OS-specific code (OCR, download-source metadata, thumbnails, Trash/Recycle Bin, iCloud/OneDrive placeholders, notifications, the background agent) goes behind `swipesort/platform/` (`base.py` protocol, `macos.py`, `windows.py`, `fallback.py`). Nothing outside `platform/` may check `sys.platform`.
- Set up a GitHub Actions matrix (`macos-latest`, `windows-latest`) in M0. A milestone isn't done until tests pass on both.
- Use `pathlib` only. Sanitize every generated folder name for Windows rules (reserved names, `<>:"/\|?*`, trailing dots or spaces) on both OSes. Handle case-insensitive collisions and locked files (retry, then skip; never force).
- Target scale is 100–1,000 files per session. Prefer simple brute-force approaches over premature optimization.

## Stack & commands
- Backend: Python 3.12 + `uv`. `cd backend && uv run pytest`, `uv run swipesort serve --dev`
- Frontend: React + TS + Vite. `cd frontend && npm run dev` (proxies `/api` to the daemon)
- Local models: embeddings in-process via `fastembed` (ONNX, Hugging Face `bge-small`; provider `local`, the default); LLM + VLM via Ollama (optional, degrades gracefully). Tests must pass **without** any model by using the fake provider in `ml/providers/fake.py`.

## Conventions
- Type everything (Pydantic models for API, mypy-clean). Use async for IO and a process pool for CPU extraction.
- Each extractor implements `Extractor.supports(mime) / extract(path) -> Extraction`.
- Each engine module is a pure function over data where possible, so it's easy to unit test.
- Frontend: one `useFling` hook unifies swipe, drag and arrow keys. Cards declare their edge targets as data, not hard-coded per component.
