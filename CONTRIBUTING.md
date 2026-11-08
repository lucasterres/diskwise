# Contributing

- Keep the CLI stdlib-only, so it runs anywhere Claude Code runs.
- Anything that removes data goes through `remove.remove()` and `safety.check_removable()`.
  Add a test for every new guard.
- New caches go in `REGISTRY` in `scripts/diskkit/caches.py`, with the tool's own clean
  command and a tier. New project artifacts go in `ARTIFACT_RULES` in `rules.py`, with a
  marker file.
- Run `python -m unittest discover -s tests -v` and `claude plugin validate .` before a PR.
- Docs and skills are written in plain English: short sentences, no marketing.
