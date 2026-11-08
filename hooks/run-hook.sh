#!/usr/bin/env bash
# Diskwise hook launcher. Finds a working Python 3.9+ (python3, python or the Windows `py`
# launcher; the Microsoft Store "python3" alias is detected and skipped), remembers it, and
# runs the hook. Always exits 0: a missing Python must never block Claude Code.
event="$1"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state="${CLAUDE_PLUGIN_DATA:-$HOME/.diskwise}"
input="$(cat)"

works() {
  "$@" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >/dev/null 2>&1
}

py=""
if [ -f "$state/python-path" ]; then
  cached="$(cat "$state/python-path" 2>/dev/null)"
  # shellcheck disable=SC2086
  if [ -n "$cached" ] && works $cached; then py="$cached"; fi
fi
if [ -z "$py" ]; then
  for cand in python3 python "py -3"; do
    # shellcheck disable=SC2086
    if command -v ${cand%% *} >/dev/null 2>&1 && works $cand; then py="$cand"; break; fi
  done
  if [ -n "$py" ]; then mkdir -p "$state" 2>/dev/null && printf '%s' "$py" > "$state/python-path" 2>/dev/null; fi
fi
[ -z "$py" ] && exit 0

# shellcheck disable=SC2086
printf '%s' "$input" | $py "$root/scripts/diskwise.py" hook "$event"
exit 0
