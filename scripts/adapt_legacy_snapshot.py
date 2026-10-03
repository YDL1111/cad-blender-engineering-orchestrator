"""Create a non-destructive, explicitly unverified v1 snapshot view of legacy evidence."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from artifact_validation import file_sha256, load_json, validate_snapshot


IDENTITY_TRANSFORM = {
    "location": [0.0, 0.0, 0.0],
    "rotation_deg": [0.0, 0.0, 0.0],
    "scale": [1.0, 1.0, 1.0],
}


def _manifest_metadata(project_root: Path | None, revision: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if project_root is None:
        return None, []
    lineage: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    for current in range(1, revision + 1):
        relative = f"manifests/manifest-v{current}.json"
        path = (project_root / relative).resolve()
        if project_root.resolve() not in path.parents or not path.is_file():
            return None, []
        manifest = load_json(path)
        if current == revision:
            selected = manifest
        lineage.append({
            "revision": current,
            "parent_revision": current - 1 if current > 1 else None,
            "path": relative,
            "sha256": file_sha256(path),
        })
    return selected, lineage


def adapt_snapshot(data: dict[str, Any], project_root: Path | None = None) -> tuple[dict[str, Any], list[str]]:
    """Return an adapted copy and warnings; never mutate or overstate the source evidence."""
    adapted = copy.deepcopy(data)
    warnings: list[str] = []
    revision = adapted.get("manifest_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("legacy snapshot manifest_revision must be a positive integer")
    manifest, lineage = _manifest_metadata(project_root.resolve() if project_root else None, revision)
    if not lineage:
        digest = adapted.get("manifest_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("legacy snapshot has no usable manifest hash and no readable project Manifest chain")
        lineage = [{
            "revision": revision,
            "parent_revision": None,
            "path": f"manifests/manifest-v{revision}.json",
            "sha256": digest,
        }]
        warnings.append("Manifest ancestry could not be reconstructed; the single legacy revision is not strong ancestor proof.")
    else:
        warnings.append("Manifest files predate authenticated parent_manifest links; reconstructed lineage is legacy/unverified.")
    adapted["manifest_lineage"] = lineage
    if not adapted.get("units"):
        adapted["units"] = manifest.get("units") if isinstance(manifest, dict) else None
        warnings.append("units was absent and was copied from the consumed project Manifest.")
    if not adapted.get("coordinate_system"):
        adapted["coordinate_system"] = copy.deepcopy(manifest.get("coordinate_system")) if isinstance(manifest, dict) else None
        warnings.append("coordinate_system was absent and was copied from the consumed project Manifest.")
    objects = adapted.get("objects")
    if not isinstance(objects, list):
        raise ValueError("legacy snapshot objects must be an array")
    inferred_controlled = 0
    inferred_transforms = 0
    for item in objects:
        if not isinstance(item, dict):
            continue
        if "controlled" not in item:
            item["controlled"] = True
            inferred_controlled += 1
        if "transform" not in item:
            item["transform"] = copy.deepcopy(IDENTITY_TRANSFORM)
            inferred_transforms += 1
    if inferred_controlled:
        warnings.append(f"controlled=true was inferred for {inferred_controlled} legacy CAD objects; verify against the accepted Manifest.")
    if inferred_transforms:
        warnings.append(f"identity transforms were supplied for {inferred_transforms} objects because the legacy snapshot omitted transforms; these values are not measured evidence.")
    adapted["legacy_unverified"] = True
    adapted["migration_warnings"] = warnings
    adapted["migration"] = {
        "adapter_contract_version": "1.0",
        "source_schema_version": data.get("schema_version"),
        "schema_migration_only": True,
        "legacy_unverified": True,
        "inferred_fields": {
            "controlled": inferred_controlled,
            "transform": inferred_transforms,
        },
    }
    errors = validate_snapshot(adapted, adapted.get("artifact_type"))
    if errors:
        raise ValueError("adapted snapshot is invalid: " + "; ".join(errors))
    return adapted, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Adapt a legacy CAD/Blender snapshot into a new, explicitly unverified file without overwriting the source.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    source = args.input.resolve()
    output = args.output.resolve()
    if source == output:
        parser.error("output must differ from input; legacy evidence is immutable")
    if output.exists():
        parser.error("output already exists; refusing to overwrite")
    adapted, warnings = adapt_snapshot(load_json(source), args.project_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(adapted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"valid": True, "output": str(output), "legacy_unverified": True, "warnings": warnings}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
