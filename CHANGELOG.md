# Changelog

## 0.1.0 (2026-11-08)

First release.

- Skills: `cleanup` (disk-space specialist), `tidy` (workspace hygiene and end-of-task pass),
  `backup` (verified offload and archive).
- Agent: `disk-auditor` (read-only scans).
- Hooks: SessionStart hygiene rules and low-disk warning, PreToolUse version-copy guard,
  Stop tidy check.
- CLI (`scripts/diskwise.py`, stdlib only): `status`, `scan`, `audit`, `caches`, `artifacts`,
  `dupes`, `versions`, `extracted`, `compressible`, `git`, `old`, `empty`, `temp`,
  `extensions`, `remove`, `apply`, `quarantine`, `restore`, `purge`, `backup`, `verify`.
