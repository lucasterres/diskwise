# Caches: where they live and how to clean them

`diskwise caches` measures all of these and prints the clean command when the tool is
installed. Prefer the command: it knows what is in use. Ask the tool for its path rather than
guessing (`uv cache dir`, `pip cache dir`, `npm config get cache`, `yarn cache dir`,
`pnpm store path`, `go env GOCACHE GOMODCACHE`, `conda info --json` -> `pkgs_dirs`).

## Python

| Cache | Default path (Win / mac / Linux) | Clean |
|---|---|---|
| uv | `%LOCALAPPDATA%\uv\cache` / `~/.cache/uv` | `uv cache prune` (unused only) or `uv cache clean` |
| uv Pythons | `%APPDATA%\uv\python` / `~/.local/share/uv/python` | `uv python list --only-installed`, `uv python uninstall X` |
| pip | `%LOCALAPPDATA%\pip\Cache` / `~/Library/Caches/pip` / `~/.cache/pip` | `pip cache purge` |
| conda / mamba | `<install>/pkgs` | `conda clean --all -y`; unused envs: `conda env remove -n NAME` |
| poetry | `%LOCALAPPDATA%\pypoetry\Cache` | `poetry cache clear --all PyPI` |
| pre-commit | `~/.cache/pre-commit` | `pre-commit gc` |
| Hugging Face | `$HF_HOME/hub` (default `~/.cache/huggingface/hub`) | `hf cache ls`, `hf cache rm <repo>`, `hf cache prune`; old CLI `huggingface-cli delete-cache`. Subcommands changed across versions: check `hf cache --help` |
| torch | `~/.cache/torch/hub`, `checkpoints` | delete unused folders |

uv is routinely the biggest cache on a Python machine (tens of GB). `uv cache prune` is safe
any time; `uv cache clean` makes the next `uv sync` re-download everything.

## JavaScript

| Cache | Path | Clean |
|---|---|---|
| npm (incl. `_npx`) | `%LOCALAPPDATA%\npm-cache` / `~/.npm` | `npm cache clean --force` |
| yarn v1 / berry | `yarn cache dir` | `yarn cache clean` / `yarn cache clean --all` |
| pnpm | `pnpm store path` | `pnpm store prune` |
| bun | `~/.bun/install/cache` | `bun pm cache rm` |
| deno | `DENO_DIR` | `deno clean` |
| Playwright browsers | `%LOCALAPPDATA%\ms-playwright` / `~/Library/Caches/ms-playwright` / `~/.cache/ms-playwright` | `npx playwright uninstall --all` |

## Other toolchains

| Cache | Path | Clean |
|---|---|---|
| Cargo | `~/.cargo/registry`, `~/.cargo/git` | `cargo cache --autoclean` (cargo-cache), or delete `registry/cache` + `git/checkouts`; Cargo 1.88+ also GCs automatically. Per project: `cargo clean`, `cargo sweep` |
| Go | `go env GOCACHE GOMODCACHE` | `go clean -cache -testcache -modcache` |
| Gradle | `~/.gradle/caches`, `wrapper/dists` | `gradle --stop`, then delete; Gradle 8 auto-cleans old entries |
| Maven | `~/.m2/repository` | delete (re-downloads) or `mvn dependency:purge-local-repository` |
| NuGet | `~/.nuget/packages` | `dotnet nuget locals all --clear` |
| Composer | `composer config cache-dir` | `composer clear-cache` |
| Android | `~/.android/avd`, SDK `system-images` | delete unused AVDs in Device Manager |

## Containers and VMs

- Docker: `docker system df` first. Then `docker builder prune`, `docker image prune -a`.
  `docker system prune --volumes` deletes data volumes: separate, explicit approval.
- Docker Desktop / WSL disks (`*.vhdx`) never shrink by themselves. Prune inside, quit Docker,
  `wsl --shutdown`, then `Optimize-VHD -Path X -Mode Full` (Hyper-V module, admin) or
  `diskpart` -> `select vdisk file=X` -> `compact vdisk`. Or once:
  `wsl --manage <distro> --set-sparse true`.

## Editors, agents, browsers

- VS Code: `Cache`, `CachedData`, `CachedExtensionVSIXs`, `logs` under the Code user folder
  (VS Code closed). Superseded extension versions: `diskwise extensions`.
- JetBrains: folders for IDE versions no longer installed; the IDE offers "Delete leftover
  IDE directories".
- Claude Code / Codex: agent worktrees under `<repo>/.claude/worktrees` (remove merged ones
  with `git worktree remove`), transcripts in `~/.claude/projects`, `~/.codex/sessions`.
  Transcripts are history: archive, do not delete without asking.
- Browsers: clear from the browser's settings, or delete only `Cache`, `Code Cache`,
  `GPUCache` inside the profile with the browser closed. Never the profile itself.
- Local LLMs: Ollama (`ollama rm`), LM Studio (My Models), ComfyUI `models/` (ask: these
  are big re-downloads).

## Gotchas

- A cache in use is locked (IDE open, Docker running, a build in progress). Skip it or ask
  the user to close the app.
- Caches regrow. Tell the user which ones and suggest a periodic `uv cache prune`/`npm cache clean`.
- Moving a cache to another drive (env vars like `UV_CACHE_DIR`, `PIP_CACHE_DIR`,
  `npm_config_cache`, `HF_HOME`) is often better than deleting it on a small system drive.
