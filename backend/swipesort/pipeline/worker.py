"""Background pipeline for one session (SPEC §7, M1 version).

fingerprint -> extract (process pool) -> digest -> embed (single-flight) -> score -> READY.
Runs in a daemon thread so the API never blocks on ML. M3 replaces the linear order with a
priority scheduler + invalidation; the stage functions here stay the same.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from sqlalchemy.engine import Engine
from sqlmodel import Session as DB
from sqlmodel import col, select

from swipesort.config import Settings
from swipesort.db.models import (
    Extraction,
    Features,
    File,
    FileStatus,
    Folder,
    IndexedFile,
    Suggestion,
)
from swipesort.db.models import Session as SessionRow
from swipesort.engine import scoring
from swipesort.engine.dedup import DedupItem, find_duplicates
from swipesort.ml.embed import embed_cached
from swipesort.ml.gateway import ModelUnavailable, Provider
from swipesort.pipeline.digest import build_digest, tokenize_name
from swipesort.pipeline.extractors.base import ExtractResult
from swipesort.pipeline.extractors.registry import extract
from swipesort.pipeline.features import compute_features
from swipesort.pipeline.fingerprint import content_hash
from swipesort.pipeline.scanner import scan
from swipesort.platform.base import Platform

log = logging.getLogger(__name__)

Vec = np.ndarray


def _rel(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return "" if rel == "." else rel


def profile_text(rel_path: str, description: str) -> str:
    words = " ".join(" ".join(tokenize_name(part)) or part
                     for part in rel_path.split("/") if part)
    return f"folder {words} {rel_path} {description}".strip()


class SessionPipeline:
    def __init__(self, engine: Engine, settings: Settings, platform: Platform,
                 provider: Provider, session_id: int) -> None:
        self.engine = engine
        self.settings = settings
        self.platform = platform
        self.provider = provider
        self.sid = session_id
        self.done = threading.Event()
        self.error: str = ""
        self.degraded = False  # True when the model provider is unavailable -> manual cards
        self._lock = threading.Lock()
        self._specs: list[scoring.FolderSpec] = []
        self._folder_paths: dict[int, str] = {}
        self._direct_vecs: dict[int, list[Vec]] = {}
        self._direct_names: dict[int, list[str]] = {}
        self._profiles: dict[int, Vec] = {}
        self._index: list[scoring.FolderIndex] = []
        self._placed: dict[str, scoring.PlacedVec] = {}  # key: indexed path
        self._vecs: dict[int, Vec] = {}  # input file id -> vec
        # invalidation (SPEC §7): suggestions carry the versions they were computed at
        self.tree_version = 0  # bumped on folder mkdir/rename/remove
        self.index_version = 0  # bumped every `index_bump_every` moves into one folder
        self._moves_into: dict[int, int] = {}
        self.notify: Callable[[str, list[int]], None] | None = None  # set by the service
        self._stop = threading.Event()
        self._rescore_wanted = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"pipeline-{session_id}",
                                        daemon=True)
        self._rescorer = threading.Thread(target=self._rescore_loop,
                                          name=f"rescore-{session_id}", daemon=True)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._thread.start()
        self._rescorer.start()

    def stop(self) -> None:
        self._stop.set()
        self._rescore_wanted.set()

    def _emit(self, kind: str, file_ids: list[int]) -> None:
        if self.notify is not None and (file_ids or kind == "progress"):
            try:
                self.notify(kind, file_ids)
            except Exception:
                log.exception("notify failed")

    def _degrade(self, e: Exception) -> None:
        log.warning("model provider unavailable, falling back to manual cards: %s", e)
        self.degraded = True
        self.error = str(e)
        self._mark_all_ready_manual()

    def wait(self, timeout: float | None = None) -> bool:
        return self.done.wait(timeout)

    def _run(self) -> None:
        try:
            self._run_inner()
        except Exception as e:
            log.exception("pipeline failed")
            self.error = str(e)
            self._mark_all_ready_manual()
        finally:
            self.done.set()

    # ------------------------------------------------------------------ stages
    def _run_inner(self) -> None:
        with DB(self.engine) as db:
            row = db.get(SessionRow, self.sid)
            assert row is not None
            inp, out = Path(row.input_root), Path(row.output_root)
            folders = db.exec(select(Folder).where(Folder.session_id == self.sid)).all()
            input_files = db.exec(select(File).where(File.session_id == self.sid)).all()
            input_paths = {f.path for f in input_files}
            by_path = {f.path: f for f in folders}

            # 1. index what already lives in the output tree
            indexed: list[IndexedFile] = []
            for item in scan(out, self.settings.scan, self.platform):
                if str(item.path) in input_paths:
                    continue  # sort-in-place: unsorted input isn't ground truth
                if out != inp and inp.is_relative_to(out) and item.path.is_relative_to(inp):
                    continue
                parent = by_path.get(str(item.path.parent))
                if parent is None or parent.is_inbox:
                    continue
                assert parent.id is not None
                indexed.append(IndexedFile(session_id=self.sid, folder_id=parent.id,
                                           path=str(item.path), name=item.path.name,
                                           ext=item.ext))
            for ix in indexed:
                db.add(ix)
            db.commit()
            for ix in indexed:
                db.refresh(ix)

            # 2. fingerprint + extract (outputs first: needed to score inputs)
            pages = self.settings.pipeline.pages
            max_chars = self.settings.pipeline.max_text_chars
            jobs: list[tuple[Path, str, str, str]] = []  # (path, rel, ext, key)
            for ix in indexed:
                jobs.append((Path(ix.path), _rel(Path(ix.path), out), ix.ext, f"ix:{ix.id}"))
            order = sorted(input_files, key=_card_order)
            for f in order:
                jobs.append((Path(f.path), f.rel_path, f.ext, f"f:{f.id}"))
            hashes: dict[str, str] = {}
            for path, _rel_p, _ext, key in jobs:
                try:
                    hashes[key] = content_hash(path)
                except OSError as e:
                    log.warning("hash failed %s: %s", path, e)
            todo = [(p, hashes[k]) for p, _r, _e, k in jobs if k in hashes
                    and db.get(Extraction, hashes[k]) is None]
            todo = list({h: (p, h) for p, h in reversed(todo)}.values())[::-1]
            results: dict[str, ExtractResult] = {}
            if todo:
                workers = self.settings.pipeline.extract_workers or None
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    for (_path, done_h), res in zip(
                        todo,
                        pool.map(extract, [p for p, _ in todo], [max_chars] * len(todo),
                                 [pages] * len(todo)),
                        strict=True,
                    ):
                        results[done_h] = res

            # 2b. dedup features (pHash/dHash, MinHash), cached by content hash
            path_of = {hashes[k]: p for p, _r, _e, k in jobs if k in hashes}
            feat_todo = [h for h in dict.fromkeys(hashes.values())
                         if db.get(Features, h) is None]
            if feat_todo:
                texts, images = [], []
                for h in feat_todo:
                    r = results.get(h)
                    ex = None if r is not None else db.get(Extraction, h)
                    texts.append(r.text if r is not None else (ex.digest_text if ex else ""))
                    images.append(r.is_image if r is not None
                                  else bool(ex and ex.mime.startswith("image/")))
                workers = self.settings.pipeline.extract_workers or None
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    feats = list(pool.map(compute_features, [path_of[h] for h in feat_todo],
                                          texts, images,
                                          [self.settings.dedup] * len(feat_todo)))
                for h, fr in zip(feat_todo, feats, strict=True):
                    db.add(Features(content_hash=h, **fr.model_dump()))
                db.commit()

            # 3. digest (+ OCR / caption for images), cached by content hash
            digests: dict[str, str] = {}
            for path, rel, ext, key in jobs:
                hk = hashes.get(key)
                if hk is None:
                    continue
                cached = db.get(Extraction, hk)
                if cached is not None and hk not in results:
                    digests[key] = cached.digest_text
                    continue
                maybe = results.get(hk)
                if maybe is None:
                    continue
                res = maybe
                ocr_text = caption = ""
                if res.is_image:
                    try:
                        ocr_text = self.platform.ocr(path)
                    except Exception:  # noqa: BLE001
                        ocr_text = ""
                    try:
                        caption = self.provider.caption(path)
                    except ModelUnavailable:
                        caption = ""
                digest = build_digest(path.name, rel, ext, res, caption=caption,
                                      ocr_text=ocr_text)
                digests[key] = digest
                if db.get(Extraction, hk) is None:
                    db.add(Extraction(content_hash=hk, mime=res.mime, digest_text=digest,
                                      metadata_json=json.dumps(res.metadata, default=str),
                                      page_count=res.page_count, ocr_used=bool(ocr_text)))
            for f in input_files:
                fh = hashes.get(f"f:{f.id}")
                if fh:
                    f.content_hash = fh
                    db.add(f)
            for ix in indexed:
                ih = hashes.get(f"ix:{ix.id}")
                if ih:
                    ix.content_hash = ih
                    db.add(ix)
            db.commit()

            # 3b. duplicates (SPEC §5) -> duplicate suggestions; shown before classification
            self._dedup(db, order, indexed, hashes)

            # 4. embed what scoring needs first: folder profiles + the output tree's files
            prof_items: list[tuple[str, str]] = []
            for fo in folders:
                assert fo.id is not None
                ptxt = profile_text(_rel(Path(fo.path), out), fo.description)
                prof_items.append(("folder-profile:" + hashlib.sha1(ptxt.encode()).hexdigest(),
                                   ptxt))
            ix_items = [(hashes[f"ix:{ix.id}"], digests[f"ix:{ix.id}"]) for ix in indexed
                        if f"ix:{ix.id}" in digests]
            try:
                vecs = embed_cached(db, self.provider, prof_items + ix_items,
                                    self.settings.pipeline.embed_batch)
            except ModelUnavailable as e:
                self._degrade(e)
                return

            # 5. folder index
            for fo in folders:
                assert fo.id is not None
                rel = _rel(Path(fo.path), out)
                self._folder_paths[fo.id] = rel
                ptxt = profile_text(rel, fo.description)
                self._profiles[fo.id] = vecs["folder-profile:"
                                             + hashlib.sha1(ptxt.encode()).hexdigest()]
                if not fo.is_inbox:
                    self._specs.append(scoring.FolderSpec(folder_id=fo.id, path=rel,
                                                          parent_id=fo.parent_id))
            for ix in indexed:
                ih2 = hashes.get(f"ix:{ix.id}")
                if ih2 is None or ih2 not in vecs:
                    continue
                self._direct_vecs.setdefault(ix.folder_id, []).append(vecs[ih2])
                self._direct_names.setdefault(ix.folder_id, []).append(ix.name)
                self._placed[ix.path] = scoring.PlacedVec(folder_id=ix.folder_id, vec=vecs[ih2])
            self._rebuild_index()

            # 6. lookahead scheduler (SPEC §7): embed + score inputs in priority order, in
            #    small batches, so the first READY cards appear within seconds. Priority =
            #    distance from the user's cursor in card order: duplicates first, then card
            #    order. Remaining work continues at idle priority until everything is READY.
            dup_ids = {sg.file_id for sg in db.exec(select(Suggestion).where(
                Suggestion.session_id == self.sid, Suggestion.kind == "duplicate")).all()}
            rank = {f.id: i for i, f in enumerate(order)}
            pending = sorted((f for f in order if f.id is not None),
                             key=lambda f: (0 if f.id in dup_ids else 1, rank[f.id]))
            batch = max(1, self.settings.pipeline.embed_batch)
            small = max(1, min(batch, self.settings.pipeline.urgent_batch))
            target = self.settings.pipeline.lookahead + batch
            i = 0
            while i < len(pending):
                if self._stop.is_set():
                    return
                # Lookahead-aware batch size: while the READY buffer ahead of the user is
                # thin, embed small batches (low latency); once it's comfortably above the
                # lookahead window, switch to full batches (throughput).
                buffered = len(db.exec(select(File.id).where(
                    File.session_id == self.sid,
                    col(File.status).in_((FileStatus.ready, FileStatus.skipped)))).all())
                size = small if buffered < target else batch
                chunk = pending[i:i + size]
                i += size
                emb_todo = [(hashes[f"f:{f.id}"], digests[f"f:{f.id}"]) for f in chunk
                            if f"f:{f.id}" in digests]
                try:
                    cvecs = embed_cached(db, self.provider, emb_todo, batch)
                except ModelUnavailable as e:
                    self._degrade(e)
                    return
                ready: list[int] = []
                for f in chunk:
                    assert f.id is not None
                    fh2 = hashes.get(f"f:{f.id}")
                    vec = cvecs.get(fh2) if fh2 else None
                    if vec is not None:
                        self._vecs[f.id] = vec
                    fresh = db.get(File, f.id)
                    if fresh is None or fresh.status != FileStatus.queued:
                        continue
                    if vec is not None:
                        db.add(self._suggest(fresh, vec))
                    fresh.status = FileStatus.ready
                    db.add(fresh)
                    ready.append(f.id)
                db.commit()
                self._emit("ready", ready)

    # ------------------------------------------------------------------ dedup
    def _dedup(self, db: DB, order: list[File], indexed: list[IndexedFile],
               hashes: dict[str, str]) -> None:
        items: list[DedupItem] = []
        for ix in indexed:
            h = hashes.get(f"ix:{ix.id}")
            if h:
                items.append(self._dedup_item(db, f"ix:{ix.id}", "output", Path(ix.path), h))
        for f in order:
            h = hashes.get(f"f:{f.id}")
            if h:
                items.append(self._dedup_item(db, f"f:{f.id}", "input", Path(f.path), h))
        for m in find_duplicates(items, self.settings.dedup):
            fid = int(m.a.split(":")[1])
            db.add(Suggestion(
                session_id=self.sid, file_id=fid, kind="duplicate", confidence=m.similarity,
                tier="duplicate", reason=m.reason,
                candidates_json=json.dumps({"other": m.b, "relation": m.relation,
                                            "similarity": m.similarity, "keep": m.keep}),
            ))
        db.commit()

    @staticmethod
    def _dedup_item(db: DB, key: str, side: str, path: Path, h: str) -> DedupItem:
        ft = db.get(Features, h)
        try:
            st = path.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        return DedupItem(
            key=key, side=side, name=path.name, size=size, mtime=mtime,
            ext=path.suffix.lower().lstrip("."), content_hash=h,
            quick_hash=ft.quick_hash if ft else "", phash=ft.phash if ft else "",
            dhash=ft.dhash if ft else "", width=ft.width if ft else 0,
            height=ft.height if ft else 0, minhash=ft.minhash if ft else b"",
            text_len=ft.text_len if ft else 0,
        )

    # ------------------------------------------------------------------ scoring
    def _rebuild_index(self) -> None:
        self._index = scoring.build_folder_index(
            self._specs, self._direct_vecs, self._direct_names, self._profiles)

    def _suggest(self, f: File, vec: Vec) -> Suggestion:
        assert f.id is not None
        with self._lock:
            cands = scoring.candidates(self._index, self.settings.scoring.leaf_only)
            scored = scoring.score(vec, tokenize_name(Path(f.path).name), f.ext, cands,
                                   list(self._placed.values()), self.settings.scoring)
        tier, conf = scoring.tier(scored, self.settings.tiers)
        top = scored[:5]
        return Suggestion(
            session_id=self.sid, file_id=f.id, kind="classify", confidence=conf, tier=tier,
            candidates_json=json.dumps([{"folder_id": s.folder_id, "path": s.path, "p": s.p,
                                         "signals": s.signals} for s in top]),
            reason=_reason(top), model_version=self.provider.embed_model_id(),
            tree_version=self.tree_version, index_version=self.index_version,
        )

    # ------------------------------------------------------------------ invalidation
    def request_rescore(self) -> None:
        self._rescore_wanted.set()

    def _rescore_loop(self) -> None:
        while not self._stop.is_set():
            self._rescore_wanted.wait()
            if self._stop.is_set():
                return
            self._rescore_wanted.clear()
            try:
                self.rescore_stale()
            except Exception:
                log.exception("rescore failed")

    def rescore_stale(self) -> list[int]:
        """Cheap rescore (vector math only) of READY suggestions computed at older versions.
        Updates in place when the top-3 or tier changed; emits card_updated for those."""
        changed: list[int] = []
        tv, iv = self.tree_version, self.index_version
        with DB(self.engine) as db:
            files = db.exec(select(File).where(
                File.session_id == self.sid,
                col(File.status).in_((FileStatus.ready, FileStatus.skipped)))).all()
            for f in files:
                assert f.id is not None
                vec = self._vecs.get(f.id)
                if vec is None:
                    continue
                old = db.exec(select(Suggestion).where(
                    Suggestion.file_id == f.id, Suggestion.kind == "classify")
                    .order_by(col(Suggestion.id).desc())).first()
                if old is None or (old.tree_version == tv and old.index_version == iv):
                    continue
                new = self._suggest(f, vec)
                top_old = [c["folder_id"] for c in json.loads(old.candidates_json)[:3]]
                top_new = [c["folder_id"] for c in json.loads(new.candidates_json)[:3]]
                if top_old != top_new or old.tier != new.tier:
                    changed.append(f.id)
                old.candidates_json, old.confidence = new.candidates_json, new.confidence
                old.tier, old.reason = new.tier, new.reason
                old.tree_version, old.index_version = new.tree_version, new.index_version
                db.add(old)
            db.commit()
        self._emit("updated", changed)
        return changed

    def on_placed(self, file_id: int, folder_id: int, new_path: str) -> None:
        """A decision moved an input file into an output folder: learn from it (kNN + centroid)."""
        vec = self._vecs.get(file_id)
        if vec is None or folder_id not in self._profiles:
            return
        with self._lock:
            self._placed[new_path] = scoring.PlacedVec(folder_id=folder_id, vec=vec)
            self._direct_vecs.setdefault(folder_id, []).append(vec)
            self._direct_names.setdefault(folder_id, []).append(Path(new_path).name)
            self._rebuild_index()
            n = self._moves_into.get(folder_id, 0) + 1
            self._moves_into[folder_id] = n
            bump = n % max(1, self.settings.pipeline.index_bump_every) == 0
            if bump:
                self.index_version += 1
        if bump:
            self.request_rescore()

    def on_unplaced(self, file_id: int, folder_id: int, old_path: str) -> None:
        vec = self._vecs.get(file_id)
        with self._lock:
            self._placed.pop(old_path, None)
            if vec is not None and folder_id in self._direct_vecs:
                lst = self._direct_vecs[folder_id]
                for i in range(len(lst) - 1, -1, -1):
                    if lst[i] is vec:
                        del lst[i]
                        names = self._direct_names.get(folder_id, [])
                        if Path(old_path).name in names:
                            names.remove(Path(old_path).name)
                        break
                self._rebuild_index()

    def on_folder_created(self, folder: Folder, rel: str) -> None:
        """New (empty) folder: embed its profile so it becomes a candidate (cold start)."""
        assert folder.id is not None
        ptxt = profile_text(rel, folder.description)
        try:
            with DB(self.engine) as db:
                key = "folder-profile:" + hashlib.sha1(ptxt.encode()).hexdigest()
                vec = embed_cached(db, self.provider, [(key, ptxt)], 1)[key]
        except ModelUnavailable:
            return
        with self._lock:
            self._folder_paths[folder.id] = rel
            self._profiles[folder.id] = vec
            self._specs.append(scoring.FolderSpec(folder_id=folder.id, path=rel,
                                                  parent_id=folder.parent_id))
            self._rebuild_index()
            self.tree_version += 1
        self.request_rescore()

    def on_folder_removed(self, folder_id: int) -> None:
        with self._lock:
            self._specs = [s for s in self._specs if s.folder_id != folder_id]
            self._profiles.pop(folder_id, None)
            self._rebuild_index()
            self.tree_version += 1
        self.request_rescore()

    def _mark_all_ready_manual(self) -> None:
        ids: list[int] = []
        with DB(self.engine) as db:
            for f in db.exec(select(File).where(File.session_id == self.sid,
                                                col(File.status) == FileStatus.queued)).all():
                f.status = FileStatus.ready
                db.add(f)
                if f.id is not None:
                    ids.append(f.id)
            db.commit()
        self._emit("ready", ids)


def _card_order(f: File) -> tuple[str, str]:
    p = Path(f.rel_path)
    return (p.parent.as_posix().casefold(), p.name.casefold())


def _reason(top: list[scoring.Scored]) -> str:
    if not top:
        return "No folders to compare against yet"
    s = top[0]
    sig = s.signals
    parts: list[str] = []
    if sig.get("knn", 0) > 0.3:
        parts.append(f"similar to files already in {s.path}")
    elif sig.get("centroid", 0) > 0.3:
        parts.append(f"content resembles {s.path}")
    if sig.get("profile", 0) > 0.3:
        parts.append("fits the folder's name")
    if sig.get("meta", 0) > 0.4:
        parts.append("filename/type match")
    return "; ".join(parts) or f"closest match: {s.path}"
