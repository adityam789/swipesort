# SwipeSort user guide

## 1. Install

You need **uv** (Python manager), **Node 20+**, and optionally **Ollama** for the AI explanations and folder names.

| | macOS | Windows (PowerShell) |
|---|---|---|
| uv | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `winget install --id=astral-sh.uv -e` |
| Node | `brew install node` | `winget install OpenJS.NodeJS.LTS` |
| Ollama (optional) | `brew install ollama` | `winget install Ollama.Ollama` |

Then:

```bash
git clone https://github.com/adityam789/swipesort.git && cd swipesort
make setup                 # macOS
make llm                   # optional: download the LLM (~4.7 GB)
```

```powershell
git clone https://github.com/adityam789/swipesort.git; cd swipesort
.\scripts\swipesort.ps1 setup          # Windows
.\scripts\swipesort.ps1 llm            # optional
```

If PowerShell blocks the script, run it as `powershell -ExecutionPolicy Bypass -File scripts\swipesort.ps1 setup`.

Every task works the same on both systems: `make <task>` on macOS, `.\scripts\swipesort.ps1 <task>` on Windows. `help` lists them all.

## 2. Start

```bash
make run          # → open http://127.0.0.1:8765
```

1. Pick an **input folder** (the mess) and an **output folder** (where things should go). They may be the same folder: sort in place.
2. Cards appear within seconds. The first run downloads the embedding model (~130 MB) once.
3. An **empty output folder** gets a "Proposed structure" card first. Accept it, keep only the folders you want, or rename them.

## 3. Sorting

Each card is one file (or one group of files). Fling it toward a labelled edge: swipe, drag, or use the arrow keys.

| Card | → Right | ↑ Up | ← Left | ↓ Down |
|---|---|---|---|---|
| **Confirm** (sure) | Move to suggested folder | Later | Not this: show choices | Trash |
| **Choices** (top 3) | 1st choice | 2nd choice | 3rd choice | Trash |
| **Inbox** (unsure) | Send to `<folder>/_Inbox` | Maybe `<best guess>` | Choose folder… | Trash |
| **Duplicate** | Keep both | Keep this, trash the other | Details | Trash this one |
| **Batch** (N alike) | Move all N | Show each file separately | Reject all | Trash all |
| **New folder** | Create folder and move the files | Send them to the inbox | Dismiss | |

| Key | Action |
|---|---|
| ← → ↑ ↓ | Fling the card |
| 1 2 3 | Pick choice 1–3 |
| N | Folder picker (search, or create a folder) |
| Space | Large preview (on a phone, tap the card) |
| Tab | Skip for now |
| Z, Ctrl+Z, ⌘Z | Undo |

**Safety.** Every action can be undone, with no limit. "Trash" moves the file to `<output>/.swipesort/trash/`. Nothing is deleted until you run `swipesort purge <session-id>`, which removes quarantined files older than 30 days.

## 4. Use your phone

```bash
make lan          # instead of `make run`
```

1. On the computer, click **Pair phone** and scan the QR code. The phone must be on the same Wi-Fi.
2. The phone joins the running session. You can install it to the home screen (Share → Add to Home Screen).
3. **Windows:** the first `lan` start shows a firewall prompt. Allow **Private networks** only.

Paired phones can sort but can't start sessions or browse your disk. Revoke a phone under **Pair phone → Paired devices**. Without `lan`, nothing outside your computer can connect.

## 5. Watch mode (automatic sorting)

Watch mode sorts new files, such as downloads, as they arrive.

```bash
make watch        # watch the folders in config (default: ~/Downloads)
make agent        # also start automatically at login (agent-remove to undo)
```

Manage folders under **Watched folders** in the app, or in the config file (§7). A file is moved automatically only when **all** of these hold:
- the download has finished (size stable for 5 s, not locked),
- confidence ≥ 0.97,
- it isn't a duplicate,
- you've made at least 50 decisions yourself.

Everything else waits as a card, with a badge count. Each automatic move shows a notification and can be undone from **Watched folders → Activity**.

## 6. Pick a setup

| You have / want | Set up | Config (`~/.swipesort/config.toml`) |
|---|---|---|
| A recent Mac or a PC with 16 GB+ RAM | `make llm` | defaults |
| 8 GB RAM or no GPU | `make llm-eco` | `[llm]` `eco = true` |
| No LLM at all (fastest, fully offline) | — | `[llm]` `provider = "none"` |
| LLM without Ollama (in-process) | `cd backend && uv sync --extra llm-inprocess` | `[llm]` `provider = "llamacpp"` |
| Ollama on another port or machine | — | `[models]` `ollama_url = "http://host:11434"` |

Without an LLM you still get suggestions, duplicates, batches and folder proposals (with keyword names). You lose the one-line reasons and the second opinion.

## 7. Configuration

Settings live in `~/.swipesort/config.toml` (Windows: `%USERPROFILE%\.swipesort\config.toml`). Create the file if it doesn't exist; anything you leave out keeps its default. Restart after editing. `SWIPESORT_HOME` moves the whole folder.

```toml
[tiers]
confirm = 0.90        # at or above → Confirm card
choices = 0.60        # at or above → Choices card; below → Inbox
descend = 0.75        # confidence needed to go one folder deeper

[llm]
provider = "ollama"   # ollama | llamacpp | none
eco = false           # small model, rerank Choices only

[models]
llm = "qwen2.5:7b-instruct"
local_embed = "BAAI/bge-small-en-v1.5"

[watch]
folders = ["~/Downloads"]          # or [{ path = "~/Downloads", output = "~/Sorted" }]
default_output = "~/Sorted"
auto_move = true
auto_move_threshold = 0.97
min_decisions_for_auto = 50
settle_seconds = 5

[scan]
skip_hidden = true
skip_cloud_placeholders = true     # iCloud / OneDrive files not downloaded locally

[proposals]
enabled = true                     # suggest new folders
min_cluster = 3                    # smallest group worth a folder

[batch]
min_size = 3                       # group N alike files into one card

[trash]
purge_days = 30

[server]
port = 8765
```

Every setting, with its default and a comment, is in [`backend/swipesort/config.py`](../backend/swipesort/config.py).

## 8. Your data

| What | Where |
|---|---|
| App state, caches, logs | `~/.swipesort/` |
| Trash (quarantine) | `<output>/.swipesort/trash/` |
| Embedding model | `~/.swipesort/` (downloaded once) |
| LLM | Ollama's model folder |

Nothing is sent anywhere: models run locally, and the app makes no network calls except the one-time model downloads.

**Uninstall:**
1. `make agent-remove`, if you installed the agent.
2. Delete the repo folder and `~/.swipesort/`.
3. Optionally, `ollama rm qwen2.5:7b-instruct`.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `make doctor` reports a missing tool | Install it with the command shown (§1). |
| Page is blank or 404 | `make ui`, then restart. |
| Phone can't connect | Same Wi-Fi, not a guest network; started with `make lan`; Windows firewall allowed on Private networks. |
| Pairing code expired | Codes last 5 minutes. Click **Pair phone** again, or run `make pair`. |
| Cards have no reasons | Ollama isn't running or the model isn't pulled: `ollama serve`, then `make llm`. |
| Slow on an older machine | `make llm-eco` and set `eco = true`, or turn the LLM off. |
| "File in use, skipped" | Another app has it open. Close it; the card comes back. |
| A move was wrong | Undo (Z). In watch mode: **Watched folders → Activity → Undo**. |
| After an upgrade the app starts empty | The database format changed. The old one is kept as `~/.swipesort/swipesort.db.v<N>.bak`. |
