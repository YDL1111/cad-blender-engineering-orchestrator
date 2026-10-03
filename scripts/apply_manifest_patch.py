"""Apply a restricted, hash-bound Manifest Patch to a new file."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from artifact_validation import file_sha256, load_json, validate_manifest_against_baseline, validate_manifest_patch

PROTECTED_PATHS = {
    "/schema_version", "/artifact_type", "/project_id", "/revision",
    "/parent_manifest", "/requirements_revision", "/requirements_baseline",
}


def tokens(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must start with '/'")
    return [item.replace("~1", "/").replace("~0", "~") for item in pointer[1:].split("/")]


def apply_operation(document: object, operation: dict) -> None:
    parts = tokens(operation["path"])
    if not parts:
        raise ValueError("root replacement/removal is not allowed")
    parent = document
    for part in parts[:-1]:
        if isinstance(parent, dict) and part in parent:
            parent = parent[part]
        elif isinstance(parent, list) and part.isdigit() and int(part) < len(parent):
            parent = parent[int(part)]
        else:
            raise ValueError(f"operation path does not exist: {operation['path']}")
    leaf = parts[-1]
    op = operation["op"]
    if isinstance(parent, dict):
        if op in {"replace", "remove"} and leaf not in parent:
            raise ValueError(f"operation target does not exist: {operation['path']}")
        if op == "remove":
            del parent[leaf]
        else:
            parent[leaf] = copy.deepcopy(operation["value"])
    elif isinstance(parent, list) and leaf.isdigit():
        index = int(leaf)
        if op == "add" and index == len(parent):
            parent.append(copy.deepcopy(operation["value"]))
        elif index < len(parent):
            if op == "remove":
                parent.pop(index)
            else:
                parent[index] = copy.deepcopy(operation["value"])
        else:
            raise ValueError(f"array index out of range: {operation['path']}")
    else:
        raise ValueError(f"operation parent is not addressable: {operation['path']}")


def apply_patch(base: dict, patch: dict, base_path: Path, baseline: dict, baseline_path: Path, project_root: Path) -> dict:
    errors = validate_manifest_against_baseline(base, baseline, baseline_path) + validate_manifest_patch(patch)
    if errors:
        raise ValueError("; ".join(errors))
    if patch["project_id"] != base["project_id"] or patch["base_revision"] != base["revision"]:
        raise ValueError("patch project/base revision does not match Manifest")
    if patch["base_sha256"] != file_sha256(base_path):
        raise ValueError("patch base_sha256 does not match Manifest file")
    root = project_root.resolve()
    for operation in patch["operations"]:
        pointer = operation["path"]
        if any(pointer == protected or pointer.startswith(protected + "/") for protected in PROTECTED_PATHS):
            raise ValueError(f"patch cannot modify protected Manifest path: {pointer}")
    for relative in patch["evidence_paths"]:
        evidence = (root / relative).resolve()
        if root not in evidence.parents or not evidence.is_file():
            raise ValueError(f"patch evidence is missing or outside project root: {relative}")
    result = copy.deepcopy(base)
    for operation in patch["operations"]:
        apply_operation(result, operation)
    result["parent_manifest"] = {
        "revision": base["revision"],
        "path": base_path.resolve().relative_to(root).as_posix(),
        "sha256": file_sha256(base_path),
    }
    result["revision"] = patch["proposed_revision"]
    errors = validate_manifest_against_baseline(result, baseline, baseline_path)
    if errors:
        raise ValueError("resulting Manifest is invalid: " + "; ".join(errors))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a validated Manifest Patch to a new non-existing output file.")
    parser.add_argument("base_manifest", type=Path)
    parser.add_argument("patch", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        root = args.project_root.resolve()
        manifests_root = (root / "manifests").resolve()
        for label, path in (("base Manifest", args.base_manifest), ("patch", args.patch), ("baseline", args.baseline)):
            resolved = path.resolve()
            if root not in resolved.parents or not resolved.is_file():
                raise ValueError(f"{label} must be an existing file inside project_root")
        output = args.output.resolve()
        if manifests_root != output.parent and manifests_root not in output.parents:
            raise ValueError("output must be a new file inside project_root/manifests")
        result = apply_patch(load_json(args.base_manifest), load_json(args.patch), args.base_manifest, load_json(args.baseline), args.baseline, args.project_root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"applied": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"applied": True, "output": str(args.output.resolve()), "revision": result["revision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
