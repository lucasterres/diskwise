"""Removing things safely: quarantine (default), OS trash, or permanent delete.

quarantine  move into <home or volume>/.diskwise-quarantine/<batch>/ on the SAME volume:
            instant (a rename, no copy), restorable with `diskwise restore`, and frees
            space only when purged. Works for items far larger than the Recycle Bin.
recycle     the OS trash (Windows Recycle Bin, macOS Trash, freedesktop Trash). Refused on
            Windows removable/network drives, where "recycle" silently deletes for good.
delete      permanent. The CLI requires --yes for it.

Every action is appended to <data_dir>/actions.jsonl.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import subprocess
import time
from pathlib import Path

from . import config, safety
from .fsutil import IS_MAC, IS_WIN, data_dir, os_path, tree_size, volume_info

QDIR = ".diskwise-quarantine"


def _log(event: dict) -> None:
    event = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **event}
    try:
        with open(data_dir() / "actions.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _size_of(path: str) -> int:
    p = os_path(path)
    if os.path.isdir(p):
        return tree_size(path, budget_s=120)[0]
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def quarantine_root_for(path: str) -> Path:
    custom = os.environ.get("DISKWISE_QUARANTINE_DIR") or config.load().get("quarantine_dir")
    if custom:
        return Path(os.path.expanduser(custom))
    ap = os.path.abspath(path)
    home = str(Path.home())
    if os.path.normcase(ap).startswith(os.path.normcase(home) + os.sep):
        return Path(home) / QDIR
    if IS_WIN:
        return Path(os.path.splitdrive(ap)[0] + "\\") / QDIR
    # unix: the mount point that holds the path
    cur = Path(ap)
    while not os.path.ismount(str(cur)) and cur != cur.parent:
        cur = cur.parent
    return cur / QDIR


def _same_volume(a: str, b: Path) -> bool:
    try:
        probe = b if b.exists() else b.parent
        return os.stat(os_path(a)).st_dev == os.stat(os_path(str(probe))).st_dev
    except OSError:
        return False


def _rel_from_abs(path: str) -> str:
    ap = os.path.abspath(path)
    drive, rest = os.path.splitdrive(ap)
    drive = drive.replace(":", "").replace("\\", "_").strip("_") or "root"
    return os.path.join(drive, rest.lstrip("\\/"))


def new_batch() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def _quarantine(path: str, batch: str, reason: str) -> dict:
    qroot = quarantine_root_for(path)
    if not _same_volume(path, qroot):
        raise OSError(f"quarantine {qroot} is on another volume; use --mode recycle or delete")
    dest = qroot / batch / _rel_from_abs(path)
    os.makedirs(os_path(str(dest.parent)), exist_ok=True)
    os.rename(os_path(path), os_path(str(dest)))
    manifest = qroot / batch / "manifest.jsonl"
    with open(os_path(str(manifest)), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"original": os.path.abspath(path), "stored": str(dest),
                             "time": time.time(), "reason": reason}, ensure_ascii=False) + "\n")
    if IS_WIN:
        try:  # hide the quarantine root so it does not clutter Explorer
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(qroot), 0x2)
        except Exception:
            pass
    return {"stored": str(dest)}


def _recycle(path: str) -> None:
    try:
        from send2trash import send2trash  # type: ignore
        send2trash(os.path.abspath(path))
        return
    except ImportError:
        pass
    ap = os.path.abspath(path)
    if IS_WIN:
        vi = volume_info(ap)
        if vi.get("drive_type") not in ("fixed", None):
            raise OSError(f"{vi.get('drive_type')} drive has no Recycle Bin; use quarantine or delete")
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 4, 0x10, 0x40, 0x400
        FOF_WANTNUKEWARNING = 0x4000
        op = SHFILEOPSTRUCTW(None, FO_DELETE, ap + "\0", None,
                             FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING,
                             False, None, None)
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc != 0 or op.fAnyOperationsAborted:
            raise OSError(f"Recycle Bin refused {ap} (code {rc}); it may be too big for the bin, use quarantine")
        return
    if IS_MAC:
        script = f'tell application "Finder" to delete POSIX file "{ap}"'
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if out.returncode != 0:
            raise OSError(out.stderr.strip() or "Finder refused")
        return
    # freedesktop trash (home volume only)
    trash = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Trash"
    (trash / "files").mkdir(parents=True, exist_ok=True)
    (trash / "info").mkdir(parents=True, exist_ok=True)
    if not _same_volume(ap, trash):
        raise OSError("item is not on the home volume; use quarantine or delete")
    base = os.path.basename(ap)
    name, i = base, 1
    while (trash / "files" / name).exists() or (trash / "info" / f"{name}.trashinfo").exists():
        i += 1
        name = f"{base}.{i}"
    from urllib.parse import quote
    (trash / "info" / f"{name}.trashinfo").write_text(
        f"[Trash Info]\nPath={quote(ap)}\nDeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n", encoding="utf-8")
    os.rename(ap, trash / "files" / name)


def _on_rm_error(func, path, _exc):
    # read-only files (git objects on Windows) need the flag cleared first
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _delete(path: str) -> None:
    p = os_path(path)
    if os.path.isdir(p) and not os.path.islink(p):
        shutil.rmtree(p, onerror=_on_rm_error)
        if os.path.exists(p):
            raise OSError("some files could not be deleted (in use or permission denied)")
    else:
        try:
            os.remove(p)
        except PermissionError:
            os.chmod(p, stat.S_IWRITE)
            os.remove(p)


def remove(paths: list[str], *, mode: str = "quarantine", reason: str = "", allow_tracked: bool = False,
           dry_run: bool = False, expect: dict | None = None) -> dict:
    """expect: {path: (size, mtime)} from a plan; items that changed since are skipped."""
    batch = new_batch()
    done, skipped = [], []
    total = 0
    for path in paths:
        why = safety.check_removable(path, allow_tracked=allow_tracked)
        if why:
            skipped.append({"path": path, "why": why})
            continue
        if expect and path in expect:
            try:
                st = os.stat(os_path(path))
                exp_size, exp_mtime = expect[path]
                if os.path.isfile(os_path(path)) and (st.st_size != exp_size or abs(st.st_mtime - exp_mtime) > 2):
                    skipped.append({"path": path, "why": "changed since the plan was made"})
                    continue
            except OSError:
                skipped.append({"path": path, "why": "vanished"})
                continue
        size = _size_of(path)
        if dry_run:
            done.append({"path": path, "bytes": size, "mode": mode, "dry_run": True})
            total += size
            continue
        try:
            extra = {}
            if mode == "quarantine":
                extra = _quarantine(path, batch, reason)
            elif mode == "recycle":
                _recycle(path)
            elif mode == "delete":
                _delete(path)
            else:
                raise ValueError(f"unknown mode {mode}")
        except (OSError, ValueError) as exc:
            skipped.append({"path": path, "why": str(exc)})
            continue
        total += size
        rec = {"path": path, "bytes": size, "mode": mode, **extra}
        done.append(rec)
        _log({"action": "remove", "batch": batch, "reason": reason, **rec})
    freed_now = total if mode == "delete" else 0
    return {"batch": batch, "mode": mode, "removed": done, "skipped": skipped, "bytes": total,
            "freed_now": freed_now, "pending_until_purge": total - freed_now, "dry_run": dry_run}


# ------------------------------------------------------------------ quarantine management
def quarantine_roots() -> list[Path]:
    roots = [Path.home() / QDIR]
    custom = os.environ.get("DISKWISE_QUARANTINE_DIR") or config.load().get("quarantine_dir")
    if custom:
        roots.append(Path(os.path.expanduser(custom)))
    if IS_WIN:
        from .fsutil import drives
        roots += [Path(d) / QDIR for d in drives()]
    else:
        from .fsutil import drives
        roots += [Path(d) / QDIR for d in drives()]
    return [r for r in dict.fromkeys(roots) if r.is_dir()]


def list_batches() -> list[dict]:
    out = []
    for root in quarantine_roots():
        for b in sorted(root.iterdir()):
            if not b.is_dir():
                continue
            items = []
            mf = b / "manifest.jsonl"
            if mf.exists():
                for line in mf.read_text(encoding="utf-8").splitlines():
                    try:
                        items.append(json.loads(line))
                    except ValueError:
                        pass
            size = tree_size(str(b), budget_s=30)[0]
            out.append({"batch": b.name, "root": str(root), "items": len(items), "bytes": size,
                        "age_days": round((time.time() - b.stat().st_mtime) / 86400, 1),
                        "originals": [i["original"] for i in items][:10]})
    return out


def _batch_dir(batch: str) -> Path | None:
    for root in quarantine_roots():
        if (root / batch).is_dir():
            return root / batch
    return None


def restore(batch: str) -> dict:
    bdir = _batch_dir(batch)
    if not bdir:
        return {"error": f"batch {batch} not found"}
    restored, failed = [], []
    for line in (bdir / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        orig, stored = rec["original"], rec["stored"]
        if os.path.exists(os_path(orig)):
            failed.append({"path": orig, "why": "something already exists at the original path"})
            continue
        try:
            os.makedirs(os_path(os.path.dirname(orig)), exist_ok=True)
            os.rename(os_path(stored), os_path(orig))
            restored.append(orig)
            _log({"action": "restore", "batch": batch, "path": orig})
        except OSError as exc:
            failed.append({"path": orig, "why": str(exc)})
    if not failed:
        shutil.rmtree(os_path(str(bdir)), onerror=_on_rm_error)
    return {"restored": restored, "failed": failed}


def purge(batch: str | None = None, older_than_days: float | None = None, dry_run: bool = False) -> dict:
    purged, freed = [], 0
    for b in list_batches():
        if batch and b["batch"] != batch:
            continue
        if older_than_days is not None and b["age_days"] < older_than_days:
            continue
        if not batch and older_than_days is None:
            continue
        path = os.path.join(b["root"], b["batch"])
        if not dry_run:
            shutil.rmtree(os_path(path), onerror=_on_rm_error)
            _log({"action": "purge", "batch": b["batch"], "bytes": b["bytes"]})
        purged.append(b["batch"])
        freed += b["bytes"]
    return {"purged": purged, "freed": freed, "dry_run": dry_run}
