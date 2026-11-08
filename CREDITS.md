# Credits and prior art

Diskwise is an original implementation. It borrows ideas, not code, from these projects:

- **kondo** (tbillington/kondo, MIT): artifact folders count only when a marker file proves
  which tool made them. https://github.com/tbillington/kondo
- **jdupes / fdupes**, **czkawka** (qarmin/czkawka), **rmlint** (sahib/rmlint): the staged
  duplicate search (size, then partial hash, then full hash, then optional byte compare),
  hash caching, keep-one strategies, and the review-before-delete workflow.
  https://github.com/qarmin/czkawka, https://github.com/sahib/rmlint
- **dupeGuru**: reference folders whose copies are always kept (`--prefer`).
- **BleachBit**: a declarative registry of cleaners with a preview step.
- **upkeep** (KyleNesium/upkeep): safe / warn / report-only tiers, plan expiry, and
  re-checking every item when the plan is applied. https://github.com/KyleNesium/upkeep
- **dust, dua-cli, ncdu, gdu, WizTree**: the fast, sorted "where is my space" view.
- **restic, kopia, borg, rclone**: verify-after-copy discipline; recommended for real backups.
- **claude-code-safety-net** (kenryu42): hooks as a safety layer for agent shell commands.

Field notes in `skills/cleanup/references/field-notes.md` come from real cleanups of the
author's own machines.
