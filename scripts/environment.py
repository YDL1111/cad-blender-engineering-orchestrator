"""Resolve user project and application locations without changing the host."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Mapping


CONFIG_RELATIVE = Path(".config") / "cad-blender-engineering-orchestrator" / "config.json"


def user_config_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / CONFIG_RELATIVE


def load_config(path: Path | None = None, home: Path | None = None) -> dict[str, str]:
    config_path = path or user_config_path(home)
    if not config_path.is_file():
        if path is not None:
            raise ValueError(f"user configuration file does not exist: {config_path}")
        return {}
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid user configuration {config_path}: {exc}") from exc
    if not isinstance(value, dict) or any(key != "projects_root" for key in value):
        raise ValueError("user configuration must be an object containing only projects_root")
    if "projects_root" in value and (not isinstance(value["projects_root"], str) or not value["projects_root"].strip()):
        raise ValueError("projects_root in user configuration must be a non-empty path string")
    return value


def resolve_projects_root(
    explicit: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: Path | None = None,
    home: Path | None = None,
) -> tuple[Path, str]:
    env = os.environ if environ is None else environ
    user_home = home or Path.home()
    if explicit is not None:
        chosen, source = explicit, "explicit"
    elif "CBE_PROJECTS_ROOT" in env:
        chosen, source = env["CBE_PROJECTS_ROOT"], "CBE_PROJECTS_ROOT"
    else:
        config = load_config(config_path, user_home)
        chosen, source = (config["projects_root"], "user_config") if "projects_root" in config else (user_home / "CBEngineeringProjects", "default")
    if not str(chosen).strip():
        raise ValueError(f"{source} project root is empty")
    selected = Path(chosen).expanduser()
    if not selected.is_absolute():
        raise ValueError(f"{source} project root must be an absolute path: {chosen}")
    return selected.resolve(), source


def _common_windows_candidates(kind: str, env: Mapping[str, str]) -> list[Path]:
    bases = [Path(env[key]) for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA") if env.get(key)]
    candidates: list[Path] = []
    for base in bases:
        if kind == "freecad":
            roots = [base, base / "Programs"]
            for root in roots:
                if root.is_dir():
                    for folder in sorted(root.glob("FreeCAD*")):
                        candidates.extend((folder / "bin" / "FreeCADCmd.exe", folder / "bin" / "freecadcmd.exe"))
        else:
            root = base / "Blender Foundation"
            if root.is_dir():
                candidates.extend(folder / "blender.exe" for folder in sorted(root.glob("Blender*")))
    return candidates


def resolve_executable(
    kind: str,
    explicit: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    common_candidates: list[Path] | None = None,
) -> dict[str, str | None]:
    if kind not in {"freecad", "blender"}:
        raise ValueError(f"unknown application: {kind}")
    env = os.environ if environ is None else environ
    key = "CBE_FREECAD_CMD" if kind == "freecad" else "CBE_BLENDER_EXE"
    names = ("FreeCADCmd.exe", "freecadcmd.exe", "FreeCADCmd") if kind == "freecad" else ("blender.exe", "blender")
    if explicit is not None or key in env:
        value, source = (explicit, "explicit") if explicit is not None else (env[key], key)
        path = Path(str(value)).expanduser()
        if not str(value).strip() or not path.is_absolute() or not path.is_file():
            return {"status": "blocked", "path": None, "source": source, "blocker": f"{source} {kind} executable is missing or is not an absolute file path: {value}"}
        return {"status": "located", "path": str(path.resolve()), "source": source, "blocker": None}
    for name in names:
        found = shutil.which(name, path=env.get("PATH", ""))
        if found:
            return {"status": "located", "path": str(Path(found).resolve()), "source": "PATH", "blocker": None}
    candidates = common_candidates if common_candidates is not None else _common_windows_candidates(kind, env)
    for path in candidates:
        if path.is_file():
            return {"status": "located", "path": str(path.resolve()), "source": "windows_install", "blocker": None}
    return {"status": "blocked", "path": None, "source": None, "blocker": f"{kind} executable not found; set {key}, pass an explicit path, or install it on PATH"}
