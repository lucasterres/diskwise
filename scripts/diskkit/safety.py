"""Guards every destructive action passes through.

A path may be removed only when:
  * it is not a protected system/user location (or inside one marked never-touch),
  * it is not the root of a drive or the home folder itself,
  * it is not a symlink/junction (we remove real things, never through links),
  * it holds no git-tracked file (unless explicitly allowed),
  * it is not an online-only cloud placeholder (deleting it deletes the cloud copy).
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path

from . import config
from .fsutil import IS_MAC, IS_WIN, is_cloud_placeholder, os_path, path_is_link


def _norm(p: str | os.PathLike) -> str:
    s = os.path.normcase(os.path.abspath(os.fspath(p)))
    return s.rstrip("\\/") or s


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def protected_roots() -> list[str]:
    """Locations that are never removed and never removed *inside* of."""
    home = str(Path.home())
    roots: list[str] = []
    if IS_WIN:
        sysroot = _env("SystemRoot", r"C:\Windows")
        roots += [sysroot, _env("ProgramFiles", r"C:\Program Files"),
                  _env("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                  os.path.join(_env("SystemDrive", "C:") + "\\", "Recovery"),
                  os.path.join(_env("SystemDrive", "C:") + "\\", "Boot"),
                  os.path.join(home, "AppData", "Roaming", "Microsoft", "Credentials"),
                  os.path.join(home, "AppData", "Local", "Microsoft", "Credentials"),
                  os.path.join(home, "AppData", "Roaming", "Microsoft", "Protect"),
                  os.path.join(home, "AppData", "Local", "Microsoft", "WinGet", "Packages"),
                  os.path.join(home, "AppData", "Roaming", "Claude", "vm_bundles")]
    elif IS_MAC:
        roots += ["/System", "/Library", "/Applications", "/bin", "/sbin", "/usr", "/etc",
                  "/private/var/db", os.path.join(home, "Library", "Keychains"),
                  os.path.join(home, "Library", "Mobile Documents")]
    else:
        roots += ["/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin", "/sys",
                  "/usr", "/var/lib", "/snap"]
    roots += [os.path.join(home, ".ssh"), os.path.join(home, ".gnupg"),
              os.path.join(home, ".aws"), os.path.join(home, ".kube"),
              os.path.join(home, ".config", "gh")]
    roots += [os.path.expanduser(p) for p in config.load().get("never_touch", [])]
    return [r for r in roots if r]


def exact_protected() -> list[str]:
    """Folders that may be cleaned inside, but never removed as a whole."""
    home = Path.home()
    out = [str(home)]
    for sub in ("Documents", "Desktop", "Downloads", "Pictures", "Music", "Videos", "Movies",
                ".claude", ".config", ".local", ".cache", "AppData", "AppData/Local",
                "AppData/Roaming", "Library", "Library/Caches", "OneDrive", "Dropbox",
                "Google Drive", "iCloud Drive"):
        out.append(str(home / sub))
    if IS_WIN:
        out += [f"{chr(c)}:\\" for c in range(ord("A"), ord("Z") + 1)]
        out += [_env("ProgramData", r"C:\ProgramData"), _env("TEMP", ""), _env("TMP", "")]
    else:
        out += ["/", "/home", "/Users", "/Volumes", "/mnt", "/media", "/tmp", "/var", "/opt"]
    return [o for o in out if o]


def why_protected(path: str) -> str | None:
    p = _norm(path)
    for e in exact_protected():
        if p == _norm(e):
            return f"{path} is a top-level folder (clean inside it, never remove it)"
    for r in protected_roots():
        rn = _norm(r)
        if p == rn or p.startswith(rn + os.sep):
            return f"{path} is inside protected location {r}"
    for pattern in config.load().get("never_touch_globs", []):
        if fnmatch.fnmatch(p, os.path.normcase(os.path.expanduser(pattern))):
            return f"{path} matches never_touch_globs pattern {pattern}"
    return None


def git_root(path: str) -> str | None:
    cur = Path(os.path.abspath(path))
    if cur.is_file():
        cur = cur.parent
    for parent in [cur, *cur.parents]:
        if (parent / ".git").exists():
            return str(parent)
    return None


def git_tracked_inside(path: str) -> list[str]:
    """Tracked files at or below `path` (first 5). Empty when not in a repo or git absent."""
    root = git_root(path)
    if not root:
        return []
    rel = os.path.relpath(os.path.abspath(path), root)
    if rel == ".":
        rel = ""
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z", "--", rel or "."],
                             capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    names = [n for n in out.stdout.decode("utf-8", "replace").split("\0") if n]
    return names[:5]


def contains_cloud_placeholders(path: str, limit: int = 2000) -> bool:
    if not IS_WIN:
        return False
    target = os_path(path)
    try:
        if os.path.isfile(target):
            return is_cloud_placeholder(os.stat(target, follow_symlinks=False))
        seen = 0
        for dirpath, _dirs, files in os.walk(target):
            for f in files:
                seen += 1
                if seen > limit:
                    return False
                try:
                    if is_cloud_placeholder(os.stat(os.path.join(dirpath, f), follow_symlinks=False)):
                        return True
                except OSError:
                    pass
    except OSError:
        return False
    return False


def check_removable(path: str, *, allow_tracked: bool = False) -> str | None:
    """None when `path` may be removed, else the reason it may not."""
    if not os.path.lexists(os_path(path)):
        return "does not exist"
    reason = why_protected(path)
    if reason:
        return reason
    if path_is_link(path):
        return "is a symlink/junction; remove the link target deliberately instead"
    if os.path.basename(os.path.abspath(path)) == ".git":
        return "is a .git folder"
    if not allow_tracked:
        tracked = git_tracked_inside(path)
        if tracked:
            return f"contains git-tracked files (e.g. {tracked[0]}); use git rm or --allow-tracked"
    if contains_cloud_placeholders(path):
        return "contains online-only cloud files (removing them deletes the cloud copy too)"
    return None
