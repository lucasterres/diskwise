---
name: backup
description: Verified backups and archiving to free space without losing data. Copies a folder or file to another drive, NAS or cloud mount with a hash-verified manifest, moves cold data off the main disk only after verification, compresses archives losslessly, and sets up real versioned backups (restic, kopia, borg, rclone) when the user needs ongoing protection. Use when the user wants to back up, archive, move old projects or datasets to an external drive or cloud, free space by offloading instead of deleting, check that a backup is intact, or asks fazer backup, arquivar, mover para o HD externo.
argument-hint: "[source] [destination]"
---

# Diskwise backup: offload safely, prove it arrived

Deleting is not the only way to free space. Old projects, raw downloads, finished
deliveries and past years of data usually belong on an archive drive. The rule is simple:
**copy, verify, then remove the source; never move without verification.**

The CLI is `<this skill's base dir>/../../scripts/diskwise.py` (call with `python` on Windows,
`python3` elsewhere; written `diskwise` below).

## One-off archive / offload

1. **Pick what to move.** Good candidates: `diskwise old PATH --min-size 500MB --older 365d`,
   finished project folders, raw inputs that have processed products, past deliveries.
   Confirm the list with the user.
2. **Check the destination.** `diskwise status` shows free space, filesystem and drive type.
   - FAT32 cannot hold files over 4 GB; exFAT has no permissions/hardlinks (fine for archives).
   - Network/cloud mounts (RaiDrive, rclone mount, Drive for desktop, OneDrive) often cache
     writes on the local disk first: a big copy can fill the local drive before it uploads.
     Check the mount's cache folder and free space; copy in chunks if needed.
3. **Compress first when it pays.** `diskwise compressible PATH` estimates lossless savings.
   Rasters -> zstd GeoTIFF/COG (`gdal_translate -co COMPRESS=ZSTD -co PREDICTOR=2 -co TILED=YES`),
   CSV -> Parquet, dumps/logs -> `zstd -19 --long`. Do not re-compress JPEG/MP4/ZIP/Parquet.
4. **Copy with verification:** `diskwise backup SRC DEST --dry-run` (size check), then
   `diskwise backup SRC DEST`. It hashes each file while copying, re-reads the copy, compares,
   and writes `.diskwise-manifest-*.json` next to the copy. Re-runs skip files already there.
   Exclude junk with `--exclude "*.tmp" --exclude "__pycache__"`.
5. **Remove the source only after `ok: true`:** either `--move` on the same command (source
   goes to quarantine, purge after the user confirms) or `diskwise remove SRC`. Never `--move`
   to the only copy on a flaky drive without a second copy for data that cannot be recreated.
6. **Tell the user** where it went, the manifest path, and how to re-check it later:
   `diskwise verify MANIFEST`.

## Ongoing, versioned backups (recommend, then set up if asked)

`diskwise backup` is a verified copy, not a backup system. For "protect my work":

- **restic** (simple, encrypted, deduplicated, many backends): `restic init`, `restic backup`,
  `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune`,
  integrity: `restic check --read-data-subset=5%`.
- **kopia** (same ideas, has a GUI): `kopia snapshot create`, `kopia snapshot verify`.
- **borg** (Linux/macOS, very efficient): `borg create`, `borg check --verify-data`.
- **rclone** for syncing to cloud storage: `rclone copy --checksum`, verify with `rclone check`.
- OS tools: Windows File History, macOS Time Machine.

Follow 3-2-1 (three copies, two kinds of media, one off-site) for data that cannot be
recreated. Exclude caches, build folders and venvs from backups (they regenerate); the
`cleanup` skill's artifact list is a good exclude list.

## Never

- Never delete a source because a copy "looks complete". Only `ok: true` from `diskwise backup`
  or a clean `restic check`/`rclone check` counts.
- Never store credentials in backup scripts committed to git; use the tool's password file
  or environment variables kept outside the repo.
