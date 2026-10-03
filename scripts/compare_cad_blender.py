"""Compare typed CAD and Blender snapshots without application dependencies."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from artifact_validation import file_sha256, load_json, validate_manifest, validate_manifest_against_baseline, validate_manifest_lineage, validate_snapshot


def load(path: Path) -> dict[str, Any]:
    return load_json(path)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def typed_differences(left: Any, right: Any, tolerance: float, path: str = "$") -> list[dict[str, Any]]:
    """Recursively compare JSON values with tolerance only for finite numeric leaves."""
    left_type, right_type = _type_name(left), _type_name(right)
    if left_type != right_type:
        return [{"path": path, "reason": "type_mismatch", "cad_type": left_type, "blender_type": right_type, "cad": left, "blender": right}]
    if left_type == "number":
        if not math.isfinite(float(left)) or not math.isfinite(float(right)):
            return [{"path": path, "reason": "non_finite_number", "cad": left, "blender": right}]
        if not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance):
            return [{"path": path, "reason": "numeric_tolerance_exceeded", "cad": left, "blender": right, "tolerance": tolerance}]
        return []
    if left_type in {"string", "bool", "null"}:
        return [] if left == right else [{"path": path, "reason": "value_mismatch", "cad": left, "blender": right}]
    if left_type == "array":
        differences: list[dict[str, Any]] = []
        if len(left) != len(right):
            differences.append({"path": path, "reason": "array_length_mismatch", "cad_length": len(left), "blender_length": len(right)})
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            differences += typed_differences(left_item, right_item, tolerance, f"{path}[{index}]")
        return differences
    if left_type == "object":
        differences = []
        left_keys, right_keys = set(left), set(right)
        for key in sorted(left_keys - right_keys):
            differences.append({"path": f"{path}.{key}", "reason": "missing_key_in_blender"})
        for key in sorted(right_keys - left_keys):
            differences.append({"path": f"{path}.{key}", "reason": "extra_key_in_blender"})
        for key in sorted(left_keys & right_keys):
            differences += typed_differences(left[key], right[key], tolerance, f"{path}.{key}")
        return differences
    return [{"path": path, "reason": "unsupported_type", "cad_type": left_type, "blender_type": right_type}]


def compare_values(left: Any, right: Any, tolerance: float) -> bool:
    return not typed_differences(left, right, tolerance)


def object_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    objects = snapshot.get("objects")
    return {item["object_id"]: item for item in (objects if isinstance(objects, list) else []) if isinstance(item, dict) and isinstance(item.get("object_id"), str)}


def _add_typed(differences: list[dict[str, Any]], category: str, object_id: str, field: str, left: Any, right: Any, tolerance: float) -> None:
    for finding in typed_differences(left, right, tolerance, f"$.objects[{object_id}].{field}"):
        differences.append({"category": category, "object_id": object_id, "field": field, **finding})


def _merge_lineages(*lineages: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_revision: dict[int, dict[str, Any]] = {}
    differences: list[dict[str, Any]] = []
    for lineage in lineages:
        if not isinstance(lineage, list):
            continue
        for entry in lineage:
            if not isinstance(entry, dict) or not isinstance(entry.get("revision"), int) or isinstance(entry.get("revision"), bool):
                continue
            existing = by_revision.get(entry["revision"])
            if existing is not None and existing != entry:
                differences.append({"category": "manifest_lineage_conflict", "revision": entry["revision"], "first": existing, "second": entry})
            else:
                by_revision[entry["revision"]] = entry
    return [by_revision[key] for key in sorted(by_revision)], differences


def compare(
    cad: dict[str, Any], blender: dict[str, Any], manifest: dict[str, Any],
    project_root: str | Path | None = None, manifest_lineage: list[dict[str, Any]] | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    differences: list[dict[str, Any]] = []
    migration_warnings: list[dict[str, Any]] = []
    revisions = (manifest.get("revision"), cad.get("manifest_revision"), blender.get("manifest_revision"))
    if any(isinstance(value, int) and not isinstance(value, bool) and value > 1 for value in revisions) and project_root is None:
        differences.append({
            "category": "authentication_project_root_required",
            "finding": "revision > 1 comparison requires project_root and authenticated Manifest files",
        })
    tolerance_spec = manifest.get("comparison_tolerance")
    tolerance = tolerance_spec.get("value") if isinstance(tolerance_spec, dict) else None
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be a finite non-negative number from the accepted Manifest")
    for error in validate_manifest(manifest):
        differences.append({"category": "invalid_manifest", "finding": error})
    snapshot_ancestors: dict[str, set[int]] = {}
    for snapshot, expected, category in ((cad, "cad_snapshot", "invalid_cad_snapshot"), (blender, "blender_snapshot", "invalid_blender_snapshot")):
        if snapshot.get("legacy_unverified") is True:
            warnings = snapshot.get("migration_warnings") if isinstance(snapshot.get("migration_warnings"), list) else []
            migration_warnings += [{"snapshot": expected, "warning": warning} for warning in warnings if isinstance(warning, str)]
            differences.append({
                "category": "legacy_unverified_evidence",
                "snapshot": expected,
                "finding": "Adapter-supplied fields and pre-parent_manifest lineage cannot establish strong engineering equivalence.",
            })
        for error in validate_snapshot(snapshot, expected):
            differences.append({"category": category, "finding": error})
        lineage_errors, ancestors = validate_manifest_lineage(snapshot.get("manifest_lineage"), snapshot.get("manifest_revision"), project_root, snapshot.get("project_id"), require_authenticated=project_root is not None)
        for error in lineage_errors:
            differences.append({"category": f"invalid_{expected}_lineage", "finding": error})
        snapshot_ancestors[expected] = ancestors
    canonical_lineage = manifest_lineage
    if canonical_lineage is None:
        canonical_lineage, merge_differences = _merge_lineages(cad.get("manifest_lineage"), blender.get("manifest_lineage"))
        differences += merge_differences
    canonical_errors, final_ancestors = validate_manifest_lineage(canonical_lineage, manifest.get("revision"), project_root, manifest.get("project_id"), require_authenticated=project_root is not None)
    for error in canonical_errors:
        differences.append({"category": "invalid_accepted_manifest_lineage", "finding": error})
    canonical_by_revision = {entry.get("revision"): entry for entry in canonical_lineage or [] if isinstance(entry, dict)}
    final_entry = canonical_by_revision.get(manifest.get("revision"))
    if project_root is not None and isinstance(final_entry, dict) and isinstance(final_entry.get("path"), str):
        lineage_manifest_path = (Path(project_root).resolve() / final_entry["path"]).resolve()
        try:
            lineage_manifest = load_json(lineage_manifest_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            differences.append({"category": "accepted_manifest_lineage_read_error", "finding": str(exc)})
        else:
            if lineage_manifest != manifest:
                differences.append({"category": "accepted_manifest_content_mismatch", "finding": "supplied accepted Manifest differs from the final canonical lineage file"})
        if manifest_path is not None:
            supplied_path = Path(manifest_path).resolve()
            if supplied_path != lineage_manifest_path:
                differences.append({"category": "accepted_manifest_path_mismatch", "expected": str(lineage_manifest_path), "supplied": str(supplied_path)})
            elif final_entry.get("sha256") != file_sha256(supplied_path):
                differences.append({"category": "accepted_manifest_sha256_mismatch", "path": final_entry["path"]})
    for snapshot, expected in ((cad, "cad_snapshot"), (blender, "blender_snapshot")):
        consumed_revision = snapshot.get("manifest_revision")
        if consumed_revision not in final_ancestors:
            differences.append({"category": "snapshot_manifest_not_ancestor", "snapshot": expected, "consumed_revision": consumed_revision, "final_revision": manifest.get("revision")})
        snapshot_lineage = snapshot.get("manifest_lineage")
        for entry in (snapshot_lineage if isinstance(snapshot_lineage, list) else []):
            if isinstance(entry, dict) and canonical_by_revision.get(entry.get("revision")) != entry:
                differences.append({"category": "snapshot_manifest_lineage_conflict", "snapshot": expected, "revision": entry.get("revision")})
    if cad.get("project_id") != blender.get("project_id"):
        differences.append({"category": "project_mismatch", "cad": cad.get("project_id"), "blender": blender.get("project_id")})
    if cad.get("project_id") != manifest.get("project_id") or blender.get("project_id") != manifest.get("project_id"):
        differences.append({"category": "manifest_project_mismatch", "manifest": manifest.get("project_id")})
    for field in ("units", "coordinate_system"):
        if cad.get(field) != blender.get(field):
            differences.append({"category": "metadata_mismatch", "field": field, "cad": cad.get(field), "blender": blender.get(field)})
    for field in ("units", "coordinate_system"):
        if cad.get(field) != manifest.get(field) or blender.get(field) != manifest.get(field):
            differences.append({"category": "snapshot_manifest_metadata_mismatch", "field": field, "manifest": manifest.get(field), "cad": cad.get(field), "blender": blender.get(field)})

    cad_objects, blender_objects = object_map(cad), object_map(blender)
    manifest_objects_value = manifest.get("objects")
    manifest_objects = {item["object_id"]: item for item in (manifest_objects_value if isinstance(manifest_objects_value, list) else []) if isinstance(item, dict) and item.get("object_id")}
    manifest_ids = set(manifest_objects)
    if set(cad_objects) != manifest_ids:
        differences.append({"category": "cad_manifest_object_set_mismatch", "manifest_ids": sorted(manifest_ids), "cad_ids": sorted(cad_objects)})
    for object_id in sorted(set(cad_objects) & manifest_ids):
        for field in ("critical_dimensions", "parent_id"):
            _add_typed(differences, "cad_manifest_controlled_property_mismatch", object_id, field, manifest_objects[object_id].get(field), cad_objects[object_id].get(field), tolerance)
    for object_id in sorted(set(cad_objects) - set(blender_objects)):
        differences.append({"category": "missing_in_blender", "object_id": object_id})
    for object_id in sorted(set(blender_objects) - set(cad_objects)):
        if blender_objects[object_id].get("controlled", True):
            differences.append({"category": "extra_controlled_in_blender", "object_id": object_id})
    for object_id in sorted(set(cad_objects) & set(blender_objects)):
        if cad_objects[object_id].get("controlled") != blender_objects[object_id].get("controlled"):
            differences.append({"category": "controlled_flag_mismatch", "object_id": object_id})
        for field in ("critical_dimensions", "transform", "parent_id", "bounding_box_mm"):
            _add_typed(differences, "controlled_property_mismatch", object_id, field, cad_objects[object_id].get(field), blender_objects[object_id].get(field), tolerance)
    engineering_categories = {
        "cad_manifest_object_set_mismatch", "cad_manifest_controlled_property_mismatch",
        "missing_in_blender", "extra_controlled_in_blender", "controlled_flag_mismatch",
        "controlled_property_mismatch", "metadata_mismatch",
    }
    engineering_differences = [item for item in differences if item.get("category") in engineering_categories]
    return {
        "consistent": not differences,
        "tolerance": tolerance,
        "difference_count": len(differences),
        "engineering_difference_count": len(engineering_differences),
        "engineering_differences": engineering_differences,
        "migration_warnings": migration_warnings,
        "differences": differences,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare typed CAD and Blender controlled-object snapshots.")
    parser.add_argument("cad_snapshot", type=Path)
    parser.add_argument("blender_snapshot", type=Path)
    parser.add_argument("--manifest", type=Path, required=True, help="Accepted Project Manifest")
    parser.add_argument("--baseline", type=Path, required=True, help="Confirmed Requirements Baseline bound to the Manifest")
    parser.add_argument("--project-root", type=Path, help="When supplied, verify every Manifest lineage path and SHA-256")
    parser.add_argument("--manifest-lineage", type=Path, help="Optional JSON array or object containing the accepted final manifest_lineage; required when snapshots do not reach the supplied final Manifest")
    parser.add_argument("--legacy-unverified", action="store_true", help="Permit a pre-parent_manifest accepted Manifest for migration analysis only; never authenticates ancestry")
    parser.add_argument("--report", type=Path, help="Optional new JSON report path")
    args = parser.parse_args()
    try:
        manifest = load(args.manifest)
        revisions = (manifest.get("revision"), load(args.cad_snapshot).get("manifest_revision"), load(args.blender_snapshot).get("manifest_revision"))
        if any(isinstance(value, int) and not isinstance(value, bool) and value > 1 for value in revisions) and args.project_root is None and not args.legacy_unverified:
            raise ValueError("revision > 1 formal comparison requires --project-root; use --legacy-unverified only for non-formal migration analysis")
        validation_manifest = dict(manifest)
        if args.legacy_unverified and "parent_manifest" not in validation_manifest:
            revision = validation_manifest.get("revision")
            validation_manifest["parent_manifest"] = None if revision == 1 else {
                "revision": revision - 1,
                "path": f"manifests/manifest-v{revision - 1}.json",
                "sha256": "0" * 64,
            }
        binding_errors = validate_manifest_against_baseline(validation_manifest, load(args.baseline), args.baseline)
        if binding_errors:
            raise ValueError("invalid accepted Manifest/baseline binding: " + "; ".join(binding_errors))
        lineage = None
        if args.manifest_lineage:
            lineage_payload = json.loads(args.manifest_lineage.read_text(encoding="utf-8"))
            lineage = lineage_payload.get("manifest_lineage") if isinstance(lineage_payload, dict) else lineage_payload
            if not isinstance(lineage, list):
                raise ValueError("--manifest-lineage must contain a JSON array or an object with manifest_lineage")
        report = compare(load(args.cad_snapshot), load(args.blender_snapshot), manifest, args.project_root, lineage, args.manifest)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"consistent": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
