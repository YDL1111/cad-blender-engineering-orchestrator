"""Read-only host preflight for a CAD-to-Blender project."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from environment import resolve_executable, resolve_projects_root, user_config_path


def _version(path: str) -> dict[str, str | None]:
    try:
        result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"value": None, "evidence": f"version query unavailable: {exc}"}
    lines = (result.stdout + "\n" + result.stderr).strip().splitlines()
    return {"value": lines[0].strip() if result.returncode == 0 and lines else None,
            "evidence": f"--version exit={result.returncode}"}


def inspect_environment(
    projects_root: str | Path | None = None,
    freecad_cmd: str | Path | None = None,
    blender_exe: str | Path | None = None,
    config_path: Path | None = None,
    *,
    environ=None,
    home: Path | None = None,
    query_versions: bool = True,
    common_candidates: dict[str, list[Path]] | None = None,
) -> dict:
    root, root_source = resolve_projects_root(projects_root, environ=environ, config_path=config_path, home=home)
    candidates = common_candidates or {}
    software = {
        "freecad": resolve_executable("freecad", freecad_cmd, environ=environ, common_candidates=candidates.get("freecad")),
        "blender": resolve_executable("blender", blender_exe, environ=environ, common_candidates=candidates.get("blender")),
    }
    blockers = []
    if sys.version_info < (3, 11):
        blockers.append({"capability": "python", "reason": "Python 3.11 or newer is required"})
    for kind, entry in software.items():
        if entry["blocker"]:
            blockers.append({"capability": kind, "reason": entry["blocker"]})
        else:
            entry["version"] = _version(entry["path"]) if query_versions else {"value": None, "evidence": "version query skipped"}
            if query_versions and entry["version"]["value"] is None:
                blockers.append({"capability": kind, "reason": f"{kind} executable was located but its --version probe failed: {entry['version']['evidence']}"})
        entry["engineering_export"] = "not_verified_by_read_only_preflight"
    return {
        "python": {"executable": sys.executable, "version": sys.version.split()[0]},
        "project_root": {"path": str(root), "source": root_source, "exists": root.is_dir()},
        "user_config": str(config_path or user_config_path(home)),
        "software": software,
        "required_export_capabilities": {
            "freecad": "controlled geometry export with units, axes, IDs and independent readback",
            "blender": "scene export/save with units, axes, IDs and independent readback",
            "status": "requires_project_smoke_test",
        },
        "blockers": blockers,
        "preflight_ready": not blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Python, project-root, FreeCAD, and Blender preflight.")
    parser.add_argument("--projects-root", type=Path)
    parser.add_argument("--freecad-cmd", type=Path)
    parser.add_argument("--blender-exe", type=Path)
    parser.add_argument("--config", type=Path, help="Optional user JSON configuration with projects_root")
    parser.add_argument("--skip-version-query", action="store_true")
    args = parser.parse_args()
    try:
        result = inspect_environment(args.projects_root, args.freecad_cmd, args.blender_exe, args.config, query_versions=not args.skip_version_query)
    except ValueError as exc:
        result = {"preflight_ready": False, "blockers": [{"capability": "configuration", "reason": str(exc)}]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["preflight_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
