# Troubleshooting

- `freecad executable not found` or `blender executable not found`: pass `--freecad-cmd` or `--blender-exe`, set the corresponding `CBE_` environment variable, or put the executable on PATH. The blocker names the missing capability.
- A specified executable path is invalid: correct that value. The detector intentionally does not fall back to a lower priority source.
- `projects_root must be an absolute path`: use an absolute root in the CLI, environment variable, or user config. The default home path needs no setup.
- Version query unavailable: executable discovery succeeded, but the `--version` probe timed out or failed. Inspect the application in a separate, read-only step; do not claim a verified export interface.
- `engineering_export: not_verified_by_read_only_preflight`: run a disposable save/export/import and independent read-back smoke test. Detection alone cannot satisfy Stage Result gates.
- `ModuleNotFoundError: yaml` from the system `quick_validate.py`: run that validator with a Python environment that already has PyYAML, or install it only with separate authorization. The bundled Skill scripts themselves need no PyYAML.
- Missing or changed `delivery/accepted/rN` files: validate the report and ledger. Keep the accepted revision intact and publish a new revision for approved changes; display edits belong in `working/`.

When reporting an issue, include the Skill `VERSION`, commands, sanitized output, and exact failing validator. Exclude proprietary geometry and local personal paths.
