"""Safely create a project workspace from the bundled JSON templates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from artifact_validation import PROJECT_ID_RE
from environment import resolve_projects_root

DIRECTORIES = ("inputs", "requirements", "manifests", "work-packets", "cad", "blender", "reviews", "previews", "render", "reports", "delivery", "delivery/accepted", "working", "working/cad", "working/blender")


def safe_target(root: Path, project_id: str) -> Path:
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError("project-id must be lowercase letters/digits/hyphens, 1-64 chars")
    root = root.resolve()
    target = (root / project_id).resolve()
    if target.parent != root:
        raise ValueError("project path escapes the selected root")
    return target


def create_project(root: Path, project_id: str, templates: Path, dry_run: bool = False) -> Path:
    target = safe_target(root, project_id)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing project: {target}")
    if dry_run:
        return target
    target.mkdir(parents=True, exist_ok=False)
    for directory in DIRECTORIES:
        (target / directory).mkdir()
    state = json.loads((templates / "project-state.json").read_text(encoding="utf-8"))
    state["project_id"] = project_id
    (target / "project-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checklist = json.loads((templates / "delivery-checklist.json").read_text(encoding="utf-8"))
    checklist["project_id"] = project_id
    (target / "delivery" / "delivery-checklist.json").write_text(json.dumps(checklist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a non-overwriting engineering project workspace.")
    parser.add_argument("project_id", help="Lowercase project identifier")
    parser.add_argument("--projects-root", type=Path, help="Explicit parent directory for projects")
    parser.add_argument("--config", type=Path, help="User configuration JSON; used after CBE_PROJECTS_ROOT")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the target without writing")
    args = parser.parse_args()
    templates = Path(__file__).resolve().parent.parent / "assets" / "templates"
    try:
        root, _ = resolve_projects_root(args.projects_root, config_path=args.config)
        target = create_project(root, args.project_id, templates, args.dry_run)
    except (ValueError, FileExistsError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"created": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"created": not args.dry_run, "dry_run": args.dry_run, "project_path": str(target)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
