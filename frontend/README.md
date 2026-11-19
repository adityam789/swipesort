# SwipeSort — frontend

React + TypeScript + Vite PWA, served by the daemon from `frontend/dist`.

```bash
npm ci
npm run dev        # Vite dev server; proxies /api and the WebSocket to the daemon on :8765
npm run build      # production build + service worker → dist/
npm test -- --run  # vitest
npx tsc -b && npx oxlint
```

| Path | Contents |
|---|---|
| `src/pages/` | Setup, Sort, Summary, Pair, Watch |
| `src/cards/` | One component per card kind. Edges come from the card's data, not from per-component code. |
| `src/gestures/` | `useFling` (swipe, drag and arrow keys in one hook); every threshold is in `constants.ts` |
| `src/components/` | BottomSheet, FolderPicker, PairDialog, SessionPicker, Toast |
| `src/store/` | Zustand session store + WebSocket client |

See the [top-level README](../README.md) and [docs/architecture](../docs/architecture/HLD.md).
