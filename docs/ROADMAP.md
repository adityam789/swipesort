# Roadmap

## Done — v1.0.0

Each milestone shipped only when its acceptance check passed on macOS and Windows CI.

| | Milestone | Acceptance check | Result |
|---|---|---|---|
| M0 | Skeleton: daemon, scan, trees, manual card, executor, journal, undo, trash | Sort 50 files by hand; undoing all 50 restores the exact original tree | Pass (checksum compare) |
| M1 | Extraction, embeddings, scoring, tiers, gestures | top-1 ≥ 80 %, top-3 ≥ 95 %, next card < 100 ms | top-1 ≈ 83 %, top-3 ≈ 99 %, 17 ms |
| M2 | Duplicates: exact, near-image, near-text, version-of | 100 % exact, ≥ 90 % near, no false exact matches | 100 % / 100 % / 0 |
| M3 | Lookahead scheduler, streaming, invalidation | ≥ 15 READY cards while swiping at 1 card/s on 1k files | ≥ 16 at 4 cards/s |
| M4 | Folder descent, mid-tree `_Inbox`, LLM rerank, batch cards | Depth-4 tree: ≥ 75 % correct path, the rest in the right parent's inbox | 88.6 % (local model + LLM) |
| M5 | New-folder proposals, cold-start structure | Empty output + 300 files → sensible structure; hidden categories rediscovered | 7–8 of 8, 100 % leaf purity |
| M6 | Phone: PWA, QR pairing, touch gestures | Full session completed from a phone over LAN | Pass |
| M7 | Watch mode, login agent | 20 downloads: confident ones sorted < 10 s after finishing; all undoable | 16 of 20 sorted, < 1 s each |

Accuracy figures come from the synthetic test corpus (`backend/tests/fixtures/generate_corpus.py`), not from real-world use.

## Future enhancements

| Item | Scope | Done when |
|---|---|---|
| **M8 Learning** | Calibrate confidence from accept/reject history (isotonic regression), learn signal weights from decisions, "always X → Y" rules by filename or download source | After 200 decisions, Confirm cards are accepted ≥ 95 % of the time |
| Proposal quality | Group lectures by subject, not by course; fewer camera-based photo splits; better names without the LLM | Cold start finds 8 of 8 categories with the local model |
| LLM speed | Benchmark the 3B model; rerank only near the current card on slow machines | Rerank keeps up at 1 card/s |
| Watch mode | Re-check files that change after they were sorted; let the LLM veto an auto-move | — |
| Dry run | Compute everything, move nothing, export the plan as CSV | — |
| Platforms | Linux; native desktop app (Tauri); HTTPS on LAN so the phone app works offline | — |
| Cloud models | Opt-in cloud model setting (`models.allow_cloud`) | — |
