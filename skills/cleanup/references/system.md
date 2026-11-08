# System-level space: suggest, let the user run

These need admin/sudo or change system behaviour. Explain the trade-off and give the exact
command; do not run them yourself unless the user asks you to in this session.

## Windows

| Item | Check | Free it | Trade-off |
|---|---|---|---|
| Hibernation file `C:\hiberfil.sys` (~40% of RAM) | `diskwise caches` | `powercfg /h off` (admin) or `powercfg /h /type reduced` | No hibernate; Fast Startup off |
| Page file `pagefile.sys` | `diskwise caches` | System > Advanced > Performance > Virtual memory | Never delete; shrinking it can crash memory-hungry jobs |
| Component store (WinSxS) | `Dism /Online /Cleanup-Image /AnalyzeComponentStore` | `Dism /Online /Cleanup-Image /StartComponentCleanup` | `/ResetBase` makes updates permanent (no uninstall): ask |
| Windows Update downloads, old upgrade files | `cleanmgr` | `cleanmgr /sageset:1` then `/sagerun:1` (admin), or Settings > Storage > Temporary files | none |
| Delivery Optimization | Settings > Storage | `Delete-DeliveryOptimizationCache -Force` (admin PowerShell) | none |
| System Restore points | `vssadmin list shadowstorage` | System Protection > Configure > Delete, or cap the size | lose restore points |
| Crash dumps | `%LOCALAPPDATA%\CrashDumps`, `C:\Windows\Minidump`, `MEMORY.DMP`, `ProgramData\Microsoft\Windows\WER` | delete | lose crash evidence |
| Storage Sense | Settings > Storage | turn on, set temp/Downloads/Recycle Bin rules | automates the above |
| NTFS compression for cold folders | `compact /s /q DIR` | `compact /c /s /i /exe:lzx DIR` (NTFS only, not exFAT) | slower writes; great for old projects/docs; useless on JPEG/ZIP/video |
| CompactOS | `compact /compactos:query` | `compact /compactos:always` (admin) | small CPU cost; frees 2-5 GB |

Never touch: `C:\Windows`, `Program Files`, `ProgramData` app folders, `$Recycle.Bin` of
other users, `System Volume Information`, app-managed bundles (e.g. VM images used by
desktop apps), winget portable packages.

Games (Steam, Epic, Battle.net) and their shader caches are often the biggest items on a
gaming PC: point them out with the launcher's own uninstall/move option.

## macOS

- Time Machine local snapshots: `tmutil listlocalsnapshots /`; thin with
  `tmutil thinlocalsnapshots / 100000000000 4` (frees up to ~100 GB).
- `~/Library/Caches` per app (quit the app first); `~/Library/Developer/Xcode/DerivedData`,
  `iOS DeviceSupport`, Archives; `xcrun simctl delete unavailable`.
- Homebrew: `brew cleanup --prune=all`, `brew autoremove`.
- "System Data" in Settings is mostly snapshots, caches and VM swap; it shrinks after the above
  and a reboot.
- Never touch `/System`, `/Library` (system parts), `~/Library/Mobile Documents` (iCloud).

## Linux

- systemd journal: `journalctl --disk-usage`, `sudo journalctl --vacuum-size=200M`.
- Packages: `sudo apt-get clean && sudo apt-get autoremove`, `sudo dnf clean all`,
  `flatpak uninstall --unused`, disabled snap revisions
  (`snap list --all | awk '/disabled/{print $1, $3}'` then `sudo snap remove NAME --revision=REV`).
- `~/.cache` per app, thumbnails in `~/.cache/thumbnails`.
- Old kernels: let the package manager do it (`autoremove`), never by hand.

## Space you think you freed but did not

- Recycle Bin / Trash / Diskwise quarantine still hold the bytes until emptied/purged.
- A file open by a running process keeps its space after deletion (Linux) or cannot be
  deleted (Windows) until the process exits.
- Thin-provisioned virtual disks (vhdx, qcow2, Docker.raw) keep their size until compacted.
- Snapshots (Time Machine local, btrfs/ZFS, VSS) can hold deleted data.
- Cloud sync clients may keep a local cache of what you just moved to the cloud.
