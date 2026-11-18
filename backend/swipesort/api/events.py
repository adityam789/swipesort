"""Per-session event fan-out from worker threads to WebSocket clients (SPEC §7, §12)."""

from __future__ import annotations

import asyncio
import threading

from swipesort.api.schemas import StreamEvent


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[str]]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, sid: int) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subs.setdefault(sid, []).append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, sid: int, q: asyncio.Queue[str]) -> None:
        with self._lock:
            self._subs[sid] = [(lp, x) for lp, x in self._subs.get(sid, []) if x is not q]

    def has_subscribers(self, sid: int) -> bool:
        with self._lock:
            return bool(self._subs.get(sid))

    def publish(self, sid: int, event: StreamEvent) -> None:
        """Thread-safe: callable from pipeline / request threads."""
        msg = event.model_dump_json()
        with self._lock:
            subs = list(self._subs.get(sid, []))
        for loop, q in subs:
            def put(q: asyncio.Queue[str] = q) -> None:
                if not q.full():  # a stalled client drops events; it refetches on reconnect
                    q.put_nowait(msg)
            try:
                loop.call_soon_threadsafe(put)
            except RuntimeError:  # loop closed
                self.unsubscribe(sid, q)
