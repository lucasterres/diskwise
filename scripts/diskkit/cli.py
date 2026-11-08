"""diskwise command line. Every analysis command is read-only; add --json for machine output."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__, config
from .fsutil import age_text, drives, free_space, human, parse_age, parse_size, volume_info

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass


def _out(args, data, text_fn):
    if getattr(args, "json", False):
        print(json.dumps(data, indent=1, default=str, ensure_ascii=False))
    else:
        text_fn(data)


def _table(rows, headers, aligns=None):
    if not rows:
        print("  (none)")
        return
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = min(max(widths[i], len(str(c))), 90)
    aligns = aligns or ["<"] * len(headers)
    fmt = "  ".join(f"{{:{a}{w}}}" for a, w in zip(aligns, widths))
    print(fmt.format(*headers))
    for r in rows:
        cells = [str(c) if len(str(c)) <= 90 else "..." + str(c)[-87:] for c in r]
        print(fmt.format(*cells))


# --------------------------------------------------------------------------- commands
def cmd_status(args):
    rows = []
    for d in drives():
        try:
            fs = free_space(d)
        except OSError:
            continue
        vi = volume_info(d)
        rows.append({**fs, **vi})
    def text(rs):
        _table([(r["path"], human(r["total"]), human(r["used"]), human(r["free"]), f"{r['free_pct']}%",
                 r.get("fs") or "", r.get("drive_type") or "") for r in rs],
               ["Drive", "Total", "Used", "Free", "Free%", "FS", "Type"], ["<", ">", ">", ">", ">", "<", "<"])
    _out(args, rows, text)


def cmd_scan(args):
    from .scan import analyze
    a = analyze(args.path, budget_s=args.budget, top_n=args.top, max_depth=args.depth)
    root_depth = len(a.root.rstrip(os.sep).split(os.sep))
    dirs = sorted(((p, s) for p, s in a.dir_bytes.items()
                   if root_depth < len(p.rstrip(os.sep).split(os.sep)) <= root_depth + args.depth),
                  key=lambda t: -t[1])[: args.top]
    data = {"root": a.root, "total": a.total, "files": a.files, "dirs": a.stats.dirs,
            "seconds": round(a.seconds, 1), "truncated": a.stats.truncated, "errors": a.stats.errors,
            "cloud_files": a.cloud_files, "top_dirs": dirs,
            "top_files": [(f.path, f.size, f.mtime) for f in a.top_files],
            "top_ext": sorted(a.ext_bytes.items(), key=lambda t: -t[1])[: args.top],
            "artifacts_bytes": sum(x.size for x in a.artifacts), "artifacts": len(a.artifacts),
            "version_family_bytes": sum(f.reclaimable for f in a.families),
            "junk_bytes": sum(f.size for _k, f in a.junk), "archives": len(a.archives),
            "git_repos": len(a.git_repos), "empty_dirs": len(a.empty_dirs)}
    def text(d):
        print(f"{d['root']}: {human(d['total'])} in {d['files']:,} files, {d['dirs']:,} folders "
              f"({d['seconds']} s{', PARTIAL: time budget hit' if d['truncated'] else ''})")
        if d["errors"]:
            print(f"  {d['errors']} folders could not be read (permissions)")
        print("\nLargest folders")
        _table([(p, human(s)) for p, s in d["top_dirs"]], ["Folder", "Size"], ["<", ">"])
        print("\nLargest files")
        now = time.time()
        _table([(p, human(s), age_text(now - m)) for p, s, m in d["top_files"]], ["File", "Size", "Age"],
               ["<", ">", ">"])
        print("\nBy extension")
        _table([(e, human(s)) for e, s in d["top_ext"]], ["Ext", "Size"], ["<", ">"])
        print(f"\nQuick hints: {d['artifacts']} build/env folders = {human(d['artifacts_bytes'])}; "
              f"version copies = {human(d['version_family_bytes'])}; junk = {human(d['junk_bytes'])}; "
              f"{d['archives']} archives; {d['git_repos']} git repos. Run `audit` for the full picture.")
    _out(args, data, text)


def cmd_artifacts(args):
    from .scan import analyze
    a = analyze(args.path, budget_s=args.budget)
    now = time.time()
    min_idle = parse_age(args.older) if args.older else 0
    rows = []
    for x in a.artifacts:
        idle = now - max(x.newest, x.project_mtime)
        if idle < min_idle:
            continue
        rows.append({"path": x.path, "bytes": x.size, "ecosystem": x.ecosystem, "tier": x.tier,
                     "idle_days": round(idle / 86400), "regen": x.regen})
    def text(rs):
        _table([(r["path"], human(r["bytes"]), r["ecosystem"], f"{r['idle_days']}d", r["regen"]) for r in rs],
               ["Folder", "Size", "Kind", "Idle", "Rebuild"], ["<", ">", "<", ">", "<"])
        print(f"\nTotal {human(sum(r['bytes'] for r in rs))} in {len(rs)} folders")
    _out(args, rows, text)


def cmd_versions(args):
    from .scan import analyze
    a = analyze(args.path, budget_s=args.budget, include_artifacts=True)
    now = time.time()
    data = [{"folder": f.folder, "key": f.key, "is_dir": f.is_dir, "reclaimable": f.reclaimable,
             "members": [{"path": p, "bytes": s, "age_days": round((now - m) / 86400)} for p, s, m in f.members]}
            for f in a.families]
    def text(fs):
        for f in fs[: args.top]:
            print(f"{f['folder']}  [{f['key']}]  -> {human(f['reclaimable'])} if only the newest is kept")
            for i, m in enumerate(f["members"]):
                print(f"   {'keep?' if i == 0 else '     '} {os.path.basename(m['path'].rstrip(os.sep))}  "
                      f"{human(m['bytes'])}  {m['age_days']}d")
        print(f"\n{len(fs)} families, {human(sum(f['reclaimable'] for f in fs))} in older members")
    _out(args, data, text)


def cmd_dupes(args):
    from . import dupes
    last = [0.0]
    def progress(b):
        if not args.json and time.monotonic() - last[0] > 5:
            last[0] = time.monotonic()
            print(f"  hashed {human(b)}...", file=sys.stderr)
    groups, meta = dupes.find_duplicates(args.paths, min_size=parse_size(args.min_size), prefer=args.prefer or [],
                                         skip_artifacts=not args.include_artifacts, use_cache=not args.no_cache,
                                         verify_bytes=args.verify_bytes, budget_s=args.budget, progress=progress)
    folders = dupes.redundant_folders(groups)
    data = {"meta": meta, "reclaimable": sum(g.reclaimable for g in groups), "groups": [
        {"size": g.size, "digest": g.digest, "keep": g.paths[0], "copies": g.paths[1:],
         "reclaimable": g.reclaimable} for g in groups], "redundant_folders": folders}
    if args.emit_list:
        with open(args.emit_list, "w", encoding="utf-8") as fh:
            for g in groups:
                fh.write(f"# keep {g.paths[0]}  ({human(g.size)})\n")
                for p in g.paths[1:]:
                    fh.write(p + "\n")
        data["list_file"] = args.emit_list
    def text(d):
        m = d["meta"]
        print(f"Scanned {m['files_seen']:,} files, {m['candidates']:,} same-size candidates, hashed "
              f"{human(m['hashed_bytes'])} in {m['seconds']} s. Hardlinks collapsed: {m['hardlinks_collapsed']}. "
              f"Cloud-only skipped: {m['cloud_skipped']}.")
        if d["redundant_folders"]:
            print("\nWhole folders that are copies")
            for f in d["redundant_folders"][:15]:
                print(f"  {f['folder']}  is {f['share'] * 100:.0f}% a copy of  {f['copy_of']}  ({human(f['shared_bytes'])})")
        print("\nDuplicate groups (first path = suggested keeper)")
        for g in d["groups"][: args.top]:
            print(f"  {human(g['size'])} x{len(g['copies']) + 1}  -> {human(g['reclaimable'])}")
            print(f"     keep  {g['keep']}")
            for c in g["copies"][:5]:
                print(f"     copy  {c}")
            if len(g["copies"]) > 5:
                print(f"     ... {len(g['copies']) - 5} more")
        print(f"\n{len(d['groups'])} groups, {human(d['reclaimable'])} reclaimable"
              + (f". Copies listed in {d['list_file']} (review, then `remove --from`)" if d.get("list_file") else ""))
    _out(args, data, text)


def cmd_caches(args):
    from . import caches
    rows = caches.survey(budget_s=args.budget, only=args.only.split(",") if args.only else None)
    def text(rs):
        _table([(r["id"], r["tier"], human(r["bytes"]) + ("+" if r["truncated"] else ""),
                 r["clean"] or "(manual)", (r["paths"] or [""])[0]) for r in rs],
               ["Cache", "Tier", "Size", "Clean with", "Path"], ["<", ">", ">", "<", "<"])
        t = {k: human(sum(r["bytes"] for r in rs if r["tier"] == k)) for k in (1, 2, 3)}
        print(f"\nTier 1 (safe, regenerates): {t[1]}. Tier 2 (costly to re-download): {t[2]}. "
              f"Tier 3 (admin or app UI, report only): {t[3]}. Notes per cache: --json.")
    _out(args, rows, text)


def cmd_extensions(args):
    from . import caches
    rows = caches.old_vscode_extensions()
    def text(rs):
        _table([(r["path"], human(r["bytes"]), r["newer"]) for r in rs], ["Old version", "Size", "Newer installed"],
               ["<", ">", "<"])
    _out(args, rows, text)


def cmd_extracted(args):
    from . import archives
    from .scan import analyze
    a = analyze(args.path, budget_s=args.budget)
    rows = archives.find_extracted([f.path for f in a.archives], verify_crc=args.verify_crc,
                                   min_fraction=args.min_fraction)
    def text(rs):
        _table([(r["archive"], human(r["archive_bytes"]), f"{r['fraction'] * 100:.0f}%", r["extracted_to"])
                for r in rs], ["Archive", "Size", "Present", "Extracted in"], ["<", ">", ">", "<"])
    _out(args, rows, text)


def cmd_compressible(args):
    from . import compress
    from .scan import analyze
    a = analyze(args.path, budget_s=args.budget, big_threshold=parse_size(args.min_size))
    rows = compress.find_compressible(a.big_files, min_size=parse_size(args.min_size))
    def text(rs):
        _table([(r["path"], human(r["bytes"]), f"{r['est_ratio'] * 100:.0f}%", human(r["est_saving"]))
                for r in rs], ["File", "Size", "Est. size after", "Saving"], ["<", ">", ">", ">"])
        for r in rs[:5]:
            print(f"  {os.path.basename(r['path'])}: {r['advice']}")
    _out(args, rows, text)


def cmd_git(args):
    from . import gitrepos
    from .scan import analyze
    a = analyze(args.path, budget_s=args.budget, include_artifacts=True)
    rows = gitrepos.survey(a.git_repos)
    def text(rs):
        for r in rs:
            print(f"{r['repo']}: .git {human(r['git_bytes'])}, loose {human(r['loose_bytes'])} ({r['loose_count']} objects), "
                  f"packs {human(r['pack_bytes'])}, garbage {human(r['garbage_bytes'])}, LFS {human(r['lfs_bytes'])}, "
                  f"{len(r['worktrees'])} worktrees")
            for s in r["suggestions"]:
                print(f"    -> {s}")
    _out(args, rows, text)


def cmd_old(args):
    from .scan import analyze
    min_size = parse_size(args.min_size)
    a = analyze(args.path, budget_s=args.budget, big_threshold=min_size)
    cutoff = time.time() - parse_age(args.older)
    rows = sorted(({"path": f.path, "bytes": f.size, "age_days": round((time.time() - f.mtime) / 86400)}
                   for f in a.big_files if f.mtime < cutoff), key=lambda r: -r["bytes"])
    def text(rs):
        _table([(r["path"], human(r["bytes"]), f"{r['age_days']}d") for r in rs[: args.top]],
               ["File", "Size", "Untouched"], ["<", ">", ">"])
        print(f"\n{len(rs)} files, {human(sum(r['bytes'] for r in rs))}. Candidates to archive (backup), not to delete blindly.")
    _out(args, rows, text)


def cmd_empty(args):
    from .scan import analyze
    a = analyze(args.path, budget_s=args.budget)
    data = {"empty_dirs": a.empty_dirs, "zero_byte_files": a.zero_files}
    def text(d):
        for p in d["empty_dirs"][: args.top]:
            print(p)
        print(f"\n{len(d['empty_dirs'])} empty folders, {d['zero_byte_files']} zero-byte files")
    _out(args, data, text)


def cmd_temp(args):
    import tempfile
    from .fsutil import tree_size
    base = tempfile.gettempdir()
    cutoff = time.time() - parse_age(args.older)
    rows = []
    try:
        entries = list(os.scandir(base))
    except OSError:
        entries = []
    for e in entries:
        try:
            st = e.stat(follow_symlinks=False)
            if st.st_mtime > cutoff:
                continue
            size = tree_size(e.path, budget_s=5)[0] if e.is_dir(follow_symlinks=False) else st.st_size
            rows.append({"path": e.path, "bytes": size, "age_days": round((time.time() - st.st_mtime) / 86400)})
        except OSError:
            continue
    rows.sort(key=lambda r: -r["bytes"])
    def text(rs):
        _table([(r["path"], human(r["bytes"]), f"{r['age_days']}d") for r in rs[: args.top]],
               ["Item", "Size", "Age"], ["<", ">", ">"])
        print(f"\n{len(rs)} items older than {args.older} in {base}: {human(sum(r['bytes'] for r in rs))}")
    _out(args, rows, text)


def cmd_audit(args):
    from . import report
    r = report.build(args.path, with_dupes=args.dupes, dupes_min=parse_size(args.dupes_min), budget_s=args.budget,
                     stale_days=args.stale_days, with_compress=not args.no_compress)
    paths = report.save(r, args.out, args.plan)
    if args.json:
        r.pop("plan", None) if not args.include_plan else None
        print(json.dumps({**r, **{"saved": paths}}, indent=1, default=str, ensure_ascii=False))
        return
    print(report.to_markdown(r) if not args.out else f"Report: {args.out}")
    if paths.get("plan"):
        n = len(r["plan"]["items"])
        sel = sum(1 for i in r["plan"]["items"] if i["selected"])
        print(f"Plan: {paths['plan']} ({n} items, {sel} preselected = {human(r['preselected_bytes'])}). "
              f"Edit 'selected', then: diskwise apply {paths['plan']}")


def _read_list(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def _confirm_permanent(args, what):
    if args.mode == "delete" and not args.yes and not args.dry_run:
        print(f"Refusing permanent delete of {what} without --yes. Default mode 'quarantine' is undoable.",
              file=sys.stderr)
        sys.exit(2)


def cmd_remove(args):
    from . import remove
    paths = list(args.paths)
    if args.from_file:
        paths += _read_list(args.from_file)
    if not paths:
        print("nothing to remove", file=sys.stderr)
        sys.exit(2)
    _confirm_permanent(args, f"{len(paths)} items")
    res = remove.remove(paths, mode=args.mode, reason=args.reason or "", allow_tracked=args.allow_tracked,
                        dry_run=args.dry_run)
    _print_remove(args, res)


def _print_remove(args, res):
    def text(r):
        verb = "Would remove" if r["dry_run"] else "Removed"
        print(f"{verb} {len(r['removed'])} items, {human(r['bytes'])} (mode {r['mode']}, batch {r['batch']})")
        for x in r["removed"][:30]:
            print(f"  - {x['path']}  {human(x['bytes'])}")
        if r["skipped"]:
            print(f"Skipped {len(r['skipped'])}:")
            for x in r["skipped"][:30]:
                print(f"  ! {x['path']}: {x['why']}")
        if not r["dry_run"] and r["mode"] != "delete" and r["removed"]:
            print(f"Space is freed only after purging: `diskwise purge {r['batch']}` "
                  f"(or `diskwise restore {r['batch']}` to undo)." if r["mode"] == "quarantine"
                  else "Space is freed when the Recycle Bin/Trash is emptied.")
    _out(args, res, text)


def cmd_apply(args):
    from . import remove
    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    if time.time() > plan.get("expires", 0) and not args.ignore_expiry:
        print("Plan expired (older than 24 h). Re-run audit, or pass --ignore-expiry.", file=sys.stderr)
        sys.exit(2)
    items = [i for i in plan["items"] if i.get("selected")]
    if args.category:
        items = [i for i in items if i["category"] in args.category.split(",")]
    _confirm_permanent(args, f"{len(items)} plan items")
    expect = {i["path"]: (i["bytes"], i["mtime"]) for i in items if i.get("mtime")}
    res = remove.remove([i["path"] for i in items], mode=args.mode, reason=f"plan {os.path.basename(args.plan)}",
                        allow_tracked=False, dry_run=args.dry_run, expect=expect)
    _print_remove(args, res)


def cmd_quarantine(args):
    from . import remove
    rows = remove.list_batches()
    def text(rs):
        _table([(r["batch"], r["items"], human(r["bytes"]), f"{r['age_days']}d", r["root"]) for r in rs],
               ["Batch", "Items", "Size", "Age", "Location"], ["<", ">", ">", ">", "<"])
        print(f"\n{human(sum(r['bytes'] for r in rs))} waiting in quarantine. `diskwise purge BATCH --yes` frees it.")
    _out(args, rows, text)


def cmd_restore(args):
    from . import remove
    _out(args, remove.restore(args.batch), lambda r: print(json.dumps(r, indent=1, ensure_ascii=False)))


def cmd_purge(args):
    from . import remove
    if not args.yes and not args.dry_run:
        print("Purging is permanent. Add --yes (or --dry-run to preview).", file=sys.stderr)
        sys.exit(2)
    res = remove.purge(args.batch, parse_age(args.older) / 86400 if args.older else None, dry_run=args.dry_run)
    _out(args, res, lambda r: print(f"{'Would purge' if r['dry_run'] else 'Purged'} {len(r['purged'])} batches, "
                                    f"{human(r['freed'])} freed"))


def cmd_backup(args):
    from . import backup, remove
    last = [0.0]
    def progress(done, total):
        if not args.json and time.monotonic() - last[0] > 5:
            last[0] = time.monotonic()
            print(f"  {human(done)} / {human(total)}", file=sys.stderr)
    res = backup.backup(args.src, args.dest, exclude=args.exclude or [], verify=args.verify,
                        dry_run=args.dry_run, progress=progress)
    if args.move and res.get("ok") and not args.dry_run:
        res["source_removal"] = remove.remove([args.src], mode=args.then, reason=f"moved to {res['dest']}",
                                              allow_tracked=False)
    elif args.move and not res.get("ok"):
        res["source_removal"] = "skipped: backup not fully verified"
    def text(r):
        if r.get("error"):
            print("ERROR:", r["error"])
            return
        print(f"{'Plan' if r['dry_run'] else 'Backup'}: {r['source']} -> {r['dest']}: {r['files']} files, {human(r['bytes'])}")
        if not r["dry_run"]:
            print(f"  copied {r['copied']}, already there {r['skipped_same']}, verified {r['verified']}, failed {len(r['failed'])}")
            for f in r["failed"][:10]:
                print(f"  ! {f['path']}: {f['why']}")
            if r.get("manifest"):
                print(f"  manifest: {r['manifest']}  (re-check any time: diskwise verify <manifest>)")
            if r.get("source_removal"):
                sr = r["source_removal"]
                print("  source:", sr if isinstance(sr, str) else f"{sr['mode']} batch {sr['batch']}, {len(sr['skipped'])} skipped")
    _out(args, res, text)
    if res.get("error") or res.get("failed"):
        sys.exit(1)


def cmd_verify(args):
    from . import backup
    res = backup.verify_manifest(args.manifest)
    _out(args, res, lambda r: print(f"{r['ok']} files OK, {len(r['bad'])} bad" +
                                    "".join(f"\n  ! {b['path']}: {b['why']}" for b in r["bad"][:20])))
    if res["bad"]:
        sys.exit(1)


def cmd_hook(args):
    from . import hooks
    sys.exit(hooks.main(args.event))


# --------------------------------------------------------------------------- parser
def build_parser():
    p = argparse.ArgumentParser(prog="diskwise", description="Find and safely remove what is wasting disk space.")
    p.add_argument("--version", action="version", version=f"diskwise {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, path=True, budget=600):
        sp = sub.add_parser(name, help=help_)
        if path:
            sp.add_argument("path", nargs="?", default=".")
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.add_argument("--budget", type=float, default=budget, help="time budget in seconds (partial result after)")
        sp.add_argument("--top", type=int, default=25)
        sp.set_defaults(fn=fn)
        return sp

    add("status", cmd_status, "free space on every drive", path=False)
    sp = add("scan", cmd_scan, "where the space is: top folders, files, types")
    sp.add_argument("--depth", type=int, default=2)
    sp = add("artifacts", cmd_artifacts, "regenerable build/env folders (node_modules, .venv, target...)")
    sp.add_argument("--older", help="only projects idle this long, e.g. 90d")
    add("versions", cmd_versions, "version families: report.docx, report_v2.docx, report_final (1).docx")
    sp = sub.add_parser("dupes", help="identical files (size -> edge hash -> full hash)")
    sp.add_argument("paths", nargs="*", default=["."])
    sp.add_argument("--min-size", default=str(config.load().get("dupes_min_size", "1MB")))
    sp.add_argument("--prefer", action="append", help="folder whose copies are kept (repeatable)")
    sp.add_argument("--include-artifacts", action="store_true")
    sp.add_argument("--verify-bytes", action="store_true", help="byte-compare before reporting")
    sp.add_argument("--no-cache", action="store_true")
    sp.add_argument("--emit-list", help="write removable copies to this file for review")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--budget", type=float, default=None)
    sp.add_argument("--top", type=int, default=25)
    sp.set_defaults(fn=cmd_dupes)
    sp = add("caches", cmd_caches, "known tool/app caches with their official clean commands", path=False, budget=60)
    sp.add_argument("--only", help="comma-separated cache ids")
    add("extensions", cmd_extensions, "superseded VS Code/Cursor extension versions", path=False)
    sp = add("extracted", cmd_extracted, "archives whose contents are already extracted next to them")
    sp.add_argument("--verify-crc", action="store_true")
    sp.add_argument("--min-fraction", type=float, default=0.9)
    sp = add("compressible", cmd_compressible, "big files that shrink a lot with lossless compression")
    sp.add_argument("--min-size", default="100MB")
    add("git", cmd_git, "git repos: .git size, loose objects, worktrees, LFS")
    sp = add("old", cmd_old, "large files untouched for a long time (archive candidates)")
    sp.add_argument("--min-size", default="500MB")
    sp.add_argument("--older", default="365d")
    add("empty", cmd_empty, "empty folders and zero-byte files")
    sp = add("temp", cmd_temp, "old items in the user temp folder", path=False)
    sp.add_argument("--older", default="7d")
    sp = add("audit", cmd_audit, "everything above in one report + an editable removal plan")
    sp.add_argument("--dupes", action="store_true", help="also hash for duplicates (slower)")
    sp.add_argument("--dupes-min", default="10MB")
    sp.add_argument("--stale-days", type=float, default=float(config.load().get("stale_project_days", 90)))
    sp.add_argument("--no-compress", action="store_true")
    sp.add_argument("--out", help="write the markdown report here")
    sp.add_argument("--plan", help="write the removal plan (JSON) here")
    sp.add_argument("--include-plan", action="store_true")

    def removal_opts(sp):
        sp.add_argument("--mode", choices=["quarantine", "recycle", "delete"], default="quarantine")
        sp.add_argument("--yes", action="store_true", help="required for --mode delete")
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("remove", help="remove paths safely (quarantine by default)")
    sp.add_argument("paths", nargs="*")
    sp.add_argument("--from", dest="from_file", help="file with one path per line (# comments ok)")
    sp.add_argument("--reason")
    sp.add_argument("--allow-tracked", action="store_true", help="allow git-tracked files")
    removal_opts(sp)
    sp.set_defaults(fn=cmd_remove)
    sp = sub.add_parser("apply", help="remove the selected items of an audit plan")
    sp.add_argument("plan")
    sp.add_argument("--category", help="only these categories (comma-separated)")
    sp.add_argument("--ignore-expiry", action="store_true")
    removal_opts(sp)
    sp.set_defaults(fn=cmd_apply)
    sp = sub.add_parser("quarantine", help="list quarantined batches")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_quarantine)
    sp = sub.add_parser("restore", help="put a quarantined batch back")
    sp.add_argument("batch")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_restore)
    sp = sub.add_parser("purge", help="permanently delete quarantined batches (frees the space)")
    sp.add_argument("batch", nargs="?")
    sp.add_argument("--older", help="purge all batches older than this, e.g. 7d")
    sp.add_argument("--yes", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_purge)
    sp = sub.add_parser("backup", help="verified copy of SRC into DEST (optionally move)")
    sp.add_argument("src")
    sp.add_argument("dest")
    sp.add_argument("--exclude", action="append", help="glob on file names (repeatable)")
    sp.add_argument("--verify", choices=["hash", "size"], default="hash")
    sp.add_argument("--move", action="store_true", help="remove the source after full verification")
    sp.add_argument("--then", choices=["quarantine", "recycle", "delete"], default="quarantine",
                    help="how --move removes the source")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_backup)
    sp = sub.add_parser("verify", help="re-check a backup against its manifest")
    sp.add_argument("manifest")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_verify)
    sp = sub.add_parser("hook", help="(internal) Claude Code hook entry point")
    sp.add_argument("event", choices=["session-start", "pre-write", "stop"])
    sp.set_defaults(fn=cmd_hook)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    return 0
