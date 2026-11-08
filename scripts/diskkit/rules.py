"""Knowledge tables: regenerable project artifacts, version-copy names, junk files.

Artifact rules follow kondo's guard idea: a folder only counts as an artifact when the
marker file that proves which tool made it sits next to it. A folder called `build`
without a build file next to it is someone's data and is left alone.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactRule:
    dirname: str            # folder name (glob allowed)
    markers: tuple          # files/globs that must exist in the parent folder; () = always
    ecosystem: str
    regen: str              # how to get it back
    tier: int = 1           # 1 safe/regenerable, 2 regenerable but slow/large to rebuild
    inside: bool = False    # marker must exist INSIDE the folder instead of next to it


ARTIFACT_RULES: tuple[ArtifactRule, ...] = (
    ArtifactRule("node_modules", ("package.json",), "node", "npm/pnpm/yarn install"),
    ArtifactRule(".next", ("package.json",), "node", "next build"),
    ArtifactRule(".nuxt", ("package.json",), "node", "nuxt build"),
    ArtifactRule(".svelte-kit", ("package.json",), "node", "vite build"),
    ArtifactRule(".angular", ("package.json", "angular.json"), "node", "ng build"),
    ArtifactRule(".parcel-cache", ("package.json",), "node", "rebuilt on next build"),
    ArtifactRule(".turbo", ("turbo.json", "package.json"), "node", "rebuilt on next build"),
    ArtifactRule(".expo", ("package.json",), "node", "rebuilt by expo"),
    ArtifactRule(".vite", ("package.json",), "node", "rebuilt by vite"),
    ArtifactRule(".venv", ("pyvenv.cfg",), "python", "uv sync / pip install -r ...", 1, True),
    ArtifactRule("venv", ("pyvenv.cfg",), "python", "uv sync / pip install -r ...", 1, True),
    ArtifactRule("env", ("pyvenv.cfg",), "python", "uv sync / pip install -r ...", 1, True),
    ArtifactRule(".pixi", ("pixi.toml", "pyproject.toml"), "python", "pixi install"),
    ArtifactRule("__pycache__", (), "python", "recreated on import"),
    ArtifactRule(".pytest_cache", (), "python", "recreated by pytest"),
    ArtifactRule(".mypy_cache", (), "python", "recreated by mypy"),
    ArtifactRule(".ruff_cache", (), "python", "recreated by ruff"),
    ArtifactRule(".hypothesis", (), "python", "recreated by hypothesis"),
    ArtifactRule(".tox", ("tox.ini", "pyproject.toml", "setup.cfg"), "python", "tox"),
    ArtifactRule(".nox", ("noxfile.py",), "python", "nox"),
    ArtifactRule("__pypackages__", ("pyproject.toml",), "python", "pdm install"),
    ArtifactRule("*.egg-info", ("setup.py", "pyproject.toml", "setup.cfg"), "python", "pip install -e ."),
    ArtifactRule(".ipynb_checkpoints", (), "jupyter", "recreated by Jupyter"),
    ArtifactRule("htmlcov", (".coveragerc", "pyproject.toml", "setup.cfg"), "python", "coverage html"),
    ArtifactRule("target", ("Cargo.toml",), "rust", "cargo build", 2),
    ArtifactRule("target", ("pom.xml",), "maven", "mvn package"),
    ArtifactRule("target", ("build.sbt",), "scala", "sbt compile"),
    ArtifactRule("build", ("build.gradle", "build.gradle.kts"), "gradle", "gradle build"),
    ArtifactRule(".gradle", ("build.gradle", "build.gradle.kts", "settings.gradle",
                             "settings.gradle.kts"), "gradle", "gradle build"),
    ArtifactRule("build", ("CMakeLists.txt",), "cmake", "cmake --build"),
    ArtifactRule("cmake-build-*", ("CMakeLists.txt",), "cmake", "cmake --build"),
    ArtifactRule("bin", ("*.csproj", "*.fsproj", "*.vbproj"), "dotnet", "dotnet build"),
    ArtifactRule("obj", ("*.csproj", "*.fsproj", "*.vbproj"), "dotnet", "dotnet build"),
    ArtifactRule("build", ("pubspec.yaml",), "flutter", "flutter build"),
    ArtifactRule(".dart_tool", ("pubspec.yaml",), "flutter", "flutter pub get"),
    ArtifactRule(".build", ("Package.swift",), "swift", "swift build"),
    ArtifactRule(".swiftpm", ("Package.swift",), "swift", "swift build"),
    ArtifactRule("DerivedData", (), "xcode", "Xcode rebuilds it", 1),
    ArtifactRule("Pods", ("Podfile",), "cocoapods", "pod install"),
    ArtifactRule("zig-cache", ("build.zig",), "zig", "zig build"),
    ArtifactRule(".zig-cache", ("build.zig",), "zig", "zig build"),
    ArtifactRule("zig-out", ("build.zig",), "zig", "zig build"),
    ArtifactRule(".stack-work", ("stack.yaml",), "haskell", "stack build"),
    ArtifactRule("dist-newstyle", ("cabal.project", "*.cabal"), "haskell", "cabal build"),
    ArtifactRule("_build", ("mix.exs",), "elixir", "mix compile"),
    ArtifactRule(".elixir_ls", ("mix.exs",), "elixir", "recreated by the LSP"),
    ArtifactRule("vendor", ("composer.json",), "php", "composer install"),
    ArtifactRule(".terraform", (".terraform.lock.hcl", "*.tf"), "terraform", "terraform init"),
    ArtifactRule(".godot", ("project.godot",), "godot", "re-imported when the editor opens", 2),
    ArtifactRule("Library", ("Assembly-CSharp.csproj", "ProjectSettings"), "unity",
                 "Unity re-imports (slow)", 2),
    ArtifactRule("Intermediate", ("*.uproject",), "unreal", "rebuilt by Unreal", 2),
    ArtifactRule("DerivedDataCache", ("*.uproject",), "unreal", "rebuilt by Unreal", 2),
    ArtifactRule("Binaries", ("*.uproject",), "unreal", "rebuilt by Unreal", 2),
    ArtifactRule(".dvc", ("dvc.yaml", ".dvcignore"), "dvc", "only .dvc/cache is a cache; run dvc gc", 2),
    ArtifactRule(".cache", ("package.json",), "node", "tool cache, rebuilt on demand"),
)

_ARTIFACT_NAMES = {r.dirname for r in ARTIFACT_RULES if "*" not in r.dirname}
_ARTIFACT_GLOBS = [r.dirname for r in ARTIFACT_RULES if "*" in r.dirname]


def _marker_present(folder: str, markers: tuple, listing: list[str] | None = None) -> bool:
    if not markers:
        return True
    try:
        names = listing if listing is not None else os.listdir(folder)
    except OSError:
        return False
    for m in markers:
        if any(ch in m for ch in "*?["):
            if any(fnmatch.fnmatch(n, m) for n in names):
                return True
        elif m in names:
            return True
    return False


def match_artifact(parent: str, name: str, parent_listing: list[str] | None = None):
    """Return the ArtifactRule that makes parent/name a regenerable artifact, else None."""
    if name not in _ARTIFACT_NAMES and not any(fnmatch.fnmatch(name, g) for g in _ARTIFACT_GLOBS):
        return None
    for rule in ARTIFACT_RULES:
        if not fnmatch.fnmatch(name, rule.dirname):
            continue
        if rule.inside:
            if _marker_present(os.path.join(parent, name), rule.markers):
                return rule
        elif _marker_present(parent, rule.markers, parent_listing):
            return rule
    return None


# --------------------------------------------------------------------------- versions
# Suffixes people and agents add instead of overwriting a file. Applied repeatedly to the
# end of the stem, so "report_v2_final (1)" normalises to "report".
_VERSION_PATTERNS = [
    r"\s*\(\d+\)$",                                   # "name (1)"
    r"\s*-\s*copy(\s*\(\d+\))?$",                     # "name - Copy (2)"
    r"\s+copy(\s+\d+)?$",                             # "name copy 2" (macOS)
    r"[-_ .](v|ver|version|rev)[-_ .]?\d+([._]\d+)*[a-z]?$",  # _v2, -v1.3, _rev4
    r"[-_ .](final|finalfinal|final\d+|old|older|new|newer|backup|bak|orig|original|copy|"
    r"draft|edited|edit|fixed|fix|latest|updated|update|revised|tmp|temp|test|wip|"
    r"working|clean|cleaned|alt|redo|again)\d*$",
    r"[-_ .](19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])([-_ T]?\d{4,6})?$",  # _20261108[_1530]
    r"[-_ .](19|20)\d{2}[-_.](0[1-9]|1[0-2])[-_.](0[1-9]|[12]\d|3[01])"
    r"([-_ T]\d{2}[-_.:]?\d{2}([-_.:]?\d{2})?)?$",                               # _2026-11-08[_15-30]
]
# A name whose only decoration is a plain date (no time, no version word) is usually one
# step of a time series (monthly maps, daily exports), not a superseded copy.
_DATE_ONLY_RE = re.compile(
    r"[-_ .]((19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])|"
    r"(19|20)\d{2}[-_.](0[1-9]|1[0-2])([-_.](0[1-9]|[12]\d|3[01]))?)$")
_COPY_PREFIX = re.compile(r"^(copy of|copia de|cópia de|kopie van)\s+", re.I)
_VERSION_RES = [re.compile(p, re.I) for p in _VERSION_PATTERNS]
_MARKER_RE = re.compile(
    r"(\(\d+\)|copy|c[oó]pia|kopie|[-_ .](v|ver|version|rev)[-_ .]?\d|final|[-_ .]old\b|"
    r"backup|[-_ .]bak\b|orig|draft|edited|fixed|latest|updated|revised|[-_ .]wip\b|"
    r"(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])|(19|20)\d{2}-(0[1-9]|1[0-2])-\d{2})", re.I)

BACKUP_EXTS = (".bak", ".old", ".orig", ".backup", ".prev", ".sav")


def split_name(name: str) -> tuple[str, str]:
    """('report_v2', '.docx'); handles double extensions like .tar.gz and backup suffixes."""
    lower = name.lower()
    for ext in BACKUP_EXTS:
        if lower.endswith(ext) and len(name) > len(ext):
            inner_stem, inner_ext = split_name(name[: -len(ext)])
            return inner_stem + "~" + ext.lstrip("."), inner_ext
    if name.endswith("~"):
        s, e = split_name(name[:-1])
        return s + "~tilde", e
    for dbl in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".nii.gz"):
        if lower.endswith(dbl):
            return name[: -len(dbl)], name[-len(dbl):]
    stem, ext = os.path.splitext(name)
    return stem, ext


def normalize_stem(stem: str) -> str:
    """Strip version/copy/date decorations: 'Copy of Report_v3_FINAL (2)' -> 'report'."""
    s = stem.strip()
    s = re.sub(r"~(bak|old|orig|backup|prev|sav|tilde)$", "", s, flags=re.I)
    s = _COPY_PREFIX.sub("", s)
    for _ in range(8):
        before = s
        for rx in _VERSION_RES:
            s = rx.sub("", s).rstrip(" -_.")
        if s == before:
            break
    return s.lower() or stem.lower()


def version_rank(name: str) -> tuple:
    """Higher = more likely the current one: 'latest'/'final' words, then the version number,
    then the embedded date/time. Used to break mtime ties when picking which member to keep."""
    stem, _ext = split_name(name)
    low = stem.lower()
    words = 2 if "latest" in low else 1 if "final" in low else 0
    stale = 1 if re.search(r"(^|[-_ .~])(old|bak|backup|orig)", low) or "copy" in low else 0
    v = re.findall(r"[-_ .](?:v|ver|version|rev)[-_ .]?(\d+(?:[._]\d+)*)", low)
    vnum = tuple(int(x) for x in re.split(r"[._]", v[-1])) if v else (0,)
    digits = "".join(re.findall(r"(?:19|20)\d{2}[-_.]?\d{2}[-_.]?\d{2}(?:[-_ T]?\d{4,6})?", low))
    return (words, -stale, vnum, re.sub(r"\D", "", digits))


def is_dated_series_member(name: str) -> bool:
    """True when the only decoration is a plain date: 'lake_2026-04-22.png', 'map_202604.tif'."""
    stem, _ext = split_name(name)
    if not _DATE_ONLY_RE.search(stem):
        return False
    rest = _DATE_ONLY_RE.sub("", stem)
    return not has_version_marker(rest + ".x") if rest else True


def looks_like_series(names: list[str]) -> bool:
    """A family where every decorated member is date-only and there are 3+ of them is a
    time series (keep all), not a pile of old versions."""
    decorated = [n for n in names if has_version_marker(n)]
    dated = [n for n in decorated if is_dated_series_member(n)]
    return len(dated) >= 3 and len(dated) == len(decorated)


def has_version_marker(name: str) -> bool:
    stem, _ext = split_name(name)
    if "~" in stem and stem.split("~")[-1] in ("bak", "old", "orig", "backup", "prev", "sav", "tilde"):
        return True
    return bool(_MARKER_RE.search(stem)) or bool(_COPY_PREFIX.match(stem))


def version_key(name: str) -> tuple[str, str]:
    stem, ext = split_name(name)
    return normalize_stem(stem), ext.lower()


# --------------------------------------------------------------------------- junk
JUNK_FILE_GLOBS = {
    "partial-download": ("*.crdownload", "*.part", "*.partial", "*.download", "*.!ut", "*.opdownload"),
    "office-lock": ("~$*",),
    "editor-swap": ("*.swp", "*.swo", ".~lock.*#"),
    "crash-dump": ("*.dmp", "*.mdmp", "*.hprof", "core.[0-9]*"),
    "os-metadata": ("Thumbs.db", ".DS_Store", "ehthumbs.db"),
    "temp": ("*.tmp", "*.temp", "~*.tmp"),
}


def junk_kind(name: str) -> str | None:
    for kind, globs in JUNK_FILE_GLOBS.items():
        for g in globs:
            if fnmatch.fnmatch(name, g) or fnmatch.fnmatch(name.lower(), g.lower()):
                return kind
    return None


# Scratch folder names agents and people leave in a project root.
SCRATCH_DIR_NAMES = {"tmp", "temp", "scratch", "scratchpad", "_tmp", "_scratch", "sandbox",
                     "playground", "junk", "trash", "old", "_old", "backup", "backups", "bak",
                     "archive_old", "debug_out", "debug_output", "test_output", "outputs_old"}

ALREADY_COMPRESSED_EXTS = {
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".7z", ".rar", ".lz4", ".br",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".heic", ".jp2", ".jxl",
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mp3", ".aac", ".ogg", ".opus", ".flac", ".m4a",
    ".parquet", ".orc", ".avro", ".feather", ".pdf", ".docx", ".xlsx", ".pptx", ".odt",
    ".apk", ".ipa", ".jar", ".whl", ".nupkg", ".msi", ".dmg", ".iso", ".pak", ".vpk",
    ".npz", ".h5", ".hdf5", ".nc", ".zarr", ".laz", ".cog", ".pmtiles", ".mbtiles",
    ".vhdx", ".vmdk", ".qcow2",
}
