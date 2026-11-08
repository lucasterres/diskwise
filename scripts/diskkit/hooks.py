"""Claude Code hook handlers. They advise; they never delete anything.

session-start  records a snapshot (time, free space), warns when the disk is low, and adds a
               few lines of workspace-hygiene rules to the context.
pre-write      before the Write tool creates `report_v2.md` next to `report.md` (or any other
               version/copy variant), tells Claude so it can edit in place instead.
               Mode advise (default) | ask | deny | off.
stop           after each turn, a time-boxed look at files created or modified this session:
               big files, version copies, junk, total growth. Reports each finding once.
               Mode notify (default: message to the user) | nudge (asks Claude once to tidy) | off.

All handlers fail open: any error -> exit 0 with no output.
"""

from __future__ import annotations

import json
import os
import sys
import time

from . import config, rules
from .fsutil import data_dir, free_space, human, walk

HYGIENE_RULES = (
    "Diskwise workspace hygiene is on. While working: edit files in place instead of creating "
    "_v2/_final/copy variants (git and the user keep history); put throwaway files in a scratch or "
    "temp folder and delete them before you finish; write large outputs compressed (Parquet+zstd, "
    "zstd GeoTIFF/COG, npz) and do not keep intermediate copies you can regenerate; reuse existing "
    "downloads and caches instead of fetching again; before finishing a task, check which files you "
    "created and remove the temporary ones. For disk-space work use the diskwise skills."
)

SCRATCH_PREFIXES = ("tmp", "temp_", "temp-", "scratch", "debug_", "debug-", "_tmp", "test_output",
                    "output_old", "untitled")


def _state_path(session_id: str):
    d = data_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in (session_id or "unknown") if c.isalnum() or c in "-_")[:80]
    return d / f"{safe}.json"


def _load_state(session_id: str) -> dict:
    try:
        return json.loads(_state_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(session_id: str, state: dict) -> None:
    try:
        _state_path(session_id).write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def _gc_states(max_age_days: float = 14) -> None:
    d = data_dir() / "sessions"
    now = time.time()
    try:
        for p in d.iterdir():
            if now - p.stat().st_mtime > max_age_days * 86400:
                p.unlink()
    except OSError:
        pass


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def low_disk_message(cwd: str, cfg: dict) -> str | None:
    try:
        fs = free_space(cwd)
    except OSError:
        return None
    gb = fs["free"] / 1024**3
    if gb < cfg["low_disk_free_gb"] or fs["free_pct"] < cfg["low_disk_free_pct"]:
        return (f"Disk is low: {human(fs['free'])} free ({fs['free_pct']}%) on the drive holding "
                f"{cwd}. Avoid large downloads and intermediate copies; write outputs compressed; "
                f"suggest the diskwise disk-cleanup skill to the user if space runs out.")
    return None


# --------------------------------------------------------------------------- session-start
def session_start(payload: dict) -> None:
    cfg = config.load(payload.get("cwd"))
    if not cfg.get("hooks_enabled", True):
        return
    cwd = payload.get("cwd") or os.getcwd()
    sid = payload.get("session_id", "unknown")
    state = _load_state(sid)
    if "start" not in state:
        state = {"start": time.time(), "cwd": cwd, "reported": []}
    try:
        state["free_at_start"] = free_space(cwd)["free"]
    except OSError:
        pass
    _save_state(sid, state)
    _gc_states()
    parts = []
    if cfg.get("session_context", True):
        parts.append(HYGIENE_RULES)
    low = low_disk_message(cwd, cfg)
    if low:
        parts.append(low)
    if parts:
        out = {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                      "additionalContext": "\n".join(parts)}}
        if low:
            out["systemMessage"] = "Diskwise: " + low.split(". ")[0] + "."
        _emit(out)


# --------------------------------------------------------------------------- pre-write
def version_siblings(file_path: str) -> list[str]:
    """Existing files in the same folder that this new file would be a version/copy of."""
    folder, name = os.path.split(os.path.abspath(file_path))
    if not os.path.isdir(folder):
        return []
    key = rules.version_key(name)
    sibs = []
    try:
        for entry in os.scandir(folder):
            if entry.name == name or not entry.is_file(follow_symlinks=False):
                continue
            if rules.version_key(entry.name) == key:
                sibs.append(entry.name)
    except OSError:
        return []
    return sorted(sibs)[:5]


def pre_write(payload: dict) -> None:
    cfg = config.load(payload.get("cwd"))
    mode = cfg.get("version_guard", "advise")
    if not cfg.get("hooks_enabled", True) or mode == "off":
        return
    if payload.get("tool_name") not in ("Write", "NotebookEdit"):
        return
    path = (payload.get("tool_input") or {}).get("file_path") or \
           (payload.get("tool_input") or {}).get("notebook_path")
    if not path or os.path.exists(path):
        return  # overwriting in place is exactly what we want
    name = os.path.basename(path)
    msgs = []
    if rules.has_version_marker(name):
        sibs = version_siblings(path)
        if sibs:
            msgs.append(
                f"You are creating '{name}' next to {', '.join(repr(s) for s in sibs)}. That looks like "
                f"a new version/copy of an existing file. Prefer editing the existing file in place "
                f"(git and the user keep history). If a separate copy is really needed, say why and "
                f"remove the superseded one once the new one is confirmed.")
    low = name.lower()
    proj = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ""
    if low.startswith(SCRATCH_PREFIXES) and proj and \
            os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(os.path.abspath(proj)):
        msgs.append(
            f"'{name}' looks like a scratch file in the project root. Put throwaway files in a scratch/"
            f"temp folder outside the project, or delete it before you finish.")
    if not msgs:
        return
    text = "Diskwise: " + " ".join(msgs)
    if mode in ("ask", "deny") and rules.has_version_marker(name):
        _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": mode,
                                      "permissionDecisionReason": text}})
    else:
        _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}})


# --------------------------------------------------------------------------- stop
def session_changes(root: str, since: float, budget_s: float, large: int) -> dict:
    new_files, new_bytes = [], 0
    truncated = False
    from .fsutil import WalkStats
    stats = WalkStats()

    def skip(parent, nm):
        return rules.match_artifact(parent, nm) is not None or nm in ("node_modules", ".venv", "venv")

    by_folder: dict[str, list] = {}
    for d, files, _s in walk(root, skip_dir=skip, budget_s=budget_s, stats=stats):
        for f in files:
            by_folder.setdefault(d, []).append(f)
            if f.mtime >= since:
                new_files.append(f)
                new_bytes += f.size
    truncated = stats.truncated
    big = [f for f in new_files if f.size >= large]
    versions, junk = [], []
    for f in new_files:
        name = os.path.basename(f.path)
        if rules.junk_kind(name):
            junk.append(f)
            continue
        if rules.has_version_marker(name):
            key = rules.version_key(name)
            sibs = [g for g in by_folder.get(os.path.dirname(f.path), [])
                    if g.path != f.path and rules.version_key(os.path.basename(g.path)) == key]
            if sibs:
                versions.append((f, [os.path.basename(s.path) for s in sibs][:3]))
    return {"new_files": len(new_files), "new_bytes": new_bytes, "big": big, "versions": versions,
            "junk": junk, "truncated": truncated}


def stop(payload: dict) -> None:
    cfg = config.load(payload.get("cwd"))
    mode = cfg.get("stop_check", "notify")
    if not cfg.get("hooks_enabled", True) or mode == "off":
        return
    sid = payload.get("session_id", "unknown")
    state = _load_state(sid)
    cwd = state.get("cwd") or payload.get("cwd") or os.getcwd()
    since = state.get("start")
    if not since:
        state = {"start": time.time(), "cwd": cwd, "reported": []}
        _save_state(sid, state)
        return
    ch = session_changes(cwd, since - 1, float(cfg.get("stop_scan_budget_s", 2.0)),
                         int(cfg.get("large_file_mb", 200)) * 1024**2)
    reported = set(state.get("reported", []))
    lines = []
    for f in ch["big"]:
        if f.path not in reported:
            lines.append(f"big new file {f.path} ({human(f.size)})")
            reported.add(f.path)
    for f, sibs in ch["versions"]:
        if f.path not in reported:
            lines.append(f"version copy {os.path.basename(f.path)} next to {', '.join(sibs)} in {os.path.dirname(f.path)}")
            reported.add(f.path)
    for f in ch["junk"]:
        if f.path not in reported:
            lines.append(f"leftover {f.path}")
            reported.add(f.path)
    growth_flag = f"growth:{int(ch['new_bytes'] // (1024**3))}"
    warn = int(cfg.get("session_growth_warn_mb", 2048)) * 1024**2
    if ch["new_bytes"] >= warn and growth_flag not in reported:
        lines.append(f"this session created/modified {human(ch['new_bytes'])} in {ch['new_files']} files under {cwd}")
        reported.add(growth_flag)
    if "lowdisk" not in reported:
        low = low_disk_message(cwd, cfg)
        if low:
            lines.append(low.split(". ")[0])
            reported.add("lowdisk")
    state["reported"] = sorted(reported)[-500:]
    _save_state(sid, state)
    if not lines:
        return
    shown = lines[:6] + ([f"... and {len(lines) - 6} more"] if len(lines) > 6 else [])
    summary = "; ".join(shown)
    if mode == "nudge" and not payload.get("stop_hook_active") and not state.get("nudged"):
        state["nudged"] = True
        _save_state(sid, state)
        _emit({"decision": "block",
               "reason": "Diskwise tidy check before you stop: " + summary +
                         ". Remove what is temporary or superseded (ask the user before deleting anything "
                         "they made), compress what must stay, then finish."})
        return
    _emit({"systemMessage": "Diskwise: " + summary})


HANDLERS = {"session-start": session_start, "pre-write": pre_write, "stop": stop}


def main(event: str) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        handler = HANDLERS.get(event)
        if handler:
            handler(payload)
    except Exception:
        if os.environ.get("DISKWISE_DEBUG") == "1":
            raise
    return 0
