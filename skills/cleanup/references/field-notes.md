# Field notes from real cleanups

From cleaning a data-science workstation (1 TB system SSD at 2% free, a 2 TB external
SSD at 1% free, plus a cloud archive mount). Three passes freed about 670 GB without losing
anything that was needed.

## What was actually big

1. **Package caches: 80 GB.** uv alone was 40 GB, pip 19 GB, npm 8 GB. All of it was
   regenerable, cleared with the tools' own commands, and it was the fastest win.
2. **Model caches: 12 GB (HF hub + datasets) plus 40+ GB of local LLMs and diffusion models.**
   These are tier 2: they re-download, but slowly. List them by model and let the user pick.
3. **Duplicate files on the data drive: 129 GB.** Mostly whole folders copied as "Final_2025"
   from "Final_Organized", zips kept after extraction, `.img` exports of rasters that also
   existed as `.tif`, and `.bak` copies. The staged hash finder found them in minutes. The
   whole-folder view (`redundant_folders`) made the decisions easy.
4. **Version folders of map products: tens of GB.** Folders like `maps_v2`, `maps_v3`,
   `maps_test`, `old_bbox`. The rule that worked: keep the highest version per product and
   month, and drop the test runs. Confirm with the user which one is the delivered set.
5. **Agent worktrees: 15 unchanged worktrees.** Their branches were kept and the folders
   removed. Worktrees pile up silently with coding agents.
6. **A game repo's `.git` with 73 GB of loose objects.** `git gc` would only prune
   unreachable objects older than two weeks, so we noted a date and re-ran later instead of
   forcing it.
7. **Uncompressed rasters: three 110 GB mosaics.** A 25.6 GB class raster was 0.6 GB as a
   zstd GeoTIFF, lossless. This was the biggest remaining win, and it was left as advice
   because it needs GDAL and time.

## Mistakes to avoid

- **Treating a training input like a cache.** A pre-fix database snapshot looked like an old
  copy of the live database. It was the exact input a model was trained on. Ask what a
  "duplicate" or an "old" database is used for before you suggest removing it.
- **Moving data to a cloud mount that caches on C:.** The mount tool buffered writes on the
  same full drive. Free space first, or copy in chunks.
- **Shell globs after `cd`.** `cd X && rm -rf rel/*` is how the wrong folder gets emptied.
  Use explicit absolute paths through `diskwise remove`.
- **Forgetting what regrows.** Caches came back to 20+ GB within weeks. Tell the user which
  ones regrow and how to prune them on a schedule, or move them to a bigger drive.

## Left for the user, correctly

Games (100+ GB each), the page file and hibernation file, an antivirus's ProgramData,
browser site data, browsers' on-device AI models, and a 90 GB local database server (dumps
existed to restore it, but the user chose to keep it). Say what each one is, how big it is,
and how they would remove it. Then leave it.
