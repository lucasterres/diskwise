"""Verified copies: hash while copying, re-read the copy, compare, write a manifest.

`backup SRC DEST` copies SRC into DEST/<name of SRC>. Files already present with the same
size and mtime are skipped (re-runs are incremental). With `--move`, the source is removed
only after every file verified, and through the normal guarded remove().

For real, versioned, deduplicated backups use restic, kopia or borg; this command is for
"copy this to the external drive / archive mount, prove it arrived, then free the space".
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import time

from .fsutil import human, os_path, walk

CHUNK = 4 * 1024 * 1024


def _hash_file(path: str) -> str:
    h = hashlib.blake2b(digest_size=32)
    with open(os_path(path), "rb") as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _copy_hashing(src: str, dst: str) -> str:
    h = hashlib.blake2b(digest_size=32)
    os.makedirs(os_path(os.path.dirname(dst)), exist_ok=True)
    tmp = dst + ".diskwise-part"
    with open(os_path(src), "rb") as fi, open(os_path(tmp), "wb") as fo:
        while True:
            b = fi.read(CHUNK)
            if not b:
                break
            h.update(b)
            fo.write(b)
        fo.flush()
        os.fsync(fo.fileno())
    shutil.copystat(os_path(src), os_path(tmp))
    os.replace(os_path(tmp), os_path(dst))
    return h.hexdigest()


def plan_size(src: str, exclude: list[str]) -> tuple[int, int]:
    total = count = 0
    if os.path.isfile(os_path(src)):
        return os.path.getsize(os_path(src)), 1
    for _d, files, _s in walk(src):
        for f in files:
            if not any(fnmatch.fnmatch(os.path.basename(f.path), e) for e in exclude):
                total += f.size
                count += 1
    return total, count


def backup(src: str, dest_root: str, *, exclude: list[str] = (), verify: str = "hash",
           dry_run: bool = False, progress=None) -> dict:
    src = os.path.abspath(src)
    name = os.path.basename(src.rstrip("\\/")) or "root"
    dest = os.path.join(os.path.abspath(dest_root), name)
    total, count = plan_size(src, list(exclude))
    free = shutil.disk_usage(os.path.abspath(dest_root) if os.path.exists(dest_root)
                             else os.path.dirname(os.path.abspath(dest_root))).free
    result = {"source": src, "dest": dest, "files": count, "bytes": total, "dest_free": free,
              "copied": 0, "skipped_same": 0, "verified": 0, "failed": [], "dry_run": dry_run}
    if total > free:
        result["error"] = f"not enough space at destination: need {human(total)}, free {human(free)}"
        return result
    if dry_run:
        return result
    manifest = []
    if os.path.isfile(os_path(src)):
        pairs = [(src, os.path.join(os.path.abspath(dest_root), name))]
        dest = pairs[0][1]
        result["dest"] = dest
    else:
        pairs = []
        for _d, files, _s in walk(src):
            for f in files:
                if any(fnmatch.fnmatch(os.path.basename(f.path), e) for e in exclude):
                    continue
                rel = os.path.relpath(f.path, src)
                pairs.append((f.path, os.path.join(dest, rel)))
    done_bytes = 0
    for s, d in pairs:
        try:
            st = os.stat(os_path(s))
            if os.path.exists(os_path(d)):
                dt = os.stat(os_path(d))
                if dt.st_size == st.st_size and abs(dt.st_mtime - st.st_mtime) < 2:
                    digest = _hash_file(s) if verify == "hash" else None
                    if verify == "hash" and _hash_file(d) != digest:
                        raise OSError("existing copy differs in content")
                    result["skipped_same"] += 1
                    manifest.append({"rel": os.path.relpath(d, dest) if d != dest else name,
                                     "bytes": st.st_size, "blake2b": digest})
                    result["verified"] += 1
                    continue
            digest = _copy_hashing(s, d)
            result["copied"] += 1
            if verify == "hash":
                if _hash_file(d) != digest:
                    raise OSError("hash mismatch after copy")
            elif os.path.getsize(os_path(d)) != st.st_size:
                raise OSError("size mismatch after copy")
            result["verified"] += 1
            manifest.append({"rel": os.path.relpath(d, dest) if d != dest else name,
                             "bytes": st.st_size, "blake2b": digest})
        except OSError as exc:
            result["failed"].append({"path": s, "why": str(exc)})
        done_bytes += os.path.getsize(os_path(s)) if os.path.exists(os_path(s)) else 0
        if progress:
            progress(done_bytes, total)
    mpath = os.path.join(os.path.dirname(dest) if os.path.isfile(os_path(dest)) else dest,
                         f".diskwise-manifest-{time.strftime('%Y%m%d-%H%M%S')}.json")
    try:
        with open(os_path(mpath), "w", encoding="utf-8") as fh:
            json.dump({"source": src, "dest": dest, "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "algorithm": "blake2b-256" if verify == "hash" else "size", "files": manifest},
                      fh, indent=1, ensure_ascii=False)
        result["manifest"] = mpath
    except OSError as exc:
        result["failed"].append({"path": mpath, "why": f"manifest not written: {exc}"})
    result["ok"] = not result["failed"] and result["verified"] == len(pairs)
    return result


def verify_manifest(manifest_path: str) -> dict:
    with open(os_path(manifest_path), encoding="utf-8") as fh:
        m = json.load(fh)
    dest = m["dest"]
    base = dest if os.path.isdir(os_path(dest)) else os.path.dirname(dest)
    bad, ok = [], 0
    for rec in m["files"]:
        p = os.path.join(base, rec["rel"])
        try:
            if os.path.getsize(os_path(p)) != rec["bytes"]:
                bad.append({"path": p, "why": "size differs"})
            elif rec.get("blake2b") and _hash_file(p) != rec["blake2b"]:
                bad.append({"path": p, "why": "content differs"})
            else:
                ok += 1
        except OSError as exc:
            bad.append({"path": p, "why": str(exc)})
    return {"manifest": manifest_path, "ok": ok, "bad": bad, "all_good": not bad}
