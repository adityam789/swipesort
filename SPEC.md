# SwipeSort — Product & Technical Spec (v0.1 draft)

> Working name. A local-first, AI-assisted file sorter with a swipe / arrow-key UI.
> You pick an **input** tree and an **output** tree. The app reads each file's name, type, metadata and first pages (or image content). It then suggests where the file belongs, whether it's a duplicate, or whether to trash it. You confirm with one gesture.

---

## 0. Principles (non-negotiable)

1. **One gesture per decision.** No typing or editing in the main flow. Swipe, arrow key, or drag.
2. **The user never waits on the AI.** Cards are computed ahead of the cursor. The UI only shows cards that are already *ready*.
3. **Nothing is destructive or irreversible.** Every action is journaled and can be undone. "Delete" means quarantine first.
4. **Local-first and private.** Models, index and files stay on the user's machine. Cloud models are an opt-in toggle that is off by default.
5. **The system learns from every decision.** Each confirm or correction becomes labeled data.

---

## 1. Key design decisions (refined from the brainstorm)

| # | Original idea | Refinement |
|---|---|---|
| 1 | Web app on laptop *or* phone | Browsers can't reach the local filesystem, and phones can't read files on the laptop. So: a **local daemon** (Python) runs on the computer that holds the files. It serves a **PWA**. The phone connects over LAN after QR-code pairing. Same UI everywhere. |
| 2 | Vector DB | **sqlite-vec inside the same SQLite file.** No server process. Scale target is **100–1,000 files per session** (output tree may hold a few thousand). At this size brute-force cosine search is instant, so there's no ANN tuning. |
| 3 | LLM gives a confidence % | LLM self-reported confidence is **not calibrated**. Confidence is a **composite score**: embedding similarity, nearest-neighbor votes, metadata priors, and agreement with an LLM reranker. It gets recalibrated from the user's accept/reject history. |
| 4 | 90 / 60 tiers | Kept, with thresholds in config. Added **batch cards**: e.g. "14 files → `Finance/Taxes/2025` at ≥95%, confirm all?" Low-confidence files are also batched into one "send to inbox" card. This gives a big speed-up. |
| 5 | Default folder can sit mid-tree | Formalized as **confidence-gated descent**: walk down the output tree while confident. Where confidence drops, place the file in that level's `_Inbox/` (see §4). |
| 6 | Dedup by content | Three tiers: **exact** (hash), **near** (perceptual hash / MinHash), **version-of** (same doc, different revision). Dedup runs *before* classification. |
| 7 | Delete | Trash goes to app quarantine (`.swipesort/trash/`) with undo and purge after N days. OS Trash is optional. |
| 8 | Create new folders | **Folder proposals** come from clustering items in inboxes and the catch-all. **Cold start**: if the output tree is empty, the app proposes an initial structure. |
| 9 | Pre-compute while user decides | Priority scheduler with a lookahead window, plus **invalidation** (a decision can change other files' suggestions, see §7). |
| 10 | (new) Project folders | Folders that are a unit (`.git`, `package.json`, `.xcodeproj`, `.app`, photo libraries) are **never scattered**. They're treated as a single item. |

---

## 2. UX

### 2.1 Layout
- **Center:** the current card. It shows a preview (thumbnail or first-page render or text snippet), file name, size, date, and *why* the suggestion was made ("matches 23 files in `Receipts/`; filename has 'invoice'").
- **Left drawer:** input tree with remaining counts. **Right drawer:** output tree, which grows live and highlights new folders.
- **Top bar:** progress, a "ready ahead" buffer indicator, and the undo stack.
- **Mobile:** drawers become bottom sheets, and the card is full-screen.

### 2.2 Interaction principle: *fling the file toward its destination*
Each card labels its screen edges with destinations. You swipe or arrow toward the label. **Down is always Trash. `Z` / ⌘Z / the Undo toast always undoes.** `Space` opens a full preview. `Tab` defers the card to later.

| Card type | → Right | ↑ Up | ← Left | ↓ Down |
|---|---|---|---|---|
| **Confirm** (≥ 90%) | Move to suggested folder | Defer | "Not this" → flips to Choices | Trash |
| **Choices** (60–90%) | Option #1 | Option #2 | Option #3 | Trash |
| **Low-confidence** (< 60%), see §6.1 | Do the recommended action (new folder **or** `_Inbox`) | Do the other action | Open folder picker | Trash |
| **Duplicate** | Keep both → continue to classify | Keep this, trash the other | Show diff / details | Trash this one |
| **Batch** (N files, same target) | Confirm all | Review one by one | Reject all → individual cards | Trash all |
| **Folder proposal** | Create folder & move members | Rename (only text input in the app) | Dismiss | — |

- Keys `1/2/3` also pick Choices. Desktop users can drag the card with the mouse.
- Choices "none of these": press `N` or long-press to get a searchable folder picker (fuzzy).
- Every action is **optimistic**: the next card appears in under 100 ms, and file operations run in the background.

### 2.3 Session flow
1. Pick input root and output root (they may be the same, for "sort in place").
2. Fast scan starts. Cards stream in within seconds, while deep processing continues in the background.
3. Card order is: duplicates first (cheapest wins), then batches, then individual cards. Within individual cards, files are grouped by source folder to keep context.
4. The session ends on a summary: moved / trashed / inboxed / folders created. You can purge the trash or keep it.

---

## 3. Confidence model

Each file gets a **digest**: about 500 tokens of text. It contains the filename tokens, parent-path tokens, MIME/extension, key metadata (EXIF date/camera/GPS-city, PDF title/author, doc properties), and a snippet of extracted text from the first 1–2 pages, slides, or sheet headers. For images, it has a caption from a small VLM plus OCR text. The digest is embedded once and cached by content hash.

Each candidate folder `F` gets a score from these signals:

```
s_centroid = cos(e_file, centroid(F))                  # mean embedding of files in F's subtree
s_profile  = cos(e_file, e(F.path + F.description))    # folder semantics; carries cold start
s_knn      = weighted vote of k=10 nearest placed files that live in F
s_meta     = priors: extension distribution of F, date-range fit, filename BM25 vs F's filenames
raw(F)     = w·[s_centroid, s_profile, s_knn, s_meta]  # weights in config; profile weight high when F is empty
p(F)       = softmax(raw / T) over sibling candidates
```

**LLM rerank** runs only on the top 5 candidates. It uses structured JSON output: `{choice, alternates[2], new_folder_suggestion?, reason}`.
- If the LLM agrees with argmax p, confidence = p_top, boosted.
- If it disagrees, confidence is capped below 0.9, so the file can never reach the Confirm tier.
- The `reason` string is shown on the card.

**Tiers:** `conf ≥ 0.90` → Confirm. `0.60–0.90` → Choices (top 3). `< 0.60` → Low-confidence: new folder vs `_Inbox` decision (§6.1).
**Calibration:** after ≥ 200 decisions, fit isotonic regression from confidence to the observed accept rate. Retrain it nightly or on demand.
**Margin rule:** if `p1 − p2 < 0.1`, the file can't be Confirm, even when p1 is high.

---

## 4. Hierarchical placement: confidence-gated descent

```
node = output_root
loop:
  children = node.subfolders (excluding _Inbox, .swipesort)
  if no children: destination = node (leaf); break
  score children using subtree centroids (§3)
  if conf(best_child) >= τ_descend (default 0.75): node = best_child; continue
  else: destination = node/_Inbox ; break
```
- The **final tier** uses the confidence of the full path, which is the product of step confidences (or the minimum, configurable).
- If descent stops mid-tree, the Inbox card says: "Probably somewhere in `Work/PayPal/`, unsure below that."
- `_Inbox/` folders are created lazily, at any depth.
- **Leaf-only by default:** files land in leaves unless the user's existing tree already keeps files at that level.

---

## 5. Duplicate & redundancy detection

| Tier | Method | Applies to | Card text |
|---|---|---|---|
| Exact | size match → xxhash of first/last 64 KB → full BLAKE3 | all | "Identical to `X` (100%)" |
| Near (image) | pHash + dHash, Hamming ≤ 6; CLIP cosine ≥ 0.95 for crops/resizes | images | "Same photo, lower resolution (97%)" |
| Near (text) | MinHash (5-shingles) Jaccard via LSH ≥ 0.85 | pdf/docx/pptx/txt | "92% same text as `X`" |
| Version-of | filename stem similarity (`_v2`, `final`, `(1)`, `copy`) + text Jaccard 0.5–0.85 | docs | "Looks like an older version of `X` (modified 3 weeks earlier)" |

- Comparisons cover **input vs output** and **input vs input**.
- The card recommends which copy to keep: newest mtime, larger resolution, or the one already in the output tree.
- Dedup suggestions are invalidated when either file is acted on.

---

## 6. New folders & cold start

### 6.1 Low-confidence routing: new folder or default inbox?
Every < 60% file goes through this decision. It isn't dumped blindly.

1. **Pool:** gather all low-confidence files in the session, including ones not yet shown.
2. **Group:** cluster the pool (HDBSCAN on digest embeddings). The descent stop point from §4 is part of the key, so files that stopped at different levels don't mix.
3. **Decide per group:**
   - **New folder** when:
     - the group has ≥ 3 members (`min_cluster`)
     - it's cohesive (mean pairwise cosine ≥ 0.70)
     - it's distinct from every existing sibling (max cosine to sibling centroids < 0.55)
     - the LLM can produce a specific name. Generic names like "Misc" or "Other" are rejected.

     The folder goes under the descent stop node, so it can be created mid-tree.
   - **Singleton, but clearly a new topic:** the file is far from everything (max cosine < 0.40) and the LLM names a specific folder. Recommend a new folder, at lower priority.
   - **Otherwise:** `_Inbox/` at the descent stop node.
4. **One card per group, not per file:**
   - "Create `Work/PayPal/Onboarding/` for these 5 files?" → Right creates it. Up sends the 5 to the inbox instead.
   - "Couldn't place these 7 files, send them to `Work/_Inbox/`?" → Right confirms. Up suggests a new folder anyway.
5. Once a new folder exists, the remaining low-confidence files are **re-scored** (§7 invalidation). Some will now land in it with high confidence.

### 6.2 Other proposals
- **Proposals:** every N decisions, or when an inbox grows past 8 items, run HDBSCAN on inbox and catch-all embeddings. A cluster with ≥ 4 members, high cohesion and low similarity to every existing folder becomes a proposal. The LLM names it, and the proposal says where it would go in the tree (same descent logic).
- **Cold start (empty output):** cluster the whole input. The LLM proposes a 2-level taxonomy of 5–12 top folders. It's shown as a single "Proposed structure" card, and the user can accept, accept part of it, or edit names.
- **Folder profiles:** the user can add a one-line description to any folder (optional). The profile embedding is its name, its path and that description.

---

## 7. Background pipeline & scheduler

```
scan ─▶ fingerprint ─▶ extract ─▶ digest+embed ─▶ dedup ─▶ classify ─▶ READY card
 (IO)      (IO/CPU)      (CPU procs)   (model)       (CPU)     (CPU + LLM)
```

- **Scan:** walks recursively and detects bundle folders (§1 #10). It also skips hidden/system files and cloud-only placeholders (configurable).
- **Priority queue:** priority is the distance from the user's cursor in card order. A **lookahead window** keeps ≥ 15 READY cards ahead. Everything else runs at idle priority, and work throttles on battery.
- **Worker pools:**
  - async IO
  - a `ProcessPool` for extraction, OCR and hashing
  - a **single-flight model queue** for embedding and LLM calls (batch embeddings 32 at a time; LLM serialized)
- **Caching:** all stage outputs are keyed by `content_hash`, so re-runs and renames are free.
- **Invalidation:** each suggestion stores `tree_version` and `index_version`.
  - mkdir/rename/delete of a folder bumps `tree_version`. A move into a folder updates that folder's centroid incrementally. Every 25 moves into a folder bumps `index_version`.
  - Stale suggestions are **rescored cheaply** (vector math, ~ms). The LLM reruns only if the top 3 changed.
  - Changes are pushed to the UI over WebSocket (`card_updated`). A card that is on screen is never swapped under the user's finger. It's updated only after it's dismissed.
- **Execution:** decisions enqueue `ops`. A single executor applies them in order, in two phases (journal intent → perform → journal done), so it's crash-safe.

---

## 8. Safety & file operations

- **Moves:** `os.rename` on the same volume. Across volumes: copy → verify BLAKE3 → unlink source.
- **Name collisions:** if the file is identical, it's a dedup case. Otherwise append ` (2)`.
- **Trash:** move to `<output_root>/.swipesort/trash/<session>/<relative_path>`. Purge after 30 days (config). Optional `send2trash` to the OS trash.
- **Undo:** unlimited within a session, using the journal. It reverses ops LIFO and removes `_Inbox`/new folders if they're empty.
- **Guards:** skip files that are open or locked, skip symlinks (don't follow), never touch outside the chosen roots, and handle input and output overlapping (sort in place).
- **Dry-run mode:** compute everything, execute nothing, export a plan as CSV.

---

## 9. Architecture

```
┌──────────────── Laptop / desktop (holds the files) ─────────────────┐
│                                                                     │
│  swipesort daemon (Python, FastAPI, uvicorn)                        │
│  ├─ API (REST) + WebSocket event stream                             │
│  ├─ Session manager ──▶ Scheduler (priority queue, lookahead)       │
│  │                       ├─ Scanner / Watcher (watchdog)            │
│  │                       ├─ Extractors (per type, process pool)     │
│  │                       ├─ Model gateway (embed / VLM / LLM)───────┼──▶ Ollama (local)
│  │                       ├─ Dedup engine                            │     or opt-in cloud
│  │                       ├─ Classifier (descent + scoring + rerank) │
│  │                       └─ Folder proposer (clustering)            │
│  ├─ Executor + Journal (moves, trash, mkdir, undo)                  │
│  ├─ SQLite (state)  +  LanceDB/sqlite-vec (vectors)  + thumb cache  │
│  └─ Serves PWA static files                                         │
│                                                                     │
└───────────────▲─────────────────────────────▲───────────────────────┘
                │ localhost                   │ LAN (token from QR pairing)
          Desktop browser                 Phone browser (PWA)
```

---

## 10. Tech stack

**Platforms: macOS and Windows 10/11** (Linux later). All OS-specific code lives behind one `platform/` interface with a `macos.py` and a `windows.py` implementation. The rest of the codebase never branches on OS. CI runs the full test suite on `macos-latest` **and** `windows-latest` from M0, and every milestone's acceptance check must pass on both.

**Backend:** Python 3.12, `uv`, FastAPI, uvicorn, Pydantic v2, SQLModel on SQLite (WAL), sqlite-vec, `watchdog` (FSEvents on macOS, ReadDirectoryChangesW on Windows). Use `pathlib` everywhere and never build paths with string concatenation.

**`platform/` interface: one row per method**

| Method | macOS | Windows | Fallback |
|---|---|---|---|
| `ocr(image)` | Apple Vision (`ocrmac` / `pyobjc-framework-Vision`) | `Windows.Media.Ocr` via `winrt` / `winocr` | `rapidocr-onnxruntime` |
| `source_metadata(path)` | Spotlight `kMDItemWhereFroms`, authors, content type | `Zone.Identifier` alternate data stream (`HostUrl`, `ReferrerUrl`) + Shell property system (`pywin32` `propsys`) | none |
| `user_tags(path)` | Finder tags (read, optional write) | none (skip) | none |
| `thumbnail(path)` | Quick Look `qlmanage -t` | Shell `IShellItemImageFactory` via `pywin32` | `pypdfium2` render / Pillow / text snippet |
| `send_to_os_trash(path)` | `send2trash` (Finder Trash, Put Back works) | `send2trash` (Recycle Bin, Restore works) | quarantine only |
| `is_cloud_placeholder(path)` | iCloud `.icloud` stubs / ubiquity keys | OneDrive: `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` / `OFFLINE` attributes | treat as normal |
| `check_access(root)` | Full Disk Access / Files & Folders guidance | Controlled Folder Access (Defender) guidance | try-read probe |
| `notify(title, body, undo_url)` | `UserNotifications` via pyobjc (or `osascript`) | Windows toast (`windows-toasts`) | log only |
| `install_background_agent()` | `launchd` LaunchAgent plist | Task Scheduler "at logon" task (`schtasks`) | manual `swipesort serve` |
| `tray_icon()` (later) | `rumps` menu-bar icon | `pystray` tray icon | none |

**Windows-specific gotchas to handle in the executor and scanner:**
- **Locked files:** moving a file that's open in another process fails (`PermissionError` / sharing violation). Retry with backoff, then mark the card "file in use — skip for now". Never force it.
- **Long paths:** use the `\\?\` prefix for paths over 260 chars, or require LongPathsEnabled and warn if it's off.
- **Reserved and invalid names:** `CON`, `NUL`, `PRN`, `AUX`, `COM1`… and the characters `<>:"/\|?*` and trailing dots or spaces. Sanitize LLM-proposed folder names on **both** OSes so a tree created on a Mac still works on Windows.
- **Case-insensitive collisions:** NTFS (and default APFS) are case-insensitive. Check for `Report.pdf` vs `report.pdf` before moves.
- **Cross-drive moves:** `C:` → `D:` uses copy + verify + delete (same as cross-volume on macOS).
- **Hidden and system files:** skip by attribute (`FILE_ATTRIBUTE_HIDDEN/SYSTEM`), as well as dotfiles, `desktop.ini` and `Thumbs.db`.
- **LAN pairing:** the first bind to `0.0.0.0` triggers a Windows Firewall prompt. Explain it in the setup UI, and allow localhost-only mode.

**ML:** Ollama runs on both (Metal on Apple Silicon; CUDA/ROCm/CPU on Windows). Model defaults must run acceptably on CPU-only Windows laptops, so pick an "eco" model profile there (smaller LLM, rerank only on Choices tier).

**Extraction:**
- `pypdfium2` (render + text; Apache/BSD, so avoid AGPL PyMuPDF), `python-docx`, `python-pptx`, `openpyxl`
- `Pillow` + `pillow-heif`, `exifread`, `mutagen` (audio), `ffprobe` (video)
- `python-magic` (MIME), OCR via `rapidocr-onnxruntime` (Tesseract fallback)

**Hashing / dedup:** `blake3`, `xxhash`, `imagehash`, `datasketch` (MinHash LSH).

**ML:** model gateway with providers. Default is **Ollama**:
- text embedding model (e.g. a small `nomic-embed`/`bge`-class model)
- small vision-language model for image captions
- 3–8B instruct model with JSON-schema output for rerank and naming

Also `open_clip` for image near-dup, and scikit-learn `HDBSCAN`. All model names live in config.

**Frontend:** React + TypeScript + Vite, Tailwind, `@use-gesture/react` + `framer-motion` (swipe physics), Zustand, TanStack Query, `vite-plugin-pwa`, react-arborist (trees).

**Packaging (later):** `pipx install swipesort` → `swipesort serve`. Possible Tauri wrapper later.

---

## 11. Data model (SQLite)

```sql
session(id, input_root, output_root, created_at, status, config_json)
file(id, session_id, path, rel_path, is_bundle, size, mtime, mime, ext,
     quick_hash, content_hash, phash, minhash_blob, status)  -- status: queued|extracted|embedded|ready|decided|skipped|error
extraction(content_hash PK, digest_text, metadata_json, thumb_path, page_count, ocr_used)
folder(id, session_id, path, parent_id, depth, is_leaf, is_inbox, description,
       n_files, centroid_ref, created_by)            -- created_by: existing|user|proposal|inbox
suggestion(id, file_id, kind, candidates_json, confidence, tier, reason,
           tree_version, index_version, model_version, created_at, stale)
           -- kind: classify|duplicate|batch|folder_proposal
decision(id, file_id, suggestion_id, action, target_folder_id, ts, latency_ms, undone)
           -- action: accept|choose_1..3|pick|inbox|trash|keep_both|keep_this|defer
op(id, decision_id, type, src, dst, status, ts)       -- type: move|mkdir|trash|rmdir ; status: intent|done|undone|failed
proposal(id, session_id, parent_folder_id, name, member_file_ids_json, cohesion, status)
```
Vectors live in LanceDB tables `file_vec(content_hash, vec, modality)` and `folder_vec(folder_id, centroid, profile_vec)`.

---

## 12. API

```
POST   /api/pair                          -> token (QR shown in terminal/desktop UI)
POST   /api/sessions                      {input_root, output_root, options}
GET    /api/sessions/{id}
GET    /api/sessions/{id}/tree?side=input|output
GET    /api/sessions/{id}/cards?limit=20  -> ready cards in order
POST   /api/cards/{suggestion_id}/decide  {action, target_folder_id?}
POST   /api/sessions/{id}/undo            {steps:1}
POST   /api/folders                       {parent_id, name}        (manual mkdir)
POST   /api/proposals/{id}/decide         {action: accept|rename|dismiss, name?}
GET    /api/files/{id}/preview            -> thumb / page render / text
GET    /api/fs/browse?path=               -> folder picker for choosing roots
WS     /api/sessions/{id}/stream
       events: card_ready, card_updated, card_removed, tree_changed,
               progress{scanned, extracted, ready, decided}, op_failed
```

---

## 13. Repo layout

```
swipesort/
  CLAUDE.md
  SPEC.md
  backend/
    pyproject.toml
    swipesort/
      main.py               # FastAPI app, static mount
      config.py             # defaults + user overrides (toml)
      db/                   # models.py, session.py, migrations/
      api/                  # routes_sessions.py, routes_cards.py, ws.py, pairing.py
      pipeline/
        scheduler.py        # priority queue, lookahead, invalidation
        scanner.py          # recursive walk, bundle detection
        fingerprint.py
        extractors/         # base.py, pdf.py, docx.py, pptx.py, xlsx.py, image.py, media.py, text.py
        digest.py
      ml/
        gateway.py          # provider interface
        providers/ollama.py, providers/cloud.py
        embed.py, caption.py, rerank.py, naming.py
      engine/
        scoring.py          # signals + softmax + calibration
        descent.py          # confidence-gated descent
        dedup.py
        proposer.py         # clustering + cold start
        batching.py
      exec/
        executor.py, journal.py, trash.py, names.py   # names.py: cross-OS filename sanitizing + collision checks
      platform/
        base.py             # Platform protocol (see §10 table)
        macos.py, windows.py, fallback.py
        __init__.py         # get_platform() picks by sys.platform
    tests/
      fixtures/generate_corpus.py
      ...
  frontend/
    src/
      cards/                # ConfirmCard, ChoicesCard, InboxCard, DuplicateCard, BatchCard, ProposalCard
      gestures/             # useFling (swipe + arrows + drag unified)
      trees/                # InputTree, OutputTree
      store/                # zustand session store, ws client
      pages/                # Setup, Sort, Summary, Pair
  eval/
    run_eval.py             # accuracy, tier distribution, calibration plot on fixture corpus
```

---

## 14. Milestones (build in this order; each must pass its acceptance check)

| M | Scope | Acceptance |
|---|---|---|
| **M0 Skeleton** | Daemon, session setup, recursive scan, both trees in the UI, **manual** card (folder picker), executor + journal + undo + trash | Sort 50 fixture files by hand; undo all 50 restores the exact original tree (checksum compare) |
| **M1 Basic AI** | Extractors, digest, embeddings, centroid + profile + kNN scoring, tiers, Confirm/Choices/Inbox cards, gestures + arrow keys | On the fixture corpus: top-1 ≥ 80%, top-3 ≥ 95%; card → next card < 100 ms |
| **M2 Dedup** | Exact + near (image, text) + version-of, Duplicate card | Finds 100% exact and ≥ 90% seeded near-dups; no false exact matches |
| **M3 Lookahead** | Scheduler, priorities, WS streaming, invalidation + cheap rescore | ≥ 15 ready cards maintained while swiping at 1 card/sec on 1k files; creating a folder updates pending cards |
| **M4 Hierarchy + LLM** | Confidence-gated descent, mid-tree `_Inbox`, LLM rerank + reasons, batch cards | Nested fixture tree (depth 4): correct-path rate ≥ 75%, rest land in the correct ancestor's inbox |
| **M5 New folders** | Proposals from clustering, cold-start taxonomy | Empty output + 300 mixed files → proposed structure the eval rates sensible; seeded hidden categories rediscovered |
| **M6 Mobile** | PWA, QR pairing, touch swipe physics, bottom sheets | Full session completed from a phone over LAN |
| **M7 Watch mode** | See §18. Background agent (launchd / Task Scheduler), file-event watcher on chosen folders, settle delay, auto-move for Confirm-tier files above the auto threshold, notification + "pending" queue for the rest | Drop 20 files into a watched Downloads folder: high-confidence ones are auto-sorted within 10 s of finishing download; the rest appear as cards; every auto-move is undoable |
| **M8 Learning** | Isotonic calibration, decision-driven weights, filename/source rules ("always X → Y", "from paypal.com → Work/PayPal") | After 200 decisions, Confirm-tier accept rate ≥ 95% |
| Later | Dry-run plan export, Linux, Tauri/native app, cloud model toggle | — |

**Cross-platform rule:** every milestone's acceptance check runs on macOS **and** Windows (CI matrix plus one manual run on a real Windows machine for M0, M6 and M7).

---

## 15. Testing & eval
- **`generate_corpus.py`** builds a synthetic corpus with ground truth:
  - invoices, receipts, resumes, lecture slides, tax forms, photos (various EXIF), screenshots, code projects
  - seeded exact dupes, resized images, edited docs (`_v2`, `final`), and misleading filenames
- Unit tests per extractor and per engine module. Property tests show that the journal makes moves and undos reversible.
- `eval/run_eval.py` reports accuracy@1/3, tier distribution, a calibration curve, and p50/p95 latencies per stage.

---

## 16. Default config (`~/.swipesort/config.toml`)
```toml
[tiers]      confirm = 0.90  choices = 0.60  descend = 0.75  min_margin = 0.10
[pipeline]   lookahead = 15  embed_batch = 32  max_text_chars = 4000  pages = 2
[dedup]      image_hamming = 6  text_jaccard = 0.85  version_low = 0.5
[proposals]  min_cluster = 3  cohesion = 0.70  max_sibling_sim = 0.55  singleton_new_topic = 0.40
[watch]      enabled = false  folders = ["~/Downloads"]  settle_seconds = 5  auto_move_threshold = 0.97
             auto_move = true  notify = true  ignore = ["*.crdownload", "*.part", "*.download"]
[trash]      mode = "quarantine"   purge_days = 30
[models]     provider = "ollama"  embed = "<config>"  vlm = "<config>"  llm = "<config>"  allow_cloud = false
[scan]       skip_hidden = true  follow_symlinks = false  skip_cloud_placeholders = true
```

---

## 17. Resolved decisions
1. **OS:** macOS and Windows from day one, via the `platform/` layer (§10). Linux later.
2. **Scale:** 100–1,000 files per session.
3. **< 60% files:** choose between creating a new folder and the default `_Inbox` (§6.1), shown as one card per group.
4. **Watch mode:** in v1 (M7, §18).

## 18. Watch mode (v1)
- The user picks one or more watched folders (default `~/Downloads`), each with a target output tree.
- A background agent keeps the daemon running at login (`launchd` on macOS, a Task Scheduler logon task on Windows). OS file events trigger on new or changed files.
- **Settle:** wait until size and mtime are stable for `settle_seconds` **and** the file can be opened exclusively (on Windows, the browser holds a lock while downloading). Ignore partial downloads (`.crdownload`, `.part`, `.download`, `.tmp`, `.partial`).
- New files go through the same pipeline:
  - **≥ `auto_move_threshold` (0.97) and not a duplicate** → moved automatically, journaled and undoable. A macOS notification says "Sorted `invoice_0412.pdf` → `Finance/Receipts`," with an Undo action.
  - **Everything else** → added to a pending queue. The menu-bar or app badge shows a count, and the user clears it later with the normal swipe UI.
- **Exact duplicates of a file already in the output tree** go to quarantine automatically when auto-trash is on. It's off by default, and when off they become pending cards.
- **Safety:** auto-move is off until the user has made ≥ 50 decisions, so the calibration has data. The app shows a weekly digest of what was auto-sorted.
