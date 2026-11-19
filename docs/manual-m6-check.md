# M6 manual check: full session from a phone over LAN

SPEC §14 M6 acceptance: "Full session completed from a phone over LAN". Do this on macOS
**and** on a real Windows machine (SPEC §14 cross-platform rule).

## Setup
1. `cd frontend && npm run build` (the daemon serves `frontend/dist`).
2. `cd backend && uv run swipesort serve --lan`
   - The terminal prints a QR code and a `http://<lan-ip>:8765/pair?code=...` URL (valid 5 min).
   - **Windows:** the first LAN bind shows a *Windows Defender Firewall* prompt for Python.
     Allow it on **Private networks** only. If you clicked Cancel, the phone can't connect:
     allow `python.exe` under *Firewall & network protection → Allow an app through firewall*.
   - Without `--lan` the daemon is localhost-only (no prompt, no phone). The desktop
     "Pair phone" dialog explains this.
3. Phone and computer on the same Wi-Fi (guest networks often isolate clients).

## Steps
- [ ] Desktop (`http://localhost:8765`): start a session (pick input + output folders).
- [ ] Desktop: click **Pair phone**. A QR, the URL, the code and a countdown are shown.
- [ ] Phone: scan the QR with the camera app and open the link. It shows "Pairing…", then
      joins the running session (or lists sessions if there are several).
- [ ] Desktop dialog: the phone appears under *Paired devices* within ~3 s.
- [ ] Phone: the card fills the screen; edge hints sit at the screen edges; no horizontal
      scroll; the page doesn't pull-to-refresh or rubber-band while swiping.
- [ ] Swipe physics: a short fast flick commits; a slow short drag springs back; dragging
      up locks to the vertical axis (doesn't drift into left/right); dragging toward a side
      with no edge resists. Android: a short vibration on commit.
- [ ] Tap **Input** / **Output**: trees open as bottom sheets; drag the handle down (or flick)
      to dismiss. "Choose folder…" and duplicate *details* open as bottom sheets too.
- [ ] Previews (images, PDFs/thumbnails, text) load on the phone.
- [ ] **Undo** button in the header reverts the last decision (file back in input).
- [ ] Complete the session from the phone: the summary screen appears; files are where the
      cards said; the desktop's trees/progress agree.
- [ ] Desktop: **Revoke** the phone. Its next action shows the pairing screen again.
- [ ] Re-scanning an old/used QR fails with "invalid or expired"; a new code works.
- [ ] Optional: "Add to Home Screen" opens SwipeSort standalone (portrait).

## Notes
- Service workers need HTTPS or localhost, so over plain `http://<lan-ip>` the phone runs the
  PWA without offline app-shell caching; that's expected. The API is never cached.
- `swipesort pair` prints a fresh QR in the terminal for a running daemon.
