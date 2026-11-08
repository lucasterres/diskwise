"""Filesystem helpers: fast bounded walking, size parsing, Windows quirks."""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Windows file attribute bits (stat.st_file_attributes)
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ATTRS = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS

# Directories we never descend into while walking: version control internals and
# OS bookkeeping. Artifact folders (node_modules...) are walked but tagged.
ALWAYS_SKIP_DIRS = {
    ".git", ".hg", ".svn",
    "$RECYCLE.BIN", "System Volume Information", "$Recycle.Bin",
    ".Trash", ".Trashes", ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100",
    ".diskwise-quarantine",
}

_UNITS = {"": 1, "B": 1, "K": 1024, "KB": 1024, "KIB": 1024, "M": 1024**2, "MB": 1024**2,
          "MIB": 1024**2, "G": 1024**3, "GB": 1024**3, "GIB": 1024**3, "T": 1024**4,
          "TB": 1024**4, "TIB": 1024**4}


def parse_size(text: str | int | float) -> int:
    """'500MB' -> bytes. Binary units (1 KB = 1024 B), like Explorer and du."""
    if isinstance(text, (int, float)):
        return int(text)
    m = re.fullmatch(r"\s*([\d.]+)\s*([a-zA-Z]*)\s*", str(text))
    if not m or m.group(2).upper() not in _UNITS:
        raise ValueError(f"cannot parse size: {text!r}")
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def parse_age(text: str | int | float) -> float:
    """'90d' / '6m' / '2y' / '12h' -> seconds."""
    if isinstance(text, (int, float)):
        return float(text) * 86400
    m = re.fullmatch(r"\s*([\d.]+)\s*([hdwmy]?)\s*", str(text).lower())
    if not m:
        raise ValueError(f"cannot parse age: {text!r}")
    mult = {"h": 3600, "d": 86400, "": 86400, "w": 7 * 86400, "m": 30 * 86400, "y": 365 * 86400}
    return float(m.group(1)) * mult[m.group(2)]


def human(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def age_text(seconds: float) -> str:
    d = seconds / 86400
    if d < 1:
        return f"{seconds / 3600:.0f}h"
    if d < 60:
        return f"{d:.0f}d"
    if d < 730:
        return f"{d / 30:.0f}mo"
    return f"{d / 365:.1f}y"


def os_path(p: str | os.PathLike) -> str:
    """Absolute path; on Windows use the \\\\?\\ prefix so >260-char paths work."""
    s = os.path.abspath(os.fspath(p))
    if IS_WIN and not s.startswith("\\\\?\\"):
        if s.startswith("\\\\"):
            return "\\\\?\\UNC\\" + s[2:]
        return "\\\\?\\" + s
    return s


def display_path(p: str) -> str:
    if IS_WIN:
        if p.startswith("\\\\?\\UNC\\"):
            return "\\\\" + p[8:]
        if p.startswith("\\\\?\\"):
            return p[4:]
    return p


def is_link_or_junction(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        if IS_WIN:
            st = entry.stat(follow_symlinks=False)
            return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return True
    return False


def path_is_link(path: str) -> bool:
    try:
        st = os.lstat(os_path(path))
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(IS_WIN and getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def is_cloud_placeholder(st: os.stat_result) -> bool:
    """OneDrive / Dropbox / Google Drive 'online-only' files. Reading them triggers a
    download, deleting them deletes the cloud copy too. Never hash or remove them blindly."""
    return bool(IS_WIN and getattr(st, "st_file_attributes", 0) & CLOUD_ATTRS)


@dataclass
class FileInfo:
    path: str          # display path
    size: int
    mtime: float
    atime: float
    dev: int
    ino: int
    nlink: int
    cloud: bool = False


@dataclass
class WalkStats:
    files: int = 0
    dirs: int = 0
    errors: int = 0
    skipped_links: int = 0
    cloud_files: int = 0
    truncated: bool = False
    seconds: float = 0.0
    error_samples: list = field(default_factory=list)


def walk(root: str, *, skip_dir: Optional[Callable[[str, str], bool]] = None,
         budget_s: Optional[float] = None, stats: Optional[WalkStats] = None,
         include_hidden: bool = True) -> Iterator[tuple[str, list[FileInfo], list[str]]]:
    """Iterative os.scandir walk. Yields (dir_display_path, files, subdir_names).

    Never follows symlinks or junctions (no double counting, no escaping the root).
    `skip_dir(parent, name)` returning True prunes a subdirectory.
    Stops early when `budget_s` is exceeded and sets stats.truncated.
    """
    stats = stats if stats is not None else WalkStats()
    t0 = time.monotonic()
    stack = [os_path(root)]
    while stack:
        if budget_s is not None and time.monotonic() - t0 > budget_s:
            stats.truncated = True
            break
        current = stack.pop()
        files: list[FileInfo] = []
        subdirs: list[str] = []
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError as exc:
            stats.errors += 1
            if len(stats.error_samples) < 5:
                stats.error_samples.append(f"{display_path(current)}: {exc.strerror or exc}")
            continue
        stats.dirs += 1
        for entry in entries:
            name = entry.name
            if not include_hidden and name.startswith("."):
                continue
            if is_link_or_junction(entry):
                stats.skipped_links += 1
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if name in ALWAYS_SKIP_DIRS:
                        continue
                    if skip_dir and skip_dir(display_path(current), name):
                        continue
                    subdirs.append(name)
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    st = entry.stat(follow_symlinks=False)
                    cloud = is_cloud_placeholder(st)
                    if cloud:
                        stats.cloud_files += 1
                    # On Windows DirEntry.stat() leaves st_ino/st_dev/st_nlink at 0. A full
                    # os.stat per file would double the walk time, so callers that need
                    # identity (duplicate finder) call file_identity() on candidates only.
                    files.append(FileInfo(display_path(entry.path), st.st_size, st.st_mtime,
                                          st.st_atime, st.st_dev, st.st_ino, st.st_nlink, cloud))
                    stats.files += 1
            except OSError:
                stats.errors += 1
        yield display_path(current), files, subdirs
    stats.seconds = time.monotonic() - t0


def file_identity(path: str) -> tuple[int, int, int]:
    """(dev, ino, nlink) with a real stat; hardlinks share (dev, ino)."""
    st = os.stat(os_path(path), follow_symlinks=False)
    return st.st_dev, st.st_ino, st.st_nlink


def tree_size(root: str, budget_s: Optional[float] = None) -> tuple[int, int, bool, float]:
    """(bytes, files, truncated, newest_mtime) of a directory tree, hardlinks counted once."""
    stats = WalkStats()
    total = 0
    count = 0
    newest = 0.0
    seen: set[tuple[int, int]] = set()
    for _d, files, _s in walk(root, budget_s=budget_s, stats=stats):
        for f in files:
            if f.nlink > 1:
                key = (f.dev, f.ino)
                if key in seen:
                    continue
                seen.add(key)
            total += f.size
            count += 1
            if f.mtime > newest:
                newest = f.mtime
    return total, count, stats.truncated, newest


def free_space(path: str) -> dict:
    u = shutil.disk_usage(path)
    return {"path": path, "total": u.total, "used": u.used, "free": u.free,
            "free_pct": round(100 * u.free / u.total, 1) if u.total else 0.0}


def drives() -> list[str]:
    if IS_WIN:
        import string
        out = []
        try:
            import ctypes
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            for i, letter in enumerate(string.ascii_uppercase):
                if mask & (1 << i):
                    out.append(f"{letter}:\\")
        except Exception:
            out = [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
        return out
    roots = ["/"]
    for base in ("/Volumes", "/mnt", "/media"):
        if os.path.isdir(base):
            for name in os.listdir(base):
                p = os.path.join(base, name)
                if os.path.ismount(p):
                    roots.append(p)
            if base == "/media":
                for user in os.listdir(base):
                    sub = os.path.join(base, user)
                    if os.path.isdir(sub) and not os.path.ismount(sub):
                        roots += [os.path.join(sub, n) for n in os.listdir(sub)
                                  if os.path.ismount(os.path.join(sub, n))]
    return roots


def volume_info(path: str) -> dict:
    """Filesystem name and drive type. Matters for advice: exFAT/FAT32 cannot use NTFS
    compression or hardlinks; removable and network drives have no Recycle Bin."""
    info = {"fs": None, "drive_type": None}
    if IS_WIN:
        try:
            import ctypes
            root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
            fs_buf = ctypes.create_unicode_buffer(64)
            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root), None, 0, None, None, None, fs_buf, len(fs_buf))
            if ok:
                info["fs"] = fs_buf.value
            dtype = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
            info["drive_type"] = {2: "removable", 3: "fixed", 4: "network", 5: "cdrom",
                                  6: "ramdisk"}.get(dtype, "unknown")
        except Exception:
            pass
    else:
        try:
            best = ""
            with open("/proc/mounts", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 3 and os.path.abspath(path).startswith(parts[1]) \
                            and len(parts[1]) > len(best):
                        best, info["fs"] = parts[1], parts[2]
            info["drive_type"] = "fixed"
        except OSError:
            pass
    return info


def home() -> Path:
    return Path.home()


def data_dir() -> Path:
    """Where Diskwise keeps its logs, hash cache and session snapshots."""
    env = os.environ.get("DISKWISE_HOME") or os.environ.get("CLAUDE_PLUGIN_DATA")
    base = Path(env) if env else Path.home() / ".diskwise"
    base.mkdir(parents=True, exist_ok=True)
    return base
