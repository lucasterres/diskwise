"""One bounded walk that collects everything the other reports need."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import rules
from .fsutil import FileInfo, WalkStats, tree_size, walk


@dataclass
class Artifact:
    path: str
    ecosystem: str
    regen: str
    tier: int
    size: int
    files: int
    newest: float           # newest mtime inside (how recently the project was built)
    project_mtime: float    # mtime of the marker's folder (proxy for last activity)
    truncated: bool = False


@dataclass
class VersionFamily:
    folder: str
    key: str
    members: list           # [(path, size, mtime)] newest first
    is_dir: bool = False

    @property
    def reclaimable(self) -> int:
        return sum(m[1] for m in self.members[1:])


@dataclass
class Analysis:
    root: str
    total: int = 0
    files: int = 0
    stats: WalkStats = field(default_factory=WalkStats)
    dir_bytes: dict = field(default_factory=dict)       # cumulative, relative depth <= max_depth
    top_files: list = field(default_factory=list)       # FileInfo
    ext_bytes: dict = field(default_factory=dict)
    ext_count: dict = field(default_factory=dict)
    artifacts: list = field(default_factory=list)
    junk: list = field(default_factory=list)            # (kind, FileInfo)
    families: list = field(default_factory=list)
    scratch_dirs: list = field(default_factory=list)    # (path, bytes)
    empty_dirs: list = field(default_factory=list)
    zero_files: int = 0
    archives: list = field(default_factory=list)        # FileInfo
    git_repos: list = field(default_factory=list)
    big_files: list = field(default_factory=list)       # FileInfo >= big threshold (for compress/old)
    cloud_files: int = 0
    seconds: float = 0.0


ARCHIVE_EXTS = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".7z", ".rar")


def analyze(root: str, *, budget_s: float | None = 300, top_n: int = 30, max_depth: int = 3,
            big_threshold: int = 100 * 1024**2, artifact_budget_s: float = 20,
            include_artifacts: bool = True) -> Analysis:
    t0 = time.monotonic()
    root = os.path.abspath(root)
    a = Analysis(root=root)
    direct: dict[str, int] = defaultdict(int)
    artifacts_found: list[tuple[str, str, rules.ArtifactRule]] = []
    subdirs_of: dict[str, list[str]] = {}
    seen_inodes: set = set()
    git_parents: list[str] = []

    def skip_dir(parent: str, name: str) -> bool:
        # Artifact folders are measured separately (tree_size) instead of walked file by
        # file, so one node_modules does not flood the top-files and version reports.
        if include_artifacts:
            rule = rules.match_artifact(parent, name)
            if rule:
                artifacts_found.append((parent, name, rule))
                return True
        return False

    top: list[FileInfo] = []
    for dirpath, files, subdirs in walk(root, skip_dir=skip_dir, budget_s=budget_s, stats=a.stats):
        if os.path.isdir(os.path.join(dirpath, ".git")):
            git_parents.append(dirpath)
        subdirs_of[dirpath] = subdirs
        if not files and not subdirs:
            a.empty_dirs.append(dirpath)
        by_key: dict[tuple, list] = defaultdict(list)
        for f in files:
            if f.nlink > 1:
                k = (f.dev, f.ino)
                if k in seen_inodes:
                    continue
                seen_inodes.add(k)
            if f.cloud:
                a.cloud_files += 1
            a.files += 1
            a.total += f.size
            direct[dirpath] += f.size
            name = f.path.rsplit(os.sep, 1)[-1]
            stem, ext = rules.split_name(name)
            ext_l = ext.lower() or "(none)"
            a.ext_bytes[ext_l] = a.ext_bytes.get(ext_l, 0) + f.size
            a.ext_count[ext_l] = a.ext_count.get(ext_l, 0) + 1
            if f.size == 0:
                a.zero_files += 1
            kind = rules.junk_kind(name)
            if kind:
                a.junk.append((kind, f))
            if name.lower().endswith(ARCHIVE_EXTS):
                a.archives.append(f)
            if f.size >= big_threshold:
                a.big_files.append(f)
            if len(top) < top_n or f.size > top[-1].size:
                top.append(f)
                top.sort(key=lambda x: -x.size)
                del top[top_n:]
            by_key[rules.version_key(name)].append(f)
        for key, members in by_key.items():
            if len(members) < 2:
                continue
            names = [m.path.rsplit(os.sep, 1)[-1] for m in members]
            if not any(rules.has_version_marker(n) for n in names) or rules.looks_like_series(names):
                continue
            ms = sorted(((m.path, m.size, m.mtime) for m in members),
                        key=lambda t: (int(t[2] // 60), rules.version_rank(os.path.basename(t[0]))),
                        reverse=True)
            a.families.append(VersionFamily(dirpath, key[0] + key[1], ms))
    a.top_files = top
    a.git_repos = git_parents
    a = _finish(a, direct, subdirs_of, artifacts_found, max_depth, artifact_budget_s)
    a.seconds = time.monotonic() - t0
    return a


def _finish(a: Analysis, direct, subdirs_of, artifacts_found, max_depth, artifact_budget_s):
    root = a.root
    seen = set()
    for parent, name, rule in artifacts_found:
        path = os.path.join(parent, name)
        if path in seen:
            continue
        seen.add(path)
        size, files, truncated, newest = tree_size(path, budget_s=artifact_budget_s)
        try:
            pm = os.stat(parent).st_mtime
        except OSError:
            pm = 0.0
        a.artifacts.append(Artifact(path, rule.ecosystem, rule.regen, rule.tier, size, files,
                                    newest, pm, truncated))
        direct[path] += size
        a.total += size
        a.files += files
    a.artifacts.sort(key=lambda x: -x.size)

    # cumulative sizes up to max_depth below root
    cum: dict[str, int] = defaultdict(int)
    root_parts = len(root.rstrip(os.sep).split(os.sep))
    for d, size in direct.items():
        parts = d.rstrip(os.sep).split(os.sep)
        for depth in range(root_parts, min(len(parts), root_parts + max_depth) + 1):
            cum[os.sep.join(parts[:depth])] += size
    a.dir_bytes = dict(cum)

    # folder version families: siblings like maps_v2 / maps_v3 / maps_final
    for parent, kids in subdirs_of.items():
        groups: dict[str, list[str]] = defaultdict(list)
        for k in kids:
            groups[rules.normalize_stem(k)].append(k)
        for key, names in groups.items():
            if len(names) < 2 or not any(rules.has_version_marker(n) for n in names) \
                    or rules.looks_like_series(names):
                continue
            members = []
            for n in names:
                p = os.path.join(parent, n)
                size = cum.get(p)
                if size is None:
                    size = tree_size(p, budget_s=10)[0]
                try:
                    mt = os.stat(p).st_mtime
                except OSError:
                    mt = 0.0
                members.append((p, size, mt))
            members.sort(key=lambda t: (int(t[2] // 60), rules.version_rank(os.path.basename(t[0]))),
                         reverse=True)
            a.families.append(VersionFamily(parent, key + "/", members, is_dir=True))
        for k in kids:
            if k.lower() in rules.SCRATCH_DIR_NAMES:
                p = os.path.join(parent, k)
                size = cum.get(p) or tree_size(p, budget_s=10)[0]
                if size >= 10 * 1024**2:
                    a.scratch_dirs.append((p, size))
    a.families.sort(key=lambda f: -f.reclaimable)
    a.scratch_dirs.sort(key=lambda t: -t[1])
    return a
