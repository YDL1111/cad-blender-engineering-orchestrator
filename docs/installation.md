# Installation and upgrades

The ZIP contains one top-level `cad-blender-engineering-orchestrator/` directory. Extract it and place that directory under `$HOME\.codex\skills\` on Windows. The result must be `$HOME\.codex\skills\cad-blender-engineering-orchestrator\SKILL.md`. For a manual installation in PowerShell, set `$archive` to the downloaded or locally built ZIP:

```powershell
$archive = 'C:\Downloads\cad-blender-engineering-orchestrator-<version>.zip'
$staging = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
$install = Join-Path $HOME '.codex\skills\cad-blender-engineering-orchestrator'
if (Test-Path -LiteralPath $install) { throw "Installation already exists: $install" }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $install) | Out-Null
Expand-Archive -LiteralPath $archive -DestinationPath $staging
if (-not (Test-Path -LiteralPath (Join-Path $staging 'cad-blender-engineering-orchestrator\SKILL.md'))) { throw 'Archive lacks the expected top-level Skill directory' }
Copy-Item -LiteralPath (Join-Path $staging 'cad-blender-engineering-orchestrator') -Destination $install -Recurse
```

To install version 0.1.0 from GitHub:

```powershell
New-Item -ItemType Directory -Force -Path (Join-Path $HOME '.codex\skills') | Out-Null
git clone --branch v0.1.0 https://github.com/YDL1111/cad-blender-engineering-orchestrator.git "$HOME\.codex\skills\cad-blender-engineering-orchestrator"
```

Do not run the clone command over an existing installation. For an upgrade, download or extract a new candidate into a separate directory, compare `VERSION` and `CHANGELOG.md`, then update only the Skill installation. Projects remain under the configured project root. Never overwrite `delivery/accepted/rN`; publish a new release revision through the Skill workflow.

Check the installation with `python "$HOME\.codex\skills\cad-blender-engineering-orchestrator\scripts\check_environment.py"` and `python "$HOME\.codex\skills\cad-blender-engineering-orchestrator\scripts\self_test.py"`. The detector is read-only. For removal, confirm the exact installed Skill path and run `Remove-Item -LiteralPath $install -Recurse` using that path only; leave the projects root untouched.

FreeCAD and Blender are not included. An executable path alone does not prove an export/read-back interface. Read [configuration](configuration.md) and [troubleshooting](troubleshooting.md) before a live project.
