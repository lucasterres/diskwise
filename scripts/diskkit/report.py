"""`diskwise audit`: one pass over a folder or drive -> tiered markdown report + editable plan."""

from __future__ import annotations

import json
import os
import time

from . import archives, compress, dupes as dupes_mod, gitrepos
from .fsutil import age_text, free_space, human, volume_info
from .scan import analyze

PLAN_TTL_S = 24 * 3600


def build(root: str, *, with_dupes: bool = False, dupes_min: int = 1024**2, budget_s: float = 600,
          stale_days: float = 90, with_compress: bool = True, with_git: bool = True) -> dict:
    now = time.time()
    a = analyze(root, budget_s=budget_s)
    res: dict = {"root": a.root, "generated": time.strftime("%Y-%m-%d %H:%M"), "total": a.total,
                 "files": a.files, "seconds": round(a.seconds, 1), "truncated": a.stats.truncated,
                 "errors": a.stats.errors, "cloud_files": a.cloud_files}
    try:
        res["disk"] = free_space(a.root)
        res["volume"] = volume_info(a.root)
    except OSError:
        pass
    plan: list[dict] = []

    def add(path, size, mtime, category, reason, tier, selected):
        plan.append({"path": path, "bytes": size, "mtime": mtime, "category": category,
                     "reason": reason, "tier": tier, "selected": selected})

    # top folders at depth 1
    root_depth = len(a.root.rstrip(os.sep).split(os.sep))
    res["top_dirs"] = sorted(((p, s) for p, s in a.dir_bytes.items()
                              if len(p.rstrip(os.sep).split(os.sep)) == root_depth + 1),
                             key=lambda t: -t[1])[:20]
    res["top_files"] = [(f.path, f.size, f.mtime) for f in a.top_files[:20]]
    res["top_ext"] = sorted(a.ext_bytes.items(), key=lambda t: -t[1])[:15]

    # tier 1: regenerable artifacts of projects idle for stale_days
    arts = []
    for art in a.artifacts:
        idle = now - max(art.newest, art.project_mtime)
        stale = idle > stale_days * 86400
        arts.append({"path": art.path, "bytes": art.size, "ecosystem": art.ecosystem, "regen": art.regen,
                     "idle": age_text(idle), "stale": stale, "tier": art.tier})
        add(art.path, art.size, art.newest, "artifact", f"{art.ecosystem} artifact, project idle {age_text(idle)}; "
            f"regenerate with: {art.regen}", art.tier, stale and art.tier == 1)
    res["artifacts"] = arts

    junk = []
    for kind, f in a.junk:
        junk.append({"path": f.path, "bytes": f.size, "kind": kind, "age": age_text(now - f.mtime)})
        old = now - f.mtime > 2 * 86400
        add(f.path, f.size, f.mtime, "junk", f"{kind}, {age_text(now - f.mtime)} old", 1,
            old and kind in ("partial-download", "office-lock", "editor-swap", "temp", "os-metadata"))
    res["junk"] = sorted(junk, key=lambda d: -d["bytes"])

    fams = []
    for fam in a.families:
        fams.append({"folder": fam.folder, "key": fam.key, "is_dir": fam.is_dir,
                     "members": [{"path": p, "bytes": s, "age": age_text(now - m)} for p, s, m in fam.members],
                     "reclaimable": fam.reclaimable})
        for p, s, m in fam.members[1:]:
            add(p, s, m, "version", f"older member of version family '{fam.key}' (newest: "
                f"{os.path.basename(fam.members[0][0])})", 3, False)
    res["versions"] = fams

    res["scratch_dirs"] = [{"path": p, "bytes": s} for p, s in a.scratch_dirs]
    for p, s in a.scratch_dirs:
        add(p, s, 0, "scratch", "scratch/old/backup-named folder: look inside before removing", 3, False)

    ex = archives.find_extracted([f.path for f in a.archives], min_fraction=0.9)
    res["extracted_archives"] = ex
    for r in ex:
        if r["fraction"] >= 1.0:
            add(r["archive"], r["archive_bytes"], 0, "extracted-archive",
                f"all {r['members']} members already extracted in {r['extracted_to']}", 2, False)

    res["empty_dirs"] = len(a.empty_dirs)
    res["zero_files"] = a.zero_files

    if with_compress:
        res["compressible"] = compress.find_compressible(a.big_files)[:30]
    if with_git:
        res["git"] = [g for g in gitrepos.survey(a.git_repos[:50])
                      if g.get("suggestions") or g["git_bytes"] > 500 * 1024**2][:20]
    if with_dupes:
        groups, meta = dupes_mod.find_duplicates([a.root], min_size=dupes_min, budget_s=budget_s)
        res["dupes_meta"] = meta
        res["dupes"] = [{"size": g.size, "keep": g.paths[0], "remove": g.paths[1:],
                         "reclaimable": g.reclaimable} for g in groups[:200]]
        res["dupes_total"] = sum(g.reclaimable for g in groups)
        res["redundant_folders"] = dupes_mod.redundant_folders(groups)[:20]
        for g in groups:
            for p, m in zip(g.paths[1:], g.mtimes[1:]):
                add(p, g.size, m, "duplicate", f"identical to {g.paths[0]}", 3, False)

    plan.sort(key=lambda d: (-d["selected"], d["tier"], -d["bytes"]))
    res["plan"] = {"created": now, "expires": now + PLAN_TTL_S, "root": a.root, "items": plan}
    by_cat: dict[str, int] = {}
    for it in plan:
        by_cat[it["category"]] = by_cat.get(it["category"], 0) + it["bytes"]
    res["reclaimable_by_category"] = by_cat
    res["preselected_bytes"] = sum(it["bytes"] for it in plan if it["selected"])
    return res


def to_markdown(r: dict) -> str:
    L = []
    w = L.append
    w(f"# Diskwise audit: {r['root']}")
    w("")
    w(f"Generated {r['generated']}. Scanned {r['files']:,} files, {human(r['total'])} in {r['seconds']} s"
      + (" (time budget hit: partial result)" if r.get("truncated") else "") + ".")
    if r.get("disk"):
        d = r["disk"]
        vol = r.get("volume", {})
        w(f"Drive: {human(d['free'])} free of {human(d['total'])} ({d['free_pct']}%), "
          f"filesystem {vol.get('fs') or '?'}, {vol.get('drive_type') or '?'}.")
    if r.get("cloud_files"):
        w(f"{r['cloud_files']:,} online-only cloud files were counted but never read or touched.")
    w("")
    w("## Where the space is")
    w("")
    w("| Folder | Size |")
    w("|---|---:|")
    for p, s in r["top_dirs"][:12]:
        w(f"| {p} | {human(s)} |")
    w("")
    w("| Largest files | Size | Age |")
    w("|---|---:|---:|")
    now = time.time()
    for p, s, m in r["top_files"][:12]:
        w(f"| {p} | {human(s)} | {age_text(now - m)} |")
    w("")
    w("By type: " + ", ".join(f"{e} {human(s)}" for e, s in r["top_ext"][:10]))
    w("")
    w("## Reclaimable, by safety tier")
    w("")
    w("Tier 1 is regenerable (rebuilds or re-downloads by itself). Tier 2 is regenerable but slow or big "
      "to get back. Tier 3 needs a human look (duplicates, versions, archives). Nothing is removed until "
      "a plan is approved.")
    w("")
    for cat, b in sorted(r["reclaimable_by_category"].items(), key=lambda t: -t[1]):
        w(f"- **{cat}**: {human(b)}")
    w(f"- Preselected (tier 1, stale or clearly junk): **{human(r['preselected_bytes'])}**")
    w("")
    if r["artifacts"]:
        w("### Build artifacts and environments")
        w("")
        w("| Path | Size | Kind | Project idle | Rebuild with |")
        w("|---|---:|---|---:|---|")
        big = [x for x in r["artifacts"] if x["bytes"] >= 10 * 1024**2]
        small = [x for x in r["artifacts"] if x["bytes"] < 10 * 1024**2]
        for x in big[:25]:
            w(f"| {x['path']} | {human(x['bytes'])} | {x['ecosystem']} | {x['idle']}{' (stale)' if x['stale'] else ''} | {x['regen']} |")
        w("")
        if small:
            w(f"Plus {len(small)} small cache folders ({human(sum(x['bytes'] for x in small))}), "
              f"mostly {max(set(x['ecosystem'] for x in small), key=[x['ecosystem'] for x in small].count)}.")
            w("")
    if r["versions"]:
        w("### Version families (old copies kept next to the new one)")
        w("")
        for f in r["versions"][:20]:
            mem = "; ".join(f"{os.path.basename(m['path'].rstrip(os.sep))} {human(m['bytes'])} ({m['age']})"
                            for m in f["members"][:6])
            w(f"- {f['folder']}: {mem} -> {human(f['reclaimable'])} if only the newest is kept")
        w("")
    if r.get("extracted_archives"):
        w("### Archives already extracted")
        w("")
        for x in r["extracted_archives"][:20]:
            w(f"- {x['archive']} ({human(x['archive_bytes'])}): {x['fraction'] * 100:.0f}% of {x['members']} members "
              f"present in {x['extracted_to']}")
        w("")
    if r.get("dupes"):
        w(f"### Duplicate files: {human(r['dupes_total'])} reclaimable")
        w("")
        for rf in r.get("redundant_folders", [])[:10]:
            w(f"- Folder {rf['folder']} is {rf['share'] * 100:.0f}% a copy of {rf['copy_of']} ({human(rf['shared_bytes'])})")
        for g in r["dupes"][:15]:
            w(f"- {human(g['size'])} x{len(g['remove']) + 1}: keep {g['keep']}; copies: " + "; ".join(g["remove"][:3]))
        w("")
    if r.get("compressible"):
        w("### Large files that compress well (lossless)")
        w("")
        w("| File | Size | Est. saving | How |")
        w("|---|---:|---:|---|")
        for x in r["compressible"][:15]:
            w(f"| {x['path']} | {human(x['bytes'])} | {human(x['est_saving'])} | {x['advice']} |")
        w("")
    if r.get("git"):
        w("### Git repositories")
        w("")
        for g in r["git"]:
            s = "; ".join(g["suggestions"]) or "nothing to do"
            w(f"- {g['repo']}: .git {human(g['git_bytes'])} (loose {human(g['loose_bytes'])}, "
              f"garbage {human(g['garbage_bytes'])}, {len(g['worktrees'])} worktrees). {s}")
        w("")
    if r["junk"]:
        w("### Junk files")
        w("")
        for x in r["junk"][:15]:
            w(f"- {x['path']} ({x['kind']}, {human(x['bytes'])}, {x['age']})")
        w("")
    if r["scratch_dirs"]:
        w("### Scratch / old / backup folders (look inside)")
        w("")
        for x in r["scratch_dirs"][:15]:
            w(f"- {x['path']} ({human(x['bytes'])})")
        w("")
    w(f"Empty folders: {r['empty_dirs']:,}. Zero-byte files: {r['zero_files']:,}.")
    w("")
    return "\n".join(L)


def save(r: dict, out_md: str | None, out_plan: str | None) -> dict:
    paths = {}
    if out_md:
        with open(out_md, "w", encoding="utf-8") as fh:
            fh.write(to_markdown(r))
        paths["report"] = out_md
    if out_plan:
        with open(out_plan, "w", encoding="utf-8") as fh:
            json.dump(r["plan"], fh, indent=1, ensure_ascii=False)
        paths["plan"] = out_plan
    return paths
