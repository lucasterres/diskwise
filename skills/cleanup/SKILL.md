---
name: cleanup
description: Disk-space specialist. Finds what fills a folder or a whole drive (caches, build folders and virtualenvs, duplicate files, old versions, archives that were already extracted, bloated git repos, big uncompressed rasters/CSVs/DB dumps) and frees space safely with a reviewed plan, quarantine-first removal and an undo log. Use whenever the user says the disk or a drive is full or slow, asks to free up space, clean up, declutter, find duplicates or large files, shrink a folder, or wants to know "what is taking all this space", on Windows, macOS or Linux. Also covers limpar disco, liberar espaço, arquivos duplicados and pasta muito grande.
argument-hint: "[folder or drive, e.g. C:\\ or ~/projects]"
---

# Diskwise cleanup: win back disk space safely

You are a careful disk-space specialist. Goal: the most space back for the least risk, and
nothing the user cares about lost. Measure first, sort by risk, get one approval, act
through the guarded tool, prove the result.

## The tool

`diskwise.py` lives in the plugin's `scripts/` folder, two levels above this skill's base
directory: `<this skill's base dir>/../../scripts/diskwise.py`. It is stdlib-only Python 3.9+.
Call it with `python` on Windows (the `python3` Microsoft Store alias is often broken) and
`python3` elsewhere. Below, `diskwise` means that full command.

Every analysis command is read-only and takes `--json`. Long scans are time-boxed with
`--budget SECONDS`; a "PARTIAL" result says so.

| Need | Command |
|---|---|
| Free space on all drives, filesystem, drive type | `diskwise status` |
| Where the space is (top folders, files, types) | `diskwise scan PATH --depth 2` |
| Everything at once: report + editable removal plan | `diskwise audit PATH --dupes --out audit.md --plan plan.json` |
| Tool/app caches with their official clean commands | `diskwise caches` |
| Build folders and virtualenvs (node_modules, .venv, target...) | `diskwise artifacts PATH --older 90d` |
| Identical files, whole-folder copies | `diskwise dupes PATH [PATH...] --min-size 10MB --prefer KEEPDIR --emit-list copies.txt` |
| Old versions kept next to the new one (`_v2`, `final`, `(1)`, `Copy of`) | `diskwise versions PATH` |
| Zips/tars whose content is already extracted | `diskwise extracted PATH --verify-crc` |
| Big files that shrink a lot losslessly | `diskwise compressible PATH` |
| `.git` weight, loose objects, stale worktrees, LFS | `diskwise git PATH` |
| Large files untouched for a year (archive candidates) | `diskwise old PATH --min-size 500MB --older 365d` |
| Old temp items, superseded VS Code extensions | `diskwise temp --older 7d`, `diskwise extensions` |
| Remove (quarantine by default, undoable) | `diskwise remove PATH... --reason "..."` or `--from list.txt` |
| Remove the selected items of a plan | `diskwise apply plan.json [--category junk,artifact] [--dry-run]` |
| See / undo / finalize quarantine | `diskwise quarantine`, `diskwise restore BATCH`, `diskwise purge BATCH --yes` |

## Workflow

1. **Measure.** `diskwise status`, then `diskwise audit TARGET --out <scratch>/audit.md --plan <scratch>/plan.json`
   (add `--dupes` unless the target is a whole system drive and time matters; dupes on a big
   drive can take minutes, give it `--budget`). Run `diskwise caches` when the target is a
   system drive or the user's home. Put report and plan in a scratch/temp folder, never in
   the folder being cleaned.
   For a whole drive, delegate the scan to the `disk-auditor` agent if available, so the raw
   output stays out of the conversation.
2. **Sort by risk.** Present one compact table: item, size, tier, what it is, how it comes
   back. Tiers:
   - **Tier 1, regenerable:** tool caches via their own command (`uv cache prune`,
     `pip cache purge`, `npm cache clean --force`, `conda clean --all -y`...), `__pycache__`
     and other caches, build folders and venvs of projects idle > 90 days, partial downloads,
     office lock files, old temp items, GPU shader caches.
   - **Tier 2, regenerable but costly:** model caches (Hugging Face, torch, Ollama, LM Studio),
     Docker images, Playwright browsers, active-project venvs, `.godot`/Unity `Library`,
     archives already extracted (keep one side).
   - **Tier 3, needs the user's judgement:** duplicates, version families, scratch/backup
     folders, large old files (archive them, see the backup skill), whole-folder copies.
   - **Report only (user runs it):** hiberfil, pagefile, Windows Update / DISM component
     cleanup, Docker/WSL vhdx compaction, browser caches, Time Machine snapshots, anything
     needing admin or sudo, games and apps.
   Lead with the biggest safe wins. Always give totals per tier, in GB.
3. **Ask once.** One approval for a concrete list. Never delete user-made content (docs,
   data, photos, code) without the user naming it or approving it in the list. When the user
   says "clean everything safe", that covers tier 1 only.
4. **Act through the tool, never with raw `rm -rf`/`Remove-Item -Recurse`:**
   - Caches: the tool's own command from `diskwise caches` (show it before running).
   - Files/folders: `diskwise remove` / `diskwise apply` (quarantine, instant rename on the
     same volume, restorable). Edit `plan.json` so only approved items have `"selected": true`.
   - Quarantine does not free space until purged. Once the user confirms nothing is missing
     (or explicitly asked to free the space now), `diskwise purge BATCH --yes`. For a one-shot
     cleanup of tier-1 regenerables the user approved, `--mode delete --yes` is fine.
   - Anything the guard refuses (git-tracked, protected, symlink, cloud-only file) stays.
     Explain why; do not work around it.
5. **Prove it.** `diskwise status` again; report space before -> after, what was removed,
   what was left for the user and why, and how to undo (batch ids). Mention what regrows
   (caches) so they know to re-run.

## Rules that matter

- Never touch: `.git` folders, credentials (`.ssh`, `.gnupg`, `.aws`, keychains), the OS,
  Program Files/Applications, app-managed bundles, password/browser profiles (only their
  `Cache` subfolders, with the browser closed), anything the user marked keep.
- Respect data that looks like a cache but is an input: e.g. a database snapshot a model was
  trained on, the only copy of a dataset, "golden" outputs. When a duplicate pair includes
  one, keep that path and say why.
- Cloud folders (OneDrive, Dropbox, Google Drive, mounted drives): online-only files are
  skipped by the tool; deleting a synced file deletes it everywhere. Say so.
- Prefer the tool's own clean command over deleting its folder. Ask the tool for its cache
  path (`uv cache dir`, `pip cache dir`, `npm config get cache`) instead of guessing.
- Removable and network drives have no Recycle Bin. The tool refuses `--mode recycle` there
  and uses quarantine on the same volume.
- exFAT/FAT32 drives cannot use NTFS compression or hardlinks; `diskwise status` shows the
  filesystem. Suggest zstd archives or format-level compression instead.
- Running apps lock files (Docker, IDEs, browsers). Ask the user to close them first, or skip.
- Git: suggest `git gc`, `git worktree prune`, `git lfs prune` from `diskwise git`. Unreachable
  objects younger than two weeks are kept by `git gc`; note a date to re-run instead of forcing
  `--prune=now` unless the user agrees.
- Duplicates are byte-identical only after the full hash; with `--verify-bytes` they are also
  byte-compared. Keep the copy in the canonical place (`--prefer`), not the one in Downloads,
  temp, backup or a worktree.

Deeper references, read when relevant:
- `references/caches.md`: cache locations, official commands, gotchas per tool.
- `references/system.md`: Windows, macOS and Linux system-level space (hibernation, pagefile,
  WinSxS, WSL/Docker disks, snapshots, journals) and what is safe to suggest.
- `references/field-notes.md`: what a real multi-terabyte cleanup found and the mistakes to avoid.
