# Security

Diskwise deletes things, so its safety guarantees are its security model:

- Analysis commands only read. Hooks only read and print advice.
- Removal goes through `diskkit/safety.py`. It refuses protected system and credential
  locations, drive roots, top-level home folders, symlinks and junctions, `.git` folders,
  git-tracked files (unless `--allow-tracked`), online-only cloud files, and user
  `never_touch` paths.
- The default removal is a same-volume quarantine that `diskwise restore` reverses.
  Permanent deletion needs `--mode delete --yes` or `purge --yes`.
- No network access, telemetry or third-party dependencies (the optional `send2trash` is
  used only if you install it yourself).

If you find a way to make Diskwise remove something its guards should have protected, or a
crash that leaves data half-moved, please open a private security advisory on GitHub
(Security > Report a vulnerability) instead of a public issue.
