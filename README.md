# CAD Blender Engineering Orchestrator

Version **0.1.0** · Author **YDL1111** · [MIT License](LICENSE)

A Codex Skill for dimensioned work where FreeCAD owns controlled geometry and Blender builds a downstream presentation model. It coordinates requirements baselines, versioned Manifests, scoped Work Packets, independent reviews, Canonical Mesh comparison, and immutable `delivery/accepted/rN` releases with editable `working/` copies.

It is not for CAD-only edits, Blender-only art, pure 2D drawings, simulation, fabrication approval, code compliance, or licensed engineering sign-off.

The Skill is the user-facing controller. It delegates bounded CAD and Blender operations to agents and routes their results through independent review. FreeCAD/Blender are external tool interfaces, not bundled software. The Skill includes Python validators and templates; it does not install either application or create an MCP connection.

## Requirements and verification

- Windows 10/11, a Codex installation supporting local Skills, and Python 3.11 or newer for the bundled scripts.
- FreeCAD with a callable command/script interface and Blender with a callable executable/script interface for live CAD-to-Blender work. Configure paths when detection cannot find them.
- Verified locally: Python 3.11.9, FreeCAD 1.1.3 Revision 20260725, and Blender 5.2.1 LTS. The rc.9 live smoke test saved FCStd/STEP and blend files, reopened both applications in independent processes, rendered two 1920x1080 PNGs, and compared three objects (268 vertices, 524 triangles) with zero cross-software differences. The CAD cylinder tessellation has an approximately 0.0373 mm Y-axis approximation difference; this is not Blender drift. Other hosts and versions need their own export/read-back smoke test.
- `quick_validate.py` from `skill-creator` requires PyYAML in the Python environment used to invoke that validator. The Skill's own runtime scripts use the Python standard library.

## Install or remove

Install from [GitHub](https://github.com/YDL1111/cad-blender-engineering-orchestrator) in PowerShell:

```powershell
New-Item -ItemType Directory -Force -Path (Join-Path $HOME '.codex\skills') | Out-Null
git clone --branch v0.1.0 https://github.com/YDL1111/cad-blender-engineering-orchestrator.git "$HOME\.codex\skills\cad-blender-engineering-orchestrator"
```

For a manual installation, extract the release ZIP, then copy its top-level `cad-blender-engineering-orchestrator` directory into `$HOME\.codex\skills\`. Keep `SKILL.md` immediately inside that directory. To uninstall, remove only that installed Skill directory after confirming its exact path. Project directories are separate and are not part of installation or removal.

## Configure and check

Project root precedence: explicit `--projects-root` or user-provided path, `CBE_PROJECTS_ROOT`, `~/.config/cad-blender-engineering-orchestrator/config.json`, then `~/CBEngineeringProjects`. FreeCAD/Blender precedence: explicit CLI path, `CBE_FREECAD_CMD` or `CBE_BLENDER_EXE`, PATH, then common Windows installation directories. A specified invalid path is a blocker; detection does not silently fall back.

```powershell
$env:CBE_PROJECTS_ROOT = 'C:\Engineering Projects'
$env:CBE_FREECAD_CMD = 'C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe'
$env:CBE_BLENDER_EXE = 'C:\Program Files\Blender Foundation\Blender 4.3\blender.exe'
python scripts\check_environment.py
python scripts\scaffold_project.py example-project --dry-run
```

These paths are examples, not verified installations. See [configuration](docs/configuration.md) for the config file and precedence tests, [installation](docs/installation.md) for copying and upgrades, and [troubleshooting](docs/troubleshooting.md) for blockers.

## Small smoke test

Run `python scripts/check_environment.py` and `python scripts/self_test.py`, then ask Codex to use `$cad-blender-engineering-orchestrator` with [this request](examples/smoke-test-request.md). Confirm a requirements baseline and review gates before allowing a disposable test project. Verify FreeCAD save/export and a separate read-back process, then Blender import/save/export and separate read-back. Do not promote a Stage Result from executable detection alone.

## Engineering and support boundaries

The Skill enforces provenance, geometry comparisons, and delivery hashes; qualified humans remain responsible for engineering decisions, legal compliance, and approval. See [security and engineering boundaries](docs/security-and-engineering-boundaries.md).

Upgrade by installing a new Skill version after comparing `VERSION` and `CHANGELOG.md`; do not copy the Skill over an engineering project or replace any `accepted/rN`. Report issues through the approved repository's issue tracker after publication, including Skill version, Python/FreeCAD/Blender versions, sanitized detector output, reproduction steps, and failing validator output. Remove project paths, personal data, and proprietary models before posting.

Licensed under [MIT](LICENSE), copyright 2026 YDL1111. FreeCAD and Blender are external applications with their own licenses and are not included in this package.

Later releases preserve existing same-name files under `working/`. To view a newer accepted model, copy it into a separate working directory; do not assume an old working file was automatically refreshed.
