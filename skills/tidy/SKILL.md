---
name: tidy
description: Workspace hygiene while you work, so a coding or data task does not leave the disk heavier than it needs to be. Covers editing in place instead of creating _v2/_final/copy files, keeping scratch files out of the project, writing big outputs compressed, not re-downloading what is cached, cleaning up worktrees, and an end-of-task tidy pass that lists and removes what the session left behind. Use at the end of any task that created files, when a task produces large intermediate outputs (rasters, model checkpoints, datasets, exports, renders), when the user says "clean up after yourself", "tidy the project", "remove the temp files", or when a Diskwise hook reported version copies, junk or big new files.
argument-hint: "[project folder, default: current]"
---

# Diskwise tidy: leave the workspace lighter than you found it

Most disk bloat on a developer machine is not one big mistake. It is a hundred small ones:
`report_v2_final.docx` next to `report.docx`, a 3 GB CSV kept after it became Parquet,
`debug_out/` folders, a second clone of a repo, a dataset downloaded twice, old worktrees.
These rules stop that while the work happens.

## While working

1. **One file, edited in place.** Do not create `name_v2.ext`, `name_final.ext`,
   `name (1).ext`, `name_new.ext`, `name.bak`. Git (or the user) keeps history. If the user
   explicitly wants a side-by-side copy, make it, and remove the superseded one once the new
   one is accepted. The Diskwise pre-write hook reminds you when you are about to do this.
2. **Scratch goes outside the project.** Throwaway scripts, debug dumps, test outputs and
   downloaded samples go in the session scratchpad or the OS temp folder, not the repo root.
   Delete them before you finish.
3. **Re-runs overwrite.** Pipelines write to a stable output path, or to run folders that
   you prune (keep the last few). Never let `outputs_test`, `outputs_test2`, `outputs_new`
   pile up.
4. **Big outputs are written compressed** unless the user's format is fixed:
   - tables -> Parquet with zstd, not CSV; append-only logs -> gzip/zstd when rotated;
   - rasters -> GeoTIFF/COG with `COMPRESS=ZSTD` (or DEFLATE) + `PREDICTOR=2` (3 for floats), tiled;
   - arrays -> `np.savez_compressed`, zarr with a compressor, not raw `.npy`/`.memmap` copies;
   - point clouds -> LAZ; audio masters -> FLAC; images for docs -> PNG/WebP at display size.
5. **No intermediate copies you can regenerate.** Delete the uncompressed or unclipped
   intermediate once the final product is verified. Keep the recipe (script + parameters),
   not every by-product.
6. **Reuse before you download.** Check the project's data folder, the tool caches (HF hub,
   pip/uv, npm) and the archive spot the user uses before fetching a dataset or model again.
   Download into one shared location, not into each project.
7. **Extract or keep the archive, not both.** After extracting a zip you will work from,
   delete the zip (or the extraction once done) after confirming it is complete.
8. **Worktrees and clones are temporary.** Remove agent worktrees (`git worktree remove`)
   when their branch is merged or pushed; do not leave a second clone of a repo around.
9. **Mind the free space.** If the Diskwise session note says the disk is low, prefer
   streaming, chunked processing, and writing straight to the final compressed format.

## End-of-task tidy pass

Do this before saying a task that created files is done (quick, usually under a minute):

1. List what this session created or modified under the project: `git status --short` in a
   repo, plus `python <plugin>/scripts/diskwise.py versions <project>` and
   `... scan <project> --top 10` if large files were produced. (`<plugin>` is two levels
   above this skill's base directory. Use `python` on Windows, `python3` elsewhere.)
2. Remove what you made that is temporary: scratch scripts, debug outputs, superseded
   versions you created, partial downloads, empty folders you created. Use
   `diskwise remove PATH --reason "session scratch"` (quarantine, undoable) for anything
   bigger than a few files, then `diskwise purge BATCH --yes` once the result is verified.
3. Never delete files the user created or files that existed before the task without asking.
4. In the final message, one line: what you removed, how much space, and anything large you
   left on purpose (with why). If nothing was left behind, say nothing about it.

## When a Diskwise hook speaks

- *"You are creating X next to Y"*: edit Y instead, unless a copy was requested.
- *"big new file ..."*: check it is needed; compress it or move it out of the repo
  (and out of git: large binaries belong in LFS, a data folder or object storage).
- *"version copy ..."* / *"leftover ..."*: clean it up in the tidy pass.
- *"Disk is low"*: avoid new large files; suggest the `cleanup` skill to the user.

Settings live in `~/.claude/diskwise.json` or `<project>/.claude/diskwise.json`
(see the plugin README): e.g. `"version_guard": "ask"` to require approval for version
copies, `"stop_check": "nudge"` to have Claude tidy before stopping, `"session_context": false`
to drop the start-of-session rules.
