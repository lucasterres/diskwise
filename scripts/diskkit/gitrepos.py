"""Git repositories: .git weight, loose objects, prunable worktrees, LFS cache."""

from __future__ import annotations

import os
import shutil
import subprocess

from .fsutil import tree_size


def _git(repo: str, *args: str, timeout: int = 60) -> str | None:
    if not shutil.which("git"):
        return None
    try:
        out = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                             timeout=timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def inspect(repo: str) -> dict:
    gitdir = os.path.join(repo, ".git")
    info = {"repo": repo, "git_bytes": 0, "loose_bytes": 0, "loose_count": 0, "pack_bytes": 0,
            "garbage_bytes": 0, "lfs_bytes": 0, "worktrees": [], "prunable_worktrees": [],
            "suggestions": []}
    if os.path.isfile(gitdir):
        info["note"] = "this is a linked worktree (its objects live in the main repo)"
        return info
    info["git_bytes"] = tree_size(gitdir, budget_s=30)[0]
    lfs = os.path.join(gitdir, "lfs")
    if os.path.isdir(lfs):
        info["lfs_bytes"] = tree_size(lfs, budget_s=20)[0]
    out = _git(repo, "count-objects", "-v")
    if out:
        kv = dict(line.split(": ", 1) for line in out.splitlines() if ": " in line)
        info["loose_count"] = int(kv.get("count", 0))
        info["loose_bytes"] = int(kv.get("size", 0)) * 1024
        info["pack_bytes"] = int(kv.get("size-pack", 0)) * 1024
        info["garbage_bytes"] = int(kv.get("size-garbage", 0)) * 1024
    wt = _git(repo, "worktree", "list", "--porcelain")
    if wt:
        cur = {}
        for line in wt.splitlines() + [""]:
            if not line.strip():
                if cur:
                    info["worktrees"].append(cur)
                    if "prunable" in cur:
                        info["prunable_worktrees"].append(cur.get("worktree"))
                cur = {}
                continue
            k, _, v = line.partition(" ")
            cur[k] = v or True
    if info["loose_bytes"] > 200 * 1024**2:
        info["suggestions"].append(
            "git gc  (packs loose objects; unreachable ones younger than 2 weeks are kept, run again later or "
            "`git gc --prune=now` once you are sure nothing needs recovering)")
    if info["garbage_bytes"] > 0:
        info["suggestions"].append("git gc --prune=now  (removes garbage/tmp_obj files left by crashed git commands)")
    if info["prunable_worktrees"]:
        info["suggestions"].append("git worktree prune  (worktree folders already deleted)")
    if len(info["worktrees"]) > 3:
        info["suggestions"].append(
            f"{len(info['worktrees']) - 1} linked worktrees: remove merged ones with `git worktree remove <path>`")
    if info["lfs_bytes"] > 500 * 1024**2:
        info["suggestions"].append("git lfs prune  (drops LFS objects not needed by recent commits)")
    return info


def survey(repos: list[str]) -> list[dict]:
    out = [inspect(r) for r in repos]
    out.sort(key=lambda d: -d["git_bytes"])
    return out
