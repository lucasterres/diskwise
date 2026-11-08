"""Registry of well-known caches: where they live, how big they are, how to clean them.

Rule: prefer the tool's own clean command over deleting folders, and ask the tool where
its cache is instead of guessing. Tiers:
  1 = safe and regenerable (re-download on demand)
  2 = regenerable but expensive to get back (big model/image re-downloads, rebuilds)
  3 = needs admin or a system/app UI: report only, the user runs it
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .fsutil import IS_MAC, IS_WIN, tree_size

H = str(Path.home())
LOCAL = os.environ.get("LOCALAPPDATA", os.path.join(H, "AppData", "Local"))
ROAMING = os.environ.get("APPDATA", os.path.join(H, "AppData", "Roaming"))
XDG_CACHE = os.environ.get("XDG_CACHE_HOME", os.path.join(H, ".cache"))
PROGRAMDATA = os.environ.get("ProgramData", r"C:\ProgramData")
WINDIR = os.environ.get("SystemRoot", r"C:\Windows")
TEMP = os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"


@dataclass
class Cache:
    id: str
    name: str
    paths: list                       # candidate paths (globs allowed)
    clean: str                        # official command, or "" for manual
    tier: int = 1
    ask: list = field(default_factory=list)   # command whose stdout is the cache path
    tool: str = ""                    # executable that must exist for `clean`
    note: str = ""
    os: str = "any"                   # any | win | mac | linux | unix


def _p(*parts):
    return os.path.join(*parts)


REGISTRY: list[Cache] = [
    # ---------------------------------------------------------------- Python
    Cache("uv", "uv cache", [_p(LOCAL, "uv", "cache"), _p(XDG_CACHE, "uv")], "uv cache prune",
          ask=["uv", "cache", "dir"], tool="uv",
          note="`uv cache prune` drops unused entries; `uv cache clean` empties it. Often the largest cache on dev machines (40 GB seen)."),
    Cache("uv-python", "uv-managed Pythons", [_p(ROAMING, "uv", "python"), _p(H, ".local", "share", "uv", "python")],
          "uv python list --only-installed", tier=2, tool="uv",
          note="Remove versions you no longer use with `uv python uninstall <ver>`."),
    Cache("pip", "pip cache", [_p(LOCAL, "pip", "Cache"), _p(H, "Library", "Caches", "pip"), _p(XDG_CACHE, "pip")],
          "pip cache purge", ask=["pip", "cache", "dir"], tool="pip"),
    Cache("conda", "conda package cache", [_p(H, "miniconda3", "pkgs"), _p(H, "anaconda3", "pkgs"),
                                           _p(H, "miniforge3", "pkgs"), _p(H, ".conda", "pkgs"),
                                           _p(LOCAL, "conda", "conda", "pkgs")],
          "conda clean --all -y", tool="conda", note="Unused envs: `conda env list`, then `conda env remove -n NAME`."),
    Cache("poetry", "Poetry cache", [_p(LOCAL, "pypoetry", "Cache"), _p(H, "Library", "Caches", "pypoetry"),
                                     _p(XDG_CACHE, "pypoetry")], "poetry cache clear --all PyPI", tool="poetry"),
    Cache("pre-commit", "pre-commit envs", [_p(XDG_CACHE, "pre-commit")], "pre-commit gc", tool="pre-commit"),
    Cache("huggingface", "Hugging Face hub", [_p(os.environ.get("HF_HOME", _p(XDG_CACHE, "huggingface")), "hub")],
          "hf cache ls  (then: hf cache rm <repo>)", tier=2, tool="hf",
          note="Models re-download on use (can be tens of GB). Older CLI: `huggingface-cli delete-cache`. Check `hf cache --help`."),
    Cache("hf-datasets", "Hugging Face datasets", [_p(os.environ.get("HF_HOME", _p(XDG_CACHE, "huggingface")), "datasets")],
          "", tier=2, note="Delete dataset folders you no longer use; they re-download."),
    Cache("torch", "PyTorch hub / checkpoints", [_p(XDG_CACHE, "torch")], "", tier=2,
          note="No clean command; delete hub/checkpoints you no longer use."),
    Cache("ollama", "Ollama models", [_p(H, ".ollama", "models")], "ollama list  (then: ollama rm MODEL)", tier=2, tool="ollama"),
    Cache("lmstudio", "LM Studio models", [_p(H, ".lmstudio", "models"), _p(H, ".cache", "lm-studio", "models")], "",
          tier=2, note="Remove models from the LM Studio UI (My Models)."),
    # ---------------------------------------------------------------- JS
    Cache("npm", "npm cache", [_p(LOCAL, "npm-cache"), _p(H, ".npm")], "npm cache clean --force",
          ask=["npm", "config", "get", "cache"], tool="npm", note="Includes _npx (one-off npx packages)."),
    Cache("yarn", "Yarn cache", [_p(LOCAL, "Yarn", "Cache"), _p(H, "Library", "Caches", "Yarn"), _p(XDG_CACHE, "yarn")],
          "yarn cache clean", ask=["yarn", "cache", "dir"], tool="yarn"),
    Cache("pnpm", "pnpm store", [_p(LOCAL, "pnpm", "store"), _p(H, "Library", "pnpm", "store"),
                                 _p(H, ".local", "share", "pnpm", "store")], "pnpm store prune",
          ask=["pnpm", "store", "path"], tool="pnpm"),
    Cache("bun", "Bun cache", [_p(H, ".bun", "install", "cache")], "bun pm cache rm", tool="bun"),
    Cache("deno", "Deno cache", [_p(LOCAL, "deno"), _p(XDG_CACHE, "deno"), _p(H, "Library", "Caches", "deno")],
          "deno clean", tool="deno"),
    Cache("electron", "Electron / node-gyp downloads", [_p(LOCAL, "electron", "Cache"), _p(XDG_CACHE, "electron"),
                                                        _p(LOCAL, "node-gyp", "Cache"), _p(XDG_CACHE, "node-gyp")], ""),
    Cache("playwright", "Playwright browsers", [_p(LOCAL, "ms-playwright"), _p(H, "Library", "Caches", "ms-playwright"),
                                                _p(XDG_CACHE, "ms-playwright")], "npx playwright uninstall --all", tier=2,
          note="Removes downloaded browsers; projects re-install them with `npx playwright install`."),
    Cache("puppeteer", "Puppeteer browsers", [_p(XDG_CACHE, "puppeteer")], "", tier=2),
    # ---------------------------------------------------------------- other langs
    Cache("cargo", "Cargo registry + git", [_p(H, ".cargo", "registry"), _p(H, ".cargo", "git")],
          "cargo cache --autoclean", tool="cargo-cache",
          note="Without cargo-cache: delete ~/.cargo/registry/cache and ~/.cargo/git/checkouts (re-downloaded). Cargo 1.88+ also auto-cleans."),
    Cache("go", "Go build + module cache", [], "go clean -cache -testcache -modcache", tool="go",
          ask=["go", "env", "GOCACHE"]),
    Cache("gradle", "Gradle caches", [_p(H, ".gradle", "caches"), _p(H, ".gradle", "wrapper", "dists")],
          "", note="Stop daemons first (`gradle --stop`), then delete caches/ and old wrapper dists."),
    Cache("maven", "Maven repository", [_p(H, ".m2", "repository")], "", tier=2,
          note="Re-downloads on next build. Or per project: `mvn dependency:purge-local-repository`."),
    Cache("nuget", "NuGet caches", [_p(H, ".nuget", "packages"), _p(LOCAL, "NuGet", "v3-cache")],
          "dotnet nuget locals all --clear", tool="dotnet"),
    Cache("composer", "Composer cache", [_p(LOCAL, "Composer"), _p(XDG_CACHE, "composer")], "composer clear-cache", tool="composer"),
    Cache("android-avd", "Android emulators (AVDs)", [_p(H, ".android", "avd")], "", tier=2,
          note="Delete unused virtual devices from Android Studio's Device Manager."),
    # ---------------------------------------------------------------- containers / VMs
    Cache("docker", "Docker images, build cache, volumes", [], "docker system df", tier=2, tool="docker",
          note="Then `docker builder prune` and `docker image prune -a`. `docker system prune --volumes` deletes data volumes: ask first."),
    Cache("docker-vhdx", "Docker Desktop disk image", [_p(LOCAL, "Docker", "wsl", "disk", "*.vhdx"),
                                                       _p(LOCAL, "Docker", "wsl", "data", "*.vhdx"),
                                                       _p(LOCAL, "Docker", "wsl", "main", "*.vhdx")],
          "", tier=3, os="win",
          note="The .vhdx does not shrink by itself. Prune inside Docker, quit it, `wsl --shutdown`, then compact (Optimize-VHD -Mode Full, admin) or enable sparse mode."),
    Cache("wsl", "WSL distro disks", [_p(LOCAL, "Packages", "*", "LocalState", "ext4.vhdx"), _p(LOCAL, "wsl", "*", "ext4.vhdx")],
          "", tier=3, os="win",
          note="Clean inside the distro, `wsl --shutdown`, then `wsl --manage <distro> --set-sparse true` or Optimize-VHD (admin)."),
    # ---------------------------------------------------------------- editors / agents
    Cache("vscode", "VS Code caches", [_p(ROAMING, "Code", "Cache"), _p(ROAMING, "Code", "CachedData"),
                                       _p(ROAMING, "Code", "CachedExtensionVSIXs"), _p(ROAMING, "Code", "logs"),
                                       _p(H, "Library", "Application Support", "Code", "Cache"),
                                       _p(H, "Library", "Application Support", "Code", "CachedData"),
                                       _p(H, ".config", "Code", "Cache"), _p(H, ".config", "Code", "CachedData")],
          "", note="Safe to delete while VS Code is closed. Also check old extension versions: `diskwise extensions`."),
    Cache("jetbrains", "JetBrains IDE caches", [_p(LOCAL, "JetBrains", "*"), _p(H, "Library", "Caches", "JetBrains", "*"),
                                                _p(XDG_CACHE, "JetBrains", "*")], "", tier=1,
          note="Folders of IDE versions you no longer run are leftovers. The IDE offers 'Delete leftover IDE directories'."),
    Cache("claude-worktrees", "Claude Code worktrees", [], "git worktree list  (per repo)", tier=2,
          note="Old agent worktrees under <repo>/.claude/worktrees pile up; remove merged ones with `git worktree remove`."),
    Cache("claude-transcripts", "Claude Code transcripts", [_p(H, ".claude", "projects")], "", tier=2,
          note="Session history (.jsonl). Keep unless you are sure; old ones can be archived."),
    Cache("codex-sessions", "Codex sessions", [_p(H, ".codex", "sessions")], "", tier=2),
    # ---------------------------------------------------------------- browsers
    Cache("chrome", "Chrome cache", [_p(LOCAL, "Google", "Chrome", "User Data", "*", "Cache"),
                                     _p(LOCAL, "Google", "Chrome", "User Data", "*", "Code Cache"),
                                     _p(H, "Library", "Caches", "Google", "Chrome"), _p(XDG_CACHE, "google-chrome")],
          "", tier=3, note="Clear from Chrome settings, or delete only Cache/Code Cache/GPUCache with Chrome closed. Never the profile."),
    Cache("edge", "Edge cache", [_p(LOCAL, "Microsoft", "Edge", "User Data", "*", "Cache"),
                                 _p(LOCAL, "Microsoft", "Edge", "User Data", "*", "Code Cache")], "", tier=3, os="win",
          note="Clear from Edge settings."),
    Cache("firefox", "Firefox cache", [_p(LOCAL, "Mozilla", "Firefox", "Profiles", "*", "cache2"),
                                       _p(H, "Library", "Caches", "Firefox", "Profiles", "*", "cache2"),
                                       _p(XDG_CACHE, "mozilla", "firefox", "*", "cache2")], "", tier=3),
    # ---------------------------------------------------------------- Windows
    Cache("win-temp", "User temp folder", [TEMP] if IS_WIN else [], "", os="win",
          note="Delete items older than a few days; files in use are skipped. `diskwise temp` lists old items."),
    Cache("crashdumps", "Crash dumps", [_p(LOCAL, "CrashDumps"), _p(WINDIR, "Minidump"), _p(WINDIR, "MEMORY.DMP"),
                                        _p(PROGRAMDATA, "Microsoft", "Windows", "WER", "ReportArchive")], "", os="win",
          note="The Windows folders need admin."),
    Cache("gpu-shaders", "GPU shader caches", [_p(LOCAL, "NVIDIA", "DXCache"), _p(LOCAL, "NVIDIA", "GLCache"),
                                               _p(LOCAL, "D3DSCache"), _p(LOCAL, "AMD", "DxCache"),
                                               _p(LOCAL, "AMD", "DxcCache")], "", os="win",
          note="Rebuilt by the driver; first game launch after is slower."),
    Cache("win-update", "Windows Update downloads", [_p(WINDIR, "SoftwareDistribution", "Download")],
          "cleanmgr /sageset:1 && cleanmgr /sagerun:1", tier=3, os="win",
          note="Admin. Also `Dism /Online /Cleanup-Image /StartComponentCleanup` for WinSxS (never /ResetBase without asking)."),
    Cache("delivery-opt", "Delivery Optimization cache", [], "Delete-DeliveryOptimizationCache -Force", tier=3, os="win",
          note="Admin PowerShell."),
    Cache("hiberfil", "Hibernation file", ["C:\\hiberfil.sys"], "powercfg /h off", tier=3, os="win",
          note="Only if you never hibernate (also disables Fast Startup). `powercfg /h /type reduced` halves it."),
    Cache("pagefile", "Page file", ["C:\\pagefile.sys"], "", tier=3, os="win",
          note="Never delete. Can be resized in System > Advanced > Performance settings."),
    # ---------------------------------------------------------------- macOS
    Cache("mac-user-caches", "~/Library/Caches", [_p(H, "Library", "Caches")], "", os="mac",
          note="Per-app caches; quit apps first. Prefer clearing big subfolders individually."),
    Cache("xcode-deriveddata", "Xcode DerivedData", [_p(H, "Library", "Developer", "Xcode", "DerivedData")], "", os="mac"),
    Cache("xcode-devicesupport", "iOS DeviceSupport", [_p(H, "Library", "Developer", "Xcode", "iOS DeviceSupport")], "", os="mac"),
    Cache("simulators", "iOS simulators", [_p(H, "Library", "Developer", "CoreSimulator", "Devices")],
          "xcrun simctl delete unavailable", tier=2, os="mac", tool="xcrun"),
    Cache("homebrew", "Homebrew cache", [_p(H, "Library", "Caches", "Homebrew")], "brew cleanup --prune=all", os="mac", tool="brew"),
    Cache("tm-snapshots", "Time Machine local snapshots", [], "tmutil listlocalsnapshots /", tier=3, os="mac",
          note="`tmutil thinlocalsnapshots / 100000000000 4` frees up to ~100 GB of local snapshots."),
    # ---------------------------------------------------------------- Linux
    Cache("journal", "systemd journal", ["/var/log/journal"], "sudo journalctl --vacuum-size=200M", tier=3, os="linux"),
    Cache("apt", "APT package cache", ["/var/cache/apt/archives"], "sudo apt-get clean", tier=3, os="linux"),
    Cache("dnf", "DNF cache", ["/var/cache/dnf"], "sudo dnf clean all", tier=3, os="linux"),
    Cache("flatpak", "Unused Flatpak runtimes", [], "flatpak uninstall --unused", tier=2, os="linux", tool="flatpak"),
    Cache("snap", "Disabled snap revisions", ["/var/lib/snapd/snaps"], "snap list --all | grep disabled", tier=3, os="linux"),
    Cache("thumbnails", "Thumbnail cache", [_p(XDG_CACHE, "thumbnails")], "", os="linux"),
]


def _os_ok(c: Cache) -> bool:
    if c.os == "any":
        return True
    if c.os == "win":
        return IS_WIN
    if c.os == "mac":
        return IS_MAC
    if c.os == "linux":
        return not IS_WIN and not IS_MAC
    return not IS_WIN


def _ask(cmd: list[str]) -> list[str]:
    if not shutil.which(cmd[0]):
        return []
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()][:3]


def resolve(c: Cache) -> list[str]:
    paths: list[str] = []
    if c.ask:
        paths += [p for p in _ask(c.ask) if os.path.exists(p)]
        if c.id == "go":
            paths += [p for p in _ask(["go", "env", "GOMODCACHE"]) if os.path.exists(p)]
    if c.id == "conda" and shutil.which("conda"):
        try:
            out = subprocess.run(["conda", "info", "--json"], capture_output=True, text=True, timeout=30)
            paths += [p for p in json.loads(out.stdout).get("pkgs_dirs", []) if os.path.isdir(p)]
        except Exception:
            pass
    for pattern in c.paths:
        if any(ch in pattern for ch in "*?["):
            paths += [p for p in glob.glob(pattern) if os.path.exists(p)]
        elif os.path.exists(pattern):
            paths.append(pattern)
    uniq, seen = [], set()
    for p in paths:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen and not any(k.startswith(s + os.sep) for s in seen):
            seen.add(k)
            uniq.append(p)
    return uniq


def _measure(c: Cache, budget_s: float) -> dict:
    paths = resolve(c)
    total, truncated = 0, False
    for p in paths:
        if os.path.isfile(p):
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
            continue
        size, _n, trunc, _new = tree_size(p, budget_s=budget_s)
        total += size
        truncated |= trunc
    tool_ok = (not c.tool) or bool(shutil.which(c.tool))
    return {"id": c.id, "name": c.name, "tier": c.tier, "paths": paths, "bytes": total,
            "truncated": truncated, "clean": c.clean if tool_ok else "", "clean_tool_missing": not tool_ok,
            "note": c.note}


def survey(budget_s: float = 60, only: list[str] | None = None, workers: int = 6) -> list[dict]:
    items = [c for c in REGISTRY if _os_ok(c) and (not only or c.id in only)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda c: _measure(c, budget_s), items))
    out = []
    for r in results:
        if r["paths"] or (r["clean"] and r["id"] in ("docker", "go", "flatpak", "tm-snapshots",
                                                      "delivery-opt")):
            out.append(r)
    out.sort(key=lambda r: -r["bytes"])
    return out


def old_vscode_extensions() -> list[dict]:
    """Superseded extension versions left in ~/.vscode/extensions (publisher.name-1.2.3)."""
    import re
    out = []
    for base in (_p(H, ".vscode", "extensions"), _p(H, ".cursor", "extensions"),
                 _p(H, ".vscode-insiders", "extensions"), _p(H, ".windsurf", "extensions")):
        if not os.path.isdir(base):
            continue
        fams: dict[str, list] = {}
        for name in os.listdir(base):
            m = re.match(r"^(.+?)-(\d+\.\d+\.\d+)(.*)$", name)
            if m:
                fams.setdefault(m.group(1) + m.group(3), []).append((tuple(int(x) for x in m.group(2).split(".")), name))
        for fam, versions in fams.items():
            if len(versions) < 2:
                continue
            versions.sort(reverse=True)
            for _v, name in versions[1:]:
                p = _p(base, name)
                out.append({"path": p, "bytes": tree_size(p, budget_s=10)[0], "newer": versions[0][1]})
    return out
