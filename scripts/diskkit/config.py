"""Configuration: built-in defaults < ~/.claude/diskwise.json < <project>/.claude/diskwise.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    # hooks
    "hooks_enabled": True,
    "session_context": True,          # inject the short hygiene rules at session start
    "version_guard": "advise",        # advise | ask | deny | off
    "stop_check": "notify",           # notify | nudge | off
    "large_file_mb": 200,             # new file above this is reported by the Stop check
    "session_growth_warn_mb": 2048,   # total new bytes in a session that triggers a note
    "low_disk_free_gb": 20,           # SessionStart warns below this...
    "low_disk_free_pct": 10,          # ...or below this percentage
    "stop_scan_budget_s": 2.0,
    # analysis
    "dupes_min_size": "1MB",
    "stale_project_days": 90,
    # safety
    "never_touch": [],                # absolute paths, never removed and never removed inside
    "never_touch_globs": [],
    "quarantine_dir": None,           # default: <home>/.diskwise-quarantine or <volume>/.diskwise-quarantine
}

_cache: dict | None = None
_cache_key: tuple | None = None


def _read(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def config_paths(project_dir: str | None = None) -> list[Path]:
    paths = [Path.home() / ".claude" / "diskwise.json"]
    proj = project_dir or os.environ.get("CLAUDE_PROJECT_DIR")
    if proj:
        paths.append(Path(proj) / ".claude" / "diskwise.json")
    return paths


def load(project_dir: str | None = None) -> dict:
    global _cache, _cache_key
    key = tuple(str(p) for p in config_paths(project_dir))
    if _cache is not None and _cache_key == key:
        return _cache
    merged = dict(DEFAULTS)
    for p in config_paths(project_dir):
        data = _read(p)
        for k, v in data.items():
            if k in ("never_touch", "never_touch_globs") and isinstance(v, list):
                merged[k] = list(merged.get(k, [])) + v
            else:
                merged[k] = v
    if os.environ.get("DISKWISE_DISABLE_HOOKS") == "1":
        merged["hooks_enabled"] = False
    _cache, _cache_key = merged, key
    return merged
