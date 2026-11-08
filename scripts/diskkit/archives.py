"""Archives whose contents already sit extracted next to them.

A zip that was downloaded, extracted and kept doubles the space. For each archive we look
for its members on disk in the usual extraction spots and compare sizes (and, with
verify_crc, the CRC-32 of each extracted zip member). 100% present = one of the two copies
can go, usually the archive.
"""

from __future__ import annotations

import os
import tarfile
import zipfile
import zlib

from .fsutil import os_path


def _members(path: str, max_tar_bytes: int) -> list[tuple[str, int, int | None]] | None:
    """[(relative name, size, crc32 or None)] for regular files, or None if unreadable."""
    low = path.lower()
    try:
        if low.endswith(".zip"):
            with zipfile.ZipFile(os_path(path)) as z:
                return [(i.filename, i.file_size, i.CRC) for i in z.infolist() if not i.is_dir()]
        if low.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
            if os.path.getsize(os_path(path)) > max_tar_bytes and not low.endswith(".tar"):
                return None  # compressed tar must be fully decompressed to list it
            with tarfile.open(os_path(path)) as t:
                return [(m.name, m.size, None) for m in t.getmembers() if m.isfile()]
    except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError, ValueError):
        return None
    return None


def _crc32(path: str) -> int:
    crc = 0
    with open(os_path(path), "rb") as fh:
        while True:
            buf = fh.read(1024 * 1024)
            if not buf:
                break
            crc = zlib.crc32(buf, crc)
    return crc & 0xFFFFFFFF


def _strip_ext(name: str) -> str:
    low = name.lower()
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tar", ".zip"):
        if low.endswith(ext):
            return name[: -len(ext)]
    return os.path.splitext(name)[0]


def check_archive(path: str, *, verify_crc: bool = False, max_tar_bytes: int = 2 * 1024**3) -> dict | None:
    members = _members(path, max_tar_bytes)
    if not members:
        return None
    folder = os.path.dirname(path)
    stem = _strip_ext(os.path.basename(path))
    candidates = [folder, os.path.join(folder, stem)]
    best = None
    for base in candidates:
        if not os.path.isdir(base):
            continue
        present = present_bytes = mismatched = 0
        total_bytes = 0
        for name, size, crc in members:
            total_bytes += size
            target = os.path.join(base, *name.replace("\\", "/").split("/"))
            try:
                st = os.stat(os_path(target))
            except OSError:
                continue
            if st.st_size != size:
                mismatched += 1
                continue
            if verify_crc and crc is not None and size and _crc32(target) != crc:
                mismatched += 1
                continue
            present += 1
            present_bytes += size
        frac = present / len(members)
        if best is None or frac > best["fraction"]:
            best = {"archive": path, "extracted_to": base, "members": len(members),
                    "present": present, "mismatched": mismatched, "fraction": round(frac, 4),
                    "archive_bytes": os.path.getsize(os_path(path)), "content_bytes": total_bytes,
                    "present_bytes": present_bytes, "crc_verified": verify_crc}
    return best


def find_extracted(archives: list[str], *, verify_crc: bool = False, min_fraction: float = 0.5) -> list[dict]:
    out = []
    for a in archives:
        r = check_archive(a, verify_crc=verify_crc)
        if r and r["fraction"] >= min_fraction:
            out.append(r)
    out.sort(key=lambda r: (-r["fraction"], -r["archive_bytes"]))
    return out
