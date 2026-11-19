# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `Makefile` (macOS / Linux) and `scripts/swipesort.ps1` (Windows): one-command setup, run, phone, watch, agent and LLM tasks.
- [User guide](docs/user-guide.md) and [roadmap](docs/ROADMAP.md).

### Removed
- Internal planning files (the build spec and agent instructions) from the repository.

## [1.0.0] — 2026-11-19

First public release. It covers milestones M0–M7 (see [docs/ROADMAP.md](docs/ROADMAP.md)).

### Added
- **M0 Skeleton:**
  - FastAPI daemon; recursive scan with bundle detection; input and output trees.
  - Manual card with a folder picker.
  - Journaled executor (intent → done) with unlimited LIFO undo, quarantine trash, crash recovery and `purge`.
  - macOS + Windows CI matrix.
- **M1 Basic AI:**
  - Extractors for PDF, Office documents, images (EXIF, HEIC), media, code and text, plus a digest for each file.
  - In-process embeddings with fastembed (bge-small).
  - Centroid, profile, kNN and metadata scoring, with Confirm / Choices / Inbox tiers.
  - Swipe, drag and arrow-key gestures through one `useFling` hook.
- **M2 Dedup:**
  - Exact duplicates (size → xxhash → BLAKE3).
  - Near-duplicate images (pHash + dHash) and near-duplicate text (MinHash).
  - Version-of detection.
  - Duplicate card shown first.
- **M3 Lookahead:**
  - Priority scheduler with lookahead-aware batch sizing.
  - WebSocket streaming.
  - `tree_version` / `index_version` invalidation with a cheap background re-score.
- **M4 Hierarchy + LLM:**
  - Confidence-gated mass descent with a mid-tree `_Inbox`.
  - Data-driven "none of these" option.
  - LLM rerank with one-sentence reasons, through Ollama or in-process llama.cpp.
  - Batch cards.
- **M5 New folders:**
  - HDBSCAN proposals for low-confidence files.
  - Cold-start two-level taxonomy card.
  - LLM folder naming with a keyword fallback.
  - Accepting a proposal is one undo step.
- **M6 Mobile:**
  - Installable PWA.
  - QR pairing with hashed, revocable device tokens; LAN auth middleware.
  - Bottom sheets and touch fling physics.
- **M7 Watch mode:**
  - watchdog watchers with settle and lock checks.
  - Gated auto-move with targeted undo and notifications.
  - Background agent (launchd / Task Scheduler).
  - Watch settings and a 7-day activity view.
- Architecture docs (HLD / LLD) with PlantUML diagrams.

### Changed
- Creating a folder now re-clusters the files that still don't fit right after the re-score, without waiting for the next periodic proposer run.
- M8 (learning) and the later items moved to [future enhancements](docs/ROADMAP.md#future-enhancements).

[Unreleased]: https://github.com/adityam789/swipesort/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/adityam789/swipesort/releases/tag/v1.0.0
