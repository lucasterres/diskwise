<div align="center">
  <h1>Diskwise</h1>

  <p><strong>Keep your disk lean while you work. Win back the space it already lost.</strong></p>

  <p>
    <a href="https://github.com/lucasterres/diskwise/actions/workflows/validate.yml"><img src="https://img.shields.io/github/actions/workflow/status/lucasterres/diskwise/validate.yml?branch=main&style=flat-square&label=tests" alt="Tests" /></a>
    <img src="https://img.shields.io/badge/Claude_Code-plugin-0f172a?style=flat-square" alt="Claude Code plugin" />
    <img src="https://img.shields.io/badge/python-3.9%2B%20stdlib%20only-3776ab?style=flat-square" alt="Python 3.9+, stdlib only" />
    <img src="https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-supported-0ea5a4?style=flat-square" alt="Windows, macOS, Linux" />
    <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT license" />
  </p>
</div>

Coding agents are good at making files and bad at cleaning up after themselves. A few weeks of
work leaves `report_v2_final (1).docx` next to `report.docx`, `outputs_test3/`, a 3 GB CSV
that already became Parquet, a zip kept after it was extracted, fifteen stale worktrees, and
40 GB of package cache. Diskwise is a Claude Code plugin that deals with that in two ways:

1. **While Claude works**, it keeps the workspace lean. A pre-write hook notices when Claude
   is about to create a version copy of an existing file. A stop check reports big new files,
   leftovers and session growth (once each). A short set of hygiene rules is loaded at session
   start, along with a warning when the disk is low.
2. **When the disk is full**, it acts as a disk-space specialist. It audits any folder or
   drive, sorts what it finds by risk, frees space through a guarded, undoable removal path,
   and moves cold data off the disk with hash-verified copies.

Everything runs locally from one stdlib-only Python CLI. Diskwise has no dependencies and sends
nothing over the network.

## What it finds

| Finder | What it catches |
|---|---|
| **Caches** | 50+ known caches (uv, pip, conda, npm, pnpm, yarn, cargo, go, gradle, maven, NuGet, Hugging Face, torch, Ollama, Playwright, Docker, WSL disks, VS Code, JetBrains, GPU shader caches, crash dumps, Xcode, Homebrew, journald...) with sizes and each tool's **own** clean command |
| **Build artifacts** | `node_modules`, `.venv`, `target`, `build`, `.gradle`, `bin/obj`, `.next`, `.godot`, Unity `Library`, Unreal `Intermediate`... 50+ rules, each guarded by a marker file (kondo-style), so a folder called `build` without a build file next to it is left alone. Projects idle past 90 days are flagged |
| **Duplicates** | Staged size, then head+tail hash, then full BLAKE2b hash, with optional byte compare. Hardlinks are collapsed and hashes cached. It suggests a keeper (canonical folders over Downloads/backup/worktrees) and detects **whole-folder copies** |
| **Version families** | `report.docx` / `report_v2.docx` / `Report_FINAL (1).docx` / `Copy of report.docx` / `report.docx.bak`, plus folder families like `maps_v2/`, `maps_v3/`. Dated time series (`lake_2026-04-22.png`...) are recognised and **not** flagged |
| **Extracted archives** | Zips/tars whose every member already exists on disk, with matching size (and CRC-32 with `--verify-crc`) |
| **Compressible files** | Big files that shrink a lot losslessly, estimated by sampling. Uncompressed GeoTIFFs are detected from the TIFF header, with format-specific advice (zstd COG, Parquet, LAZ, FLAC...) |
| **Git bloat** | `.git` size, loose objects, garbage, prunable worktrees, LFS cache |
| **Junk** | partial downloads, Office lock files, swap files, crash dumps, old temp items, empty folders, superseded VS Code extension versions |

## Safety model

Diskwise is built so that a confident-but-wrong agent cannot lose your data:

- **Read-only by default.** Every finder only reads.
- **Quarantine first.** `remove` moves items to `.diskwise-quarantine/` on the **same volume**.
  That is an instant rename that works for 100 GB folders the Recycle Bin would refuse.
  `restore` undoes it and `purge` frees the space. Permanent delete needs `--mode delete --yes`.
- **Guards on every removal:** system and credential locations are protected, along with the
  drive roots and the top-level home folders. Diskwise never follows symlinks or junctions.
  **git-tracked files** are refused, and so are **online-only cloud files**, because deleting
  one of those deletes the cloud copy too. You can add your own `never_touch` paths.
- **Plans expire, and every item is re-checked.** `audit --plan` writes an editable JSON plan
  that is valid for 24 hours. `apply` re-checks each item's size and mtime and skips anything
  that changed.
- **No silent permanent deletes.** On Windows, removable and network drives have no Recycle
  Bin, so "recycle" would silently delete. Diskwise refuses that.
- **Undo log.** Every action is appended to `~/.diskwise/actions.jsonl` (or the plugin data folder).
- **Verified backups.** `backup` hashes while copying, re-reads the copy and compares, then
  writes a manifest. `--move` removes the source only when every file verified.

## Install

In Claude Code:

```
/plugin marketplace add lucasterres/diskwise
/plugin install diskwise@diskwise
```

Or from a shell: `claude plugin marketplace add lucasterres/diskwise` then
`claude plugin install diskwise@diskwise`.

Requirements: Python 3.9+ (`python3`, `python` or the Windows `py` launcher; the broken
Microsoft Store `python3` alias is detected and skipped) and bash for the hooks (Git Bash on
Windows, which Claude Code uses for hooks anyway).

## Use

Ask in plain words ("my C: drive is full", "find duplicates in D:\Projects", "tidy up what
you made", "archive 2023 to the external drive"), or call the skills:

| Skill | For |
|---|---|
| `/diskwise:cleanup [path]` | Audit, then a tiered plan, then one approval, guarded removal and a before/after report |
| `/diskwise:tidy [project]` | Hygiene rules plus an end-of-task tidy pass for what the session created |
| `/diskwise:backup [src] [dest]` | Verified offload/archive, compression first, restic/kopia/borg/rclone guidance |

A read-only `disk-auditor` subagent runs big scans so raw output stays out of your context.

The CLI works on its own too (`python scripts/diskwise.py --help`):

```bash
diskwise status                                   # free space, filesystem, drive type of every drive
diskwise audit ~/work --dupes --out audit.md --plan plan.json
diskwise caches                                   # caches + official clean commands
diskwise dupes D:/Data E:/Backup --min-size 10MB --prefer D:/Data --emit-list copies.txt
diskwise versions ~/Documents
diskwise extracted ~/Downloads --verify-crc
diskwise compressible D:/Rasters
diskwise git ~/code
diskwise apply plan.json --dry-run                # then without --dry-run
diskwise quarantine                               # list batches;  diskwise restore BATCH  /  diskwise purge BATCH --yes
diskwise backup ~/old-projects /mnt/archive --move
diskwise verify /mnt/archive/old-projects/.diskwise-manifest-*.json
```

Here is a real run on a 24 GB, 167k-file workspace, which took 10 seconds including duplicate
hashing. It found 5.7 GB of virtualenvs and `node_modules`, a whole data folder that was 98%
a copy of another, a 782 MB zip whose contents were already extracted, and 1.5 GB of
duplicates.

## Hooks

| Event | What it does | Default |
|---|---|---|
| SessionStart | ~90 words of hygiene rules + a low-disk warning (below 20 GB or 10% free) | on |
| PreToolUse (Write) | Creating `x_v2.md` next to `x.md`, or `tmp_*.py` in the project root, makes Diskwise tell Claude before the write | `advise` |
| Stop | A time-boxed (2 s) check of files changed this session: big files, version copies, junk, growth. Each finding is reported once | `notify` |

Hooks always fail open: no Python, a slow disk or any error means no output, and they never
block your session.

## Configure

`~/.claude/diskwise.json` (user) and `<project>/.claude/diskwise.json` (project) are merged.
See [`diskwise.example.json`](diskwise.example.json).

| Key | Default | Meaning |
|---|---|---|
| `version_guard` | `advise` | `advise` (context note), `ask` (permission prompt), `deny`, `off` |
| `stop_check` | `notify` | `notify` (message to you), `nudge` (asks Claude once to tidy before stopping), `off` |
| `session_context` | `true` | load the hygiene rules at session start |
| `large_file_mb` | `200` | new files above this are reported |
| `session_growth_warn_mb` | `2048` | report when a session writes more than this |
| `low_disk_free_gb` / `low_disk_free_pct` | `20` / `10` | low-disk warning thresholds |
| `never_touch` / `never_touch_globs` | `[]` | paths Diskwise must never remove (or remove inside) |
| `quarantine_dir` | auto | override the quarantine location (keep it on the same volume) |

Set `DISKWISE_DISABLE_HOOKS=1` to switch the hooks off for one shell.

## How it compares

Diskwise borrows proven ideas: kondo's marker-guarded artifact rules, the staged duplicate
search of jdupes/fdupes/czkawka, rmlint's review-before-delete, BleachBit's declarative cache
registry, upkeep's tiers and plan expiry, and restic/rclone-style verification. What it adds
for agent workflows is a guard against version copies at write time, a session-scoped tidy
check, a quarantine that works for folders too big for the Recycle Bin, refusal to touch
git-tracked and cloud-only files, and whole-folder copy detection. See [CREDITS.md](CREDITS.md).

## Development

```bash
python -m unittest discover -s tests -v
claude plugin validate .
claude --plugin-dir .          # load this checkout for one session
```

MIT licensed. Issues and PRs welcome.
