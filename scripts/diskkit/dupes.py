"""Duplicate files: size bucket -> head+tail hash -> full hash (-> optional byte compare).

Hardlinks are collapsed first (they share storage, so removing one frees nothing).
Online-only cloud files are skipped (hashing would download them).
Hashes are cached in SQLite keyed by (path, size, mtime) so re-runs are fast.
"""

from __future__ import annotations

import filecmp
import hashlib
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import rules
from .fsutil import IS_WIN, WalkStats, data_dir, file_identity, os_path, walk

CHUNK = 1024 * 1024
EDGE = 64 * 1024

# paths that look like "the copy" rather than "the original"
_COPY_HINTS = ("downloads", "temp", "tmp", "cache", "backup", "bak", "old", "copy", "archive",
               "trash", "recycle", "duplicate", "dupes", "export", "verify", "scratch",
               "worktree", "handoff", "_moved")


@dataclass
class DupeGroup:
    size: int
    digest: str
    paths: list            # keeper first
    mtimes: list
    reasons: list = field(default_factory=list)

    @property
    def reclaimable(self) -> int:
        return self.size * (len(self.paths) - 1)


class HashCache:
    def __init__(self, enabled: bool = True):
        self.conn = None
        if not enabled:
            return
        try:
            self.conn = sqlite3.connect(str(data_dir() / "hashes.sqlite"))
            self.conn.execute("CREATE TABLE IF NOT EXISTS h (path TEXT, size INT, mtime REAL, "
                              "kind TEXT, digest TEXT, PRIMARY KEY(path, kind))")
        except sqlite3.Error:
            self.conn = None

    def get(self, path, size, mtime, kind):
        if not self.conn:
            return None
        row = self.conn.execute("SELECT size, mtime, digest FROM h WHERE path=? AND kind=?",
                                (path, kind)).fetchone()
        if row and row[0] == size and abs(row[1] - mtime) < 1e-3:
            return row[2]
        return None

    def put(self, path, size, mtime, kind, digest):
        if self.conn:
            self.conn.execute("INSERT OR REPLACE INTO h VALUES (?,?,?,?,?)",
                              (path, size, mtime, kind, digest))

    def close(self):
        if self.conn:
            self.conn.commit()
            self.conn.close()


def edge_hash(path: str, size: int) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(os_path(path), "rb") as fh:
        h.update(fh.read(EDGE))
        if size > 2 * EDGE:
            fh.seek(-EDGE, os.SEEK_END)
            h.update(fh.read(EDGE))
    return h.hexdigest()


def full_hash(path: str) -> str:
    h = hashlib.blake2b(digest_size=32)
    with open(os_path(path), "rb") as fh:
        while True:
            buf = fh.read(CHUNK)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def keeper_score(path: str, mtime: float, prefer: list[str]) -> tuple:
    """Lower sorts first = better keeper. Preferred folders win, then names without copy/version
    markers, then paths without copy-ish folders, then the oldest file, then the shortest path."""
    low = os.path.normcase(path)
    pref = 0 if any(low.startswith(os.path.normcase(os.path.abspath(p))) for p in prefer) else 1
    name = os.path.basename(path)
    marker = 1 if rules.has_version_marker(name) else 0
    parts = low.replace("\\", "/").split("/")
    parts[-1] = os.path.splitext(parts[-1])[0]   # the file's own stem counts too
    hint = sum(1 for part in parts for h in _COPY_HINTS if h in part)
    return (pref, marker, hint, mtime, len(path), path)


def find_duplicates(roots: list[str], *, min_size: int = 1024 * 1024, prefer: list[str] = (),
                    skip_artifacts: bool = True, use_cache: bool = True, verify_bytes: bool = False,
                    budget_s: float | None = None, progress=None) -> tuple[list[DupeGroup], dict]:
    t0 = time.monotonic()
    stats = WalkStats()
    by_size: dict[int, list] = defaultdict(list)
    skipped_cloud = 0

    def skip_dir(parent, name):
        return skip_artifacts and rules.match_artifact(parent, name) is not None

    seen_paths = set()
    for root in roots:
        for _d, files, _s in walk(root, skip_dir=skip_dir, budget_s=budget_s, stats=stats):
            for f in files:
                if f.size < min_size:
                    continue
                if f.cloud:
                    skipped_cloud += 1
                    continue
                key = os.path.normcase(f.path)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                by_size[f.size].append(f)

    cache = HashCache(use_cache)
    candidates = {s: fs for s, fs in by_size.items() if len(fs) > 1}
    groups: list[DupeGroup] = []
    hashed_bytes = 0
    hardlinked = 0
    try:
        for size, files in sorted(candidates.items(), key=lambda kv: -kv[0]):
            # collapse hardlinks (needs a real stat on Windows)
            uniq = {}
            for f in files:
                try:
                    dev, ino, _n = file_identity(f.path) if IS_WIN else (f.dev, f.ino, f.nlink)
                except OSError:
                    continue
                k = (dev, ino) if ino else ("p", f.path)
                if k in uniq:
                    hardlinked += 1
                    continue
                uniq[k] = f
            files = list(uniq.values())
            if len(files) < 2:
                continue
            # stage 2: head + tail
            by_edge = defaultdict(list)
            for f in files:
                try:
                    d = cache.get(f.path, f.size, f.mtime, "edge")
                    if d is None:
                        d = edge_hash(f.path, f.size)
                        cache.put(f.path, f.size, f.mtime, "edge", d)
                        hashed_bytes += min(f.size, 2 * EDGE)
                    by_edge[d].append(f)
                except OSError:
                    continue
            # stage 3: full hash
            for same in by_edge.values():
                if len(same) < 2:
                    continue
                if size <= 2 * EDGE:
                    by_full = {"edge": same}
                else:
                    by_full = defaultdict(list)
                    for f in same:
                        try:
                            d = cache.get(f.path, f.size, f.mtime, "full")
                            if d is None:
                                d = full_hash(f.path)
                                cache.put(f.path, f.size, f.mtime, "full", d)
                                hashed_bytes += f.size
                            by_full[d].append(f)
                        except OSError:
                            continue
                        if progress:
                            progress(hashed_bytes)
                for digest, members in by_full.items():
                    if len(members) < 2:
                        continue
                    members.sort(key=lambda f: keeper_score(f.path, f.mtime, list(prefer)))
                    if verify_bytes:
                        keeper = members[0]
                        members = [keeper] + [m for m in members[1:]
                                              if filecmp.cmp(os_path(keeper.path), os_path(m.path),
                                                             shallow=False)]
                        if len(members) < 2:
                            continue
                    groups.append(DupeGroup(size, digest if digest != "edge" else "small",
                                            [m.path for m in members], [m.mtime for m in members]))
    finally:
        cache.close()
    groups.sort(key=lambda g: -g.reclaimable)
    meta = {"files_seen": stats.files, "candidates": sum(len(v) for v in candidates.values()),
            "hashed_bytes": hashed_bytes, "hardlinks_collapsed": hardlinked,
            "cloud_skipped": skipped_cloud, "truncated": stats.truncated,
            "errors": stats.errors, "seconds": round(time.monotonic() - t0, 1)}
    return groups, meta


def redundant_folders(groups: list[DupeGroup], min_bytes: int = 50 * 1024**2) -> list[dict]:
    """Folders whose duplicated content lives in exactly one other folder: likely whole-folder
    copies (e.g. 'Final_2025' copied from 'Final_Organized'). Measured on the folder's direct
    files, so the copy can be removed as a unit after a look."""
    pair_bytes: dict[tuple[str, str], int] = defaultdict(int)
    folder_dupe_bytes: dict[str, int] = defaultdict(int)
    for g in groups:
        folders = [os.path.dirname(p) for p in g.paths]
        for i, a in enumerate(folders):
            for j, b in enumerate(folders):
                if i != j and a != b:
                    pair_bytes[(a, b)] += g.size
            folder_dupe_bytes[a] += g.size
    out = []
    for (a, b), shared in pair_bytes.items():
        if shared < min_bytes:
            continue
        try:
            direct = sum(e.stat(follow_symlinks=False).st_size for e in os.scandir(os_path(a))
                         if e.is_file(follow_symlinks=False))
        except OSError:
            continue
        if direct and shared / direct >= 0.9:
            out.append({"folder": a, "copy_of": b, "shared_bytes": shared, "folder_bytes": direct,
                        "share": round(shared / direct, 3)})
    out.sort(key=lambda d: -d["shared_bytes"])
    # keep one direction per pair: the folder reported is the worse keeper
    by_pair: dict[tuple, list] = defaultdict(list)
    for d in out:
        by_pair[tuple(sorted((d["folder"], d["copy_of"])))].append(d)
    final = []
    for _key, ds in by_pair.items():
        def score(d):
            try:
                mt = os.stat(os_path(d["folder"])).st_mtime
            except OSError:
                mt = 0.0
            return keeper_score(d["folder"], -mt, [])  # newer folder = more likely the copy
        final.append(max(ds, key=score) if len(ds) > 1 else ds[0])
    final.sort(key=lambda d: -d["shared_bytes"])
    return final
