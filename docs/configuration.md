# Configuration

Project roots resolve in this order: explicit `--projects-root` or a path supplied by the user, `CBE_PROJECTS_ROOT`, user config, then `~/CBEngineeringProjects`. The default user config is `~/.config/cad-blender-engineering-orchestrator/config.json` and contains only:

```json
{"projects_root": "C:/Engineering Projects"}
```

Pass `--config <absolute-path>` to use a different existing JSON file. An invalid higher-priority value is reported as an error; it never falls through to another root. Project IDs are validated before scaffold creation, and an existing target is never overwritten.

For executables, `--freecad-cmd` and `--blender-exe` outrank `CBE_FREECAD_CMD` and `CBE_BLENDER_EXE`, then PATH, then common Windows installations under Program Files or LocalAppData. Use a full path in quotes when it contains spaces:

```powershell
python scripts\check_environment.py --projects-root 'C:\Engineering Projects' --freecad-cmd 'C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe' --blender-exe 'C:\Program Files\Blender Foundation\Blender 4.3\blender.exe'
python scripts\scaffold_project.py example-project --projects-root 'C:\Engineering Projects' --dry-run
```

The detector reports Python, selected root and source, executable path and source, version query evidence, export capability status, and blockers. It never certifies geometry export or independent read-back. An operator must prove those operations with a disposable smoke project before formal Stage Result promotion. Template strings `<FREECAD_CMD>` and `<PROJECT_ROOT>` are substitution markers; replace them with actual values and recompute the canonical command digest when producing evidence.
