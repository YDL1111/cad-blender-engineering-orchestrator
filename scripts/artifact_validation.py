"""Standard-library validation helpers for orchestrator JSON artifacts."""

from __future__ import annotations

import json
import re
import hashlib
import math
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

STATUSES = {"confirmed", "assumed", "unknown", "conflict", "blocked"}
DOMAINS = {"architecture_interior", "mep_coordination", "mechanical_product", "facility_layout"}
PROJECT_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SKILL_ROOT = Path(__file__).resolve().parent.parent
STATE_MACHINE_PATH = SKILL_ROOT / "assets" / "contracts" / "state-machine.json"


def _load_state_machine() -> tuple[set[str], dict[str, set[str]]]:
    value = json.loads(STATE_MACHINE_PATH.read_text(encoding="utf-8"))
    states = value.get("states")
    if value.get("schema_version") != "1.0" or value.get("artifact_type") != "state_machine_definition" or not isinstance(states, dict):
        raise RuntimeError("invalid state-machine.json contract")
    transitions: dict[str, set[str]] = {}
    for state, targets in states.items():
        if not isinstance(state, str) or not isinstance(targets, list) or any(not isinstance(item, str) for item in targets):
            raise RuntimeError("invalid state-machine.json states mapping")
        transitions[state] = set(targets)
    unknown = set().union(*transitions.values()) - set(transitions) if transitions else set()
    if unknown or value.get("initial_state") not in transitions:
        raise RuntimeError("state-machine.json references unknown states")
    return set(transitions), transitions


# These names remain public for compatibility, but their only definition source is the JSON contract.
STATES, TRANSITIONS = _load_state_machine()


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("root must be a JSON object")
    return value


def missing(data: dict[str, Any], keys: list[str]) -> list[str]:
    return [f"missing required field: {key}" for key in keys if key not in data]


def check_identity(data: dict[str, Any], artifact_type: str) -> list[str]:
    errors = missing(data, ["schema_version", "artifact_type", "project_id", "revision"])
    if data.get("schema_version") != "1.0":
        errors.append("schema_version must be '1.0'")
    if data.get("artifact_type") != artifact_type:
        errors.append(f"artifact_type must be '{artifact_type}'")
    if not PROJECT_ID_RE.fullmatch(str(data.get("project_id", ""))):
        errors.append("project_id must be lowercase letters/digits/hyphens, 1-64 chars")
    if not _strict_int(data.get("revision"), 1):
        errors.append("revision must be an integer >= 1")
    return errors


def check_tracked(value: Any, location: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{location} must be an object"]
    errors = missing(value, ["source", "status", "confidence", "reason", "blocks"])
    if value.get("status") not in STATUSES:
        errors.append(f"{location}.status must be one of {sorted(STATUSES)}")
    confidence = value.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        errors.append(f"{location}.confidence must be a number from 0 to 1")
    if not isinstance(value.get("blocks"), list):
        errors.append(f"{location}.blocks must be an array")
    return errors


def walk_tracked(value: Any, location: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        marker_keys = {"value", "source", "status", "confidence", "reason", "blocks"}
        if marker_keys.intersection(value):
            errors.extend(check_tracked(value, location))
        else:
            for key, child in value.items():
                errors.extend(walk_tracked(child, f"{location}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(walk_tracked(child, f"{location}[{index}]"))
    return errors


def blocking_items(value: Any, location: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        if value.get("status") in {"unknown", "conflict", "blocked"} and value.get("blocks"):
            hits.append(location)
        for key, child in value.items():
            hits.extend(blocking_items(child, f"{location}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(blocking_items(child, f"{location}[{index}]"))
    return hits


def unsafe_relative_paths(values: Any, location: str) -> list[str]:
    if not isinstance(values, list):
        return [f"{location} must be an array"]
    errors: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value:
            errors.append(f"{location}[{index}] must be a non-empty relative path")
            continue
        normalized = value.replace("\\", "/")
        segments = normalized.split("/")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        invalid_segment = any(
            segment in {"", ".", ".."} or segment.rstrip(" .") != segment or segment.split(".", 1)[0].upper() in reserved
            for segment in segments
        )
        if re.match(r"^[A-Za-z]:", normalized) or normalized.startswith("/") or ":" in normalized or invalid_segment:
            errors.append(f"{location}[{index}] must stay relative to the project root")
    return errors


def relative_path_set(values: Any) -> set[str]:
    return {value for value in values if isinstance(value, str) and value} if isinstance(values, list) else set()


def _strict_int(value: Any, minimum: int | None = None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and (minimum is None or value >= minimum)


def canonical_project_path(value: Any) -> str | None:
    """Return a Windows-safe, case-insensitive canonical project-relative identity."""
    if unsafe_relative_paths([value], "path"):
        return None
    root = Path("C:/__codex_project_root__").resolve()
    candidate = (root / str(value).replace("\\", "/")).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return str(candidate).replace("\\", "/").casefold()


def _canonical_paths_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right.rstrip("/") + "/") or right.startswith(left.rstrip("/") + "/")


def validate_writer_boundary(data: dict[str, Any], stage: str, review: bool = False) -> list[str]:
    errors: list[str] = []
    inputs, outputs, scope = data.get("input_paths"), data.get("output_paths"), data.get("writer_scope")
    for field, values in (("input_paths", inputs), ("output_paths", outputs), ("writer_scope", scope)):
        errors += unsafe_relative_paths(values, field)
        if not isinstance(values, list) or not values:
            errors.append(f"{field} must be a non-empty array")
    if not all(isinstance(values, list) for values in (inputs, outputs, scope)):
        return errors
    canonical_inputs = [canonical_project_path(value) for value in inputs]
    canonical_outputs = [canonical_project_path(value) for value in outputs]
    canonical_scope = [canonical_project_path(value) for value in scope]
    if None in canonical_inputs + canonical_outputs + canonical_scope:
        return errors
    if len(set(canonical_outputs)) != len(canonical_outputs) or len(set(canonical_scope)) != len(canonical_scope):
        errors.append("output_paths and writer_scope must contain unique canonical paths")
    if len(set(canonical_inputs)) != len(canonical_inputs):
        errors.append("input_paths must contain unique canonical paths")
    for field, values in (("output_paths", canonical_outputs), ("writer_scope", canonical_scope)):
        for index, left in enumerate(values):
            for right in values[index + 1:]:
                if left is not None and right is not None and _canonical_paths_overlap(left, right):
                    errors.append(f"{field} entries must not overlap as parent/child paths")
                    break
    if set(canonical_outputs) != set(canonical_scope):
        errors.append("output_paths and writer_scope must be exactly equal after Windows canonicalization")
    writable = set(canonical_outputs) | set(canonical_scope)
    if any(_canonical_paths_overlap(input_path, writable_path) for input_path in canonical_inputs for writable_path in writable):
        errors.append("read-only input_paths must not overlap or contain output_paths/writer_scope after Windows canonicalization")
    prefixes = {"cad": ("cad/",), "blender": ("blender/",), "review": ("reviews/",)}
    allowed = prefixes["review" if review else stage]
    for field, values in (("output_paths", outputs), ("writer_scope", scope)):
        for index, value in enumerate(values):
            normalized = str(value).replace("\\", "/").casefold()
            if not any(normalized.startswith(prefix) for prefix in allowed):
                errors.append(f"{field}[{index}] is outside the {stage} stage directory whitelist")
            if normalized.startswith(("requirements/", "manifests/")) or normalized == "project-state.json":
                errors.append(f"{field}[{index}] targets an orchestrator-owned authoritative path")
            filename = normalized.rsplit("/", 1)[-1]
            stem = filename[:-5] if filename.endswith(".json") else filename
            reserved_stems = ("project-state", "stage-result", "delivery-report", "requirements-baseline", "work-packet", "review-work-packet")
            disguised_manifest = stem == "manifest" or stem.startswith("manifest-v")
            if not review and (disguised_manifest or any(stem == prefix or stem.startswith(prefix + "-") for prefix in reserved_stems) or "accepted-result" in stem):
                errors.append(f"{field}[{index}] targets an orchestrator-owned authoritative artifact")
    return errors


def _valid_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def duplicate_ids(items: Any, location: str = "items") -> list[str]:
    if not isinstance(items, list):
        return [f"{location} must be an array"]
    seen: set[str] = set()
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("object_id"), str) or not item["object_id"]:
            errors.append(f"{location}[{index}].object_id must be a non-empty string")
            continue
        object_id = item["object_id"]
        if object_id in seen:
            errors.append(f"duplicate object_id: {object_id}")
        seen.add(object_id)
    return errors


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _controlled_deviation(cad_value: Any, blender_value: Any) -> float | None:
    """Return max absolute component deviation for finite scalar/vector controls; exact values use 0/inf."""
    if _finite_number(cad_value) and _finite_number(blender_value):
        return abs(float(cad_value) - float(blender_value))
    if isinstance(cad_value, (list, tuple)) and isinstance(blender_value, (list, tuple)) and len(cad_value) == len(blender_value) and cad_value:
        parts = [_controlled_deviation(left, right) for left, right in zip(cad_value, blender_value)]
        return max(parts) if all(part is not None for part in parts) else None
    if isinstance(cad_value, dict) and isinstance(blender_value, dict) and set(cad_value) == set(blender_value) and cad_value:
        parts = [_controlled_deviation(cad_value[key], blender_value[key]) for key in cad_value]
        return max(parts) if all(part is not None for part in parts) else None
    if isinstance(cad_value, (str, type(None), bool)) and type(cad_value) is type(blender_value):
        return 0.0 if cad_value == blender_value else math.inf
    return None


def validate_typed_value(value: Any, location: str) -> list[str]:
    """Validate JSON-compatible typed values while rejecting non-finite numeric leaves."""
    if value is None or isinstance(value, (str, bool)):
        return []
    if _finite_number(value):
        return []
    if isinstance(value, list):
        errors: list[str] = []
        for index, child in enumerate(value):
            errors += validate_typed_value(child, f"{location}[{index}]")
        return errors
    if isinstance(value, dict):
        errors = []
        for key, child in value.items():
            if not isinstance(key, str) or not key:
                errors.append(f"{location} keys must be non-empty strings")
            else:
                errors += validate_typed_value(child, f"{location}.{key}")
        return errors
    return [f"{location} contains unsupported or non-finite value of type {type(value).__name__}"]


def validate_coordinate_system(value: Any, location: str = "coordinate_system") -> list[str]:
    if not isinstance(value, dict):
        return [f"{location} must be an object"]
    errors = [f"{location}.{key} is required" for key in ("handedness", "up_axis", "forward_axis", "origin") if key not in value]
    if not isinstance(value.get("handedness"), str) or value.get("handedness") not in {"left", "right"}:
        errors.append(f"{location}.handedness must be left or right")
    for key in ("up_axis", "forward_axis"):
        if not isinstance(value.get(key), str) or value.get(key) not in {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}:
            errors.append(f"{location}.{key} must be a signed principal axis")
    up_axis, forward_axis = value.get("up_axis"), value.get("forward_axis")
    if isinstance(up_axis, str) and isinstance(forward_axis, str) and up_axis[-1:] == forward_axis[-1:]:
        errors.append(f"{location}.up_axis and forward_axis must not be collinear")
    if not isinstance(value.get("origin"), str) or not value.get("origin"):
        errors.append(f"{location}.origin must be a non-empty string")
    return errors


def validate_manifest_lineage(
    lineage: Any,
    final_revision: Any,
    project_root: str | Path | None = None,
    project_id: str | None = None,
    artifact_hashes: dict[str, str] | None = None,
    require_authenticated: bool = False,
) -> tuple[list[str], set[int]]:
    """Validate an exact, immutable parent chain and optionally its files and hashes."""
    errors: list[str] = []
    if not isinstance(lineage, list) or not lineage:
        return ["manifest_lineage must be a non-empty array"], set()
    by_revision: dict[int, dict[str, Any]] = {}
    manifests_by_revision: dict[int, dict[str, Any]] = {}
    seen_paths: set[str] = set()
    root = Path(project_root).resolve() if project_root is not None else None
    for index, entry in enumerate(lineage):
        location = f"manifest_lineage[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{location} must be an object")
            continue
        errors += [f"{location}.{key} is required" for key in ("revision", "parent_revision", "path", "sha256") if key not in entry]
        revision, parent, relative, digest = entry.get("revision"), entry.get("parent_revision"), entry.get("path"), entry.get("sha256")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            errors.append(f"{location}.revision must be an integer >= 1")
            continue
        if revision in by_revision:
            errors.append(f"duplicate manifest lineage revision: {revision}")
        by_revision[revision] = entry
        if parent is not None and (not isinstance(parent, int) or isinstance(parent, bool) or parent < 1 or parent >= revision):
            errors.append(f"{location}.parent_revision must be null or an earlier positive revision")
        errors += unsafe_relative_paths([relative], f"{location}.path")
        if isinstance(relative, str):
            if relative in seen_paths:
                errors.append(f"duplicate manifest lineage path: {relative}")
            seen_paths.add(relative)
        if not SHA256_RE.fullmatch(str(digest or "")):
            errors.append(f"{location}.sha256 must be a lowercase SHA-256 digest")
        if artifact_hashes is not None and isinstance(relative, str) and artifact_hashes.get(relative) != digest:
            errors.append(f"{location}.sha256 must equal artifact_hashes for its path")
        if root is not None and isinstance(relative, str):
            manifest_path = (root / relative).resolve()
            if root not in manifest_path.parents or not manifest_path.is_file():
                errors.append(f"Manifest lineage file is missing or outside project root: {relative}")
            else:
                actual_digest = file_sha256(manifest_path)
                if digest != actual_digest:
                    errors.append(f"Manifest lineage SHA-256 mismatch: {relative}")
                try:
                    manifest = load_json(manifest_path)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(f"invalid Manifest lineage file {relative}: {exc}")
                else:
                    manifests_by_revision[revision] = manifest
                    manifest_for_validation = manifest
                    if not require_authenticated and "parent_manifest" not in manifest:
                        manifest_for_validation = dict(manifest)
                        manifest_for_validation["parent_manifest"] = None if revision == 1 else {
                            "revision": entry.get("parent_revision"),
                            "path": "legacy-unverified",
                            "sha256": "0" * 64,
                        }
                    errors += [f"{relative}: {item}" for item in validate_manifest(manifest_for_validation)]
                    if manifest.get("revision") != revision:
                        errors.append(f"Manifest lineage revision mismatch: {relative}")
                    if project_id is not None and manifest.get("project_id") != project_id:
                        errors.append(f"Manifest lineage project mismatch: {relative}")
    ancestors: set[int] = set()
    cursor = final_revision
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 1:
        errors.append("final Manifest revision must be an integer >= 1")
    else:
        while cursor is not None:
            if cursor in ancestors:
                errors.append("Manifest lineage contains a cycle")
                break
            entry = by_revision.get(cursor)
            if entry is None:
                errors.append(f"Manifest lineage is missing revision {cursor}")
                break
            ancestors.add(cursor)
            parent = entry.get("parent_revision")
            if parent is not None and (not isinstance(parent, int) or isinstance(parent, bool) or parent < 1):
                break
            cursor = parent
    if set(by_revision) != ancestors:
        errors.append("manifest_lineage must contain exactly the final Manifest and its ancestor chain; branches and unrelated revisions are forbidden")
    if require_authenticated:
        if root is None:
            errors.append("authenticated Manifest lineage requires project_root and actual Manifest files")
        else:
            for revision in sorted(ancestors):
                manifest = manifests_by_revision.get(revision)
                entry = by_revision.get(revision)
                if not isinstance(manifest, dict) or not isinstance(entry, dict):
                    continue
                parent_revision = entry.get("parent_revision")
                parent_ref = manifest.get("parent_manifest")
                if parent_revision is None:
                    if parent_ref is not None:
                        errors.append(f"Manifest revision {revision} root must have parent_manifest null")
                    continue
                parent_entry = by_revision.get(parent_revision)
                expected = {
                    "revision": parent_revision,
                    "path": parent_entry.get("path") if isinstance(parent_entry, dict) else None,
                    "sha256": parent_entry.get("sha256") if isinstance(parent_entry, dict) else None,
                }
                if parent_ref != expected:
                    errors.append(f"Manifest revision {revision} parent_manifest does not authenticate its actual parent file")
        if errors:
            return errors, set()
    return errors, ancestors


def validate_snapshot(data: dict[str, Any], expected_type: str | None = None) -> list[str]:
    artifact_type = expected_type or data.get("artifact_type")
    if artifact_type not in {"cad_snapshot", "blender_snapshot"}:
        return ["artifact_type must be cad_snapshot or blender_snapshot"]
    errors = check_identity(data, artifact_type)
    errors += missing(data, ["manifest_revision", "manifest_lineage", "units", "coordinate_system", "objects"])
    if not isinstance(data.get("manifest_revision"), int) or isinstance(data.get("manifest_revision"), bool) or data.get("manifest_revision", 0) < 1:
        errors.append("manifest_revision must be an integer >= 1")
    lineage_errors, _ = validate_manifest_lineage(data.get("manifest_lineage"), data.get("manifest_revision"))
    errors += lineage_errors
    if not isinstance(data.get("units"), str) or not data.get("units"):
        errors.append("units must be a non-empty string")
    errors += validate_coordinate_system(data.get("coordinate_system"))
    objects = data.get("objects")
    errors += duplicate_ids(objects, "objects")
    if not isinstance(objects, list) or not objects:
        errors.append("objects must be a non-empty array")
        return errors
    ids = {item.get("object_id") for item in objects if isinstance(item, dict) and isinstance(item.get("object_id"), str)}
    parent_by_id: dict[str, str | None] = {}
    for index, item in enumerate(objects):
        location = f"objects[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{location} must be an object")
            continue
        errors += [f"{location}.{key} is required" for key in ("object_id", "controlled", "parent_id", "critical_dimensions", "transform") if key not in item]
        if not isinstance(item.get("controlled"), bool):
            errors.append(f"{location}.controlled must be boolean")
        parent = item.get("parent_id")
        if parent is not None and (not isinstance(parent, str) or not parent):
            errors.append(f"{location}.parent_id must be a non-empty string or null")
        elif parent == item.get("object_id"):
            errors.append(f"{location}.parent_id cannot reference itself")
        elif parent is not None and parent not in ids:
            errors.append(f"{location}.parent_id must reference an object_id in the snapshot")
        if isinstance(item.get("object_id"), str):
            parent_by_id[item["object_id"]] = parent if isinstance(parent, str) else None
        dimensions = item.get("critical_dimensions")
        if not isinstance(dimensions, dict) or not dimensions:
            errors.append(f"{location}.critical_dimensions must be a non-empty object")
        else:
            errors += validate_typed_value(dimensions, f"{location}.critical_dimensions")
        transform = item.get("transform")
        if not isinstance(transform, dict) or set(transform) != {"location", "rotation_deg", "scale"}:
            errors.append(f"{location}.transform must contain exactly location, rotation_deg, and scale")
        else:
            for name in ("location", "rotation_deg", "scale"):
                vector = transform.get(name)
                if not isinstance(vector, list) or len(vector) != 3 or any(not _finite_number(value) for value in vector):
                    errors.append(f"{location}.transform.{name} must contain three finite numbers")
    reported_cycles: set[frozenset[str]] = set()
    for object_id in sorted(parent_by_id):
        order: list[str] = []
        positions: dict[str, int] = {}
        cursor: str | None = object_id
        while cursor is not None and cursor in parent_by_id:
            if cursor in positions:
                cycle = frozenset(order[positions[cursor]:])
                if cycle not in reported_cycles:
                    errors.append(f"object parent hierarchy contains a cycle: {sorted(cycle)}")
                    reported_cycles.add(cycle)
                break
            positions[cursor] = len(order)
            order.append(cursor)
            cursor = parent_by_id[cursor]
    return errors


def validate_geometry_fidelity(value: Any, location: str = "geometry_fidelity") -> list[str]:
    if not isinstance(value, dict):
        return [f"{location} must be an object"]
    errors = [f"{location}.{key} is required" for key in ("level", "permitted_deviation_mm", "required_checks", "intended_use") if key not in value]
    level = value.get("level")
    if level not in {"exact_mesh", "simplified_mesh", "envelope_proxy"}:
        errors.append(f"{location}.level is invalid")
    deviation = value.get("permitted_deviation_mm")
    if not _finite_number(deviation) or deviation < 0:
        errors.append(f"{location}.permitted_deviation_mm must be a finite non-negative number")
    checks = value.get("required_checks")
    if not isinstance(checks, list) or not checks or any(not isinstance(item, str) or not item for item in checks):
        errors.append(f"{location}.required_checks must be a non-empty string array")
    use = str(value.get("intended_use", "")).lower()
    if not use:
        errors.append(f"{location}.intended_use must be non-empty")
    if level == "envelope_proxy" and any(term in use for term in ("face-accurate", "face accurate", "final presentation", "photoreal")):
        errors.append(f"{location}.intended_use overclaims envelope_proxy fidelity")
    if level == "exact_mesh" and isinstance(checks, list) and not any("topolog" in item.lower() or "mesh" in item.lower() for item in checks):
        errors.append(f"{location}.exact_mesh requires mesh/topology evidence")
    return errors


def validate_baseline(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "requirements_baseline")
    errors += missing(data, ["state", "raw_request", "domain", "target_level", "goals", "exclusions", "inputs", "deliverables", "requirements", "unresolved_items", "confirmation"])
    if data.get("state") != "baseline_confirmed":
        errors.append("state must be 'baseline_confirmed'")
    if data.get("domain") not in DOMAINS:
        errors.append(f"domain must be one of {sorted(DOMAINS)}")
    errors += walk_tracked(data.get("requirements", []), "$.requirements")
    for index, item in enumerate(data.get("requirements", [])):
        if isinstance(item, dict) and item.get("status") == "assumed":
            acceptance = item.get("acceptance", {})
            actor = str(acceptance.get("accepted_by", "")).lower()
            if actor in {"", "orchestrator", "agent", "system"} or not acceptance.get("evidence"):
                errors.append(f"requirements[{index}] assumed value requires human acceptance and evidence")
    for hit in blocking_items(data.get("requirements", []), "$.requirements"):
        errors.append(f"baseline contains next-stage blocker at {hit}")
    for hit in blocking_items(data.get("unresolved_items", []), "$.unresolved_items"):
        errors.append(f"baseline contains unresolved next-stage blocker at {hit}")
    confirmation = data.get("confirmation") if isinstance(data.get("confirmation"), dict) else {}
    confirmed_by = str(confirmation.get("confirmed_by", "")).lower()
    if confirmed_by in {"", "orchestrator", "agent", "system"}:
        errors.append("confirmation.confirmed_by must identify the user or another human authority")
    return errors


def validate_manifest(data: dict[str, Any]) -> list[str]:
    required = ["parent_manifest", "domain", "target_level", "units", "coordinate_system", "comparison_tolerance", "source_files", "requirements_revision", "requirements_baseline", "objects", "systems", "constraints", "assumptions", "unresolved_items", "stage_status", "expected_outputs", "review_profiles"]
    errors = check_identity(data, "project_manifest") + missing(data, required)
    if data.get("domain") not in DOMAINS:
        errors.append(f"domain must be one of {sorted(DOMAINS)}")
    if data.get("stage_status") not in STATES:
        errors.append("stage_status is not an allowed project state")
    parent_manifest = data.get("parent_manifest")
    if data.get("revision") == 1:
        if parent_manifest is not None:
            errors.append("revision 1 Manifest must have parent_manifest null")
    elif _strict_int(data.get("revision"), 2):
        errors += _validate_hashed_ref(parent_manifest, "parent_manifest")
        if isinstance(parent_manifest, dict) and (not _strict_int(parent_manifest.get("revision"), 1) or parent_manifest.get("revision") >= data.get("revision")):
            errors.append("parent_manifest.revision must be an earlier positive revision")
    if not isinstance(data.get("units"), str) or not data.get("units"):
        errors.append("units must be a non-empty string")
    errors += validate_coordinate_system(data.get("coordinate_system"))
    tolerance = data.get("comparison_tolerance")
    errors += check_tracked(tolerance, "$.comparison_tolerance") if isinstance(tolerance, dict) else ["comparison_tolerance must be a tracked object"]
    tolerance_value = tolerance.get("value") if isinstance(tolerance, dict) else None
    if not isinstance(tolerance_value, (int, float)) or isinstance(tolerance_value, bool) or not math.isfinite(tolerance_value) or tolerance_value < 0:
        errors.append("comparison_tolerance.value must be a finite non-negative number")
    if isinstance(tolerance, dict) and tolerance.get("status") not in {"confirmed", "assumed"}:
        errors.append("comparison_tolerance must be confirmed or explicitly accepted assumed data")
    if not _strict_int(data.get("requirements_revision"), 1):
        errors.append("requirements_revision must be an integer >= 1")
    baseline_ref = data.get("requirements_baseline")
    if not isinstance(baseline_ref, dict):
        errors.append("requirements_baseline must be an object")
    else:
        errors += [f"requirements_baseline.{key} is required" for key in ("path", "project_id", "revision", "state", "sha256") if key not in baseline_ref]
        if baseline_ref.get("project_id") != data.get("project_id"):
            errors.append("requirements_baseline.project_id must match manifest project_id")
        if not _strict_int(baseline_ref.get("revision"), 1):
            errors.append("requirements_baseline.revision must be an integer >= 1")
        if baseline_ref.get("revision") != data.get("requirements_revision"):
            errors.append("requirements_baseline.revision must equal requirements_revision")
        if baseline_ref.get("state") != "baseline_confirmed":
            errors.append("requirements_baseline.state must be baseline_confirmed")
        if not re.fullmatch(r"[0-9a-f]{64}", str(baseline_ref.get("sha256", ""))):
            errors.append("requirements_baseline.sha256 must be a lowercase SHA-256 digest")
    errors += duplicate_ids(data.get("objects"), "objects")
    object_ids = {item.get("object_id") for item in data.get("objects", []) if isinstance(item, dict) and isinstance(item.get("object_id"), str)}
    for index, item in enumerate(data.get("objects", [])):
        if not isinstance(item, dict):
            continue
        parent = item.get("parent_id")
        if parent is not None and (not isinstance(parent, str) or not parent):
            errors.append(f"objects[{index}].parent_id must be a non-empty string or null")
        elif parent == item.get("object_id"):
            errors.append(f"objects[{index}].parent_id cannot reference itself")
        elif parent is not None and parent not in object_ids:
            errors.append(f"objects[{index}].parent_id must reference another Manifest object_id")
        dimensions = item.get("critical_dimensions")
        if not isinstance(dimensions, dict) or not dimensions:
            errors.append(f"objects[{index}].critical_dimensions must be a non-empty object")
        else:
            errors += validate_typed_value(dimensions, f"objects[{index}].critical_dimensions")
    errors += walk_tracked(data.get("objects", []), "$.objects")
    errors += walk_tracked(data.get("constraints", []), "$.constraints")
    errors += walk_tracked(data.get("assumptions", []), "$.assumptions")
    for index, item in enumerate(data.get("assumptions", [])):
        if isinstance(item, dict) and item.get("status") == "assumed":
            acceptance = item.get("acceptance", {})
            actor = str(acceptance.get("accepted_by", "")).lower()
            if actor in {"", "orchestrator", "agent", "system"} or not acceptance.get("evidence"):
                errors.append(f"assumptions[{index}] requires human acceptance and evidence")
    if data.get("stage_status") not in {"requirements_analysis", "clarification_required", "requirements_ready"}:
        for field in ("objects", "constraints", "assumptions", "unresolved_items"):
            for hit in blocking_items(data.get(field, []), f"$.{field}"):
                errors.append(f"manifest has blocker at {hit}")
    if data.get("stage_status") not in {"manifest_ready", "planning"}:
        evidence = data.get("gate_evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append("post-manifest stage_status requires non-empty gate_evidence")
    return errors


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_manifest_against_baseline(manifest: dict[str, Any], baseline: dict[str, Any], baseline_path: str | Path) -> list[str]:
    errors = validate_manifest(manifest) + validate_baseline(baseline)
    ref = manifest.get("requirements_baseline", {})
    if baseline.get("project_id") != manifest.get("project_id"):
        errors.append("baseline project_id does not match manifest")
    if baseline.get("revision") != manifest.get("requirements_revision"):
        errors.append("baseline revision does not match requirements_revision")
    if ref.get("sha256") != file_sha256(baseline_path):
        errors.append("baseline SHA-256 does not match requirements_baseline.sha256")
    tolerance = manifest.get("comparison_tolerance", {})
    source = tolerance.get("source")
    source_requirement = next((item for item in baseline.get("requirements", []) if isinstance(item, dict) and item.get("requirement_id") == source), None)
    if source_requirement is None:
        errors.append("comparison_tolerance.source must reference a baseline requirement_id")
    elif source_requirement.get("value") != tolerance.get("value"):
        errors.append("comparison_tolerance.value does not match its baseline requirement")
    return errors


def validate_project_state(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "project_state")
    errors += missing(data, ["current_state", "active_revisions", "last_transition", "next_allowed_states", "blockers", "writer_locks"])
    current = data.get("current_state")
    if current not in STATES:
        errors.append("current_state is not allowed")
    active_revisions = data.get("active_revisions")
    if not isinstance(active_revisions, dict):
        errors.append("active_revisions must be an object")
    else:
        for key, value in active_revisions.items():
            if not isinstance(key, str) or not key or not _strict_int(value, 1):
                errors.append(f"active_revisions.{key} must be an integer >= 1")
    next_states = data.get("next_allowed_states")
    if not isinstance(next_states, list):
        errors.append("next_allowed_states must be an array")
    elif current == "ready_for_delivery" and set(next_states) == {"delivered"}:
        pass  # Compatibility for pre-3.0 delivery state journals.
    elif current == "delivered" and not next_states:
        pass  # Compatibility for pre-3.0 terminal delivery journals.
    elif current in TRANSITIONS and set(next_states) != TRANSITIONS[current]:
        errors.append(f"next_allowed_states must equal {sorted(TRANSITIONS[current])} for {current}")
    if current != "raw_request":
        transition = data.get("last_transition")
        if not isinstance(transition, dict):
            errors.append("non-initial state requires last_transition evidence")
        else:
            errors += [f"last_transition.{key} is required" for key in ("from", "to", "actor", "evidence", "revisions") if key not in transition]
            if transition.get("to") != current or not transition.get("evidence"):
                errors.append("last_transition must target current_state and contain evidence")
            previous = transition.get("from")
            if previous not in TRANSITIONS or current not in TRANSITIONS.get(previous, set()):
                errors.append("last_transition.from -> to is not an allowed state transition")
            if not isinstance(transition.get("revisions"), dict) or not transition.get("revisions"):
                errors.append("last_transition.revisions must be a non-empty object")
            else:
                for key, value in transition["revisions"].items():
                    if not isinstance(key, str) or not key or not _strict_int(value, 1):
                        errors.append(f"last_transition.revisions.{key} must be an integer >= 1")
    locks = data.get("writer_locks")
    if isinstance(locks, list):
        seen_paths: set[str] = set()
        for index, lock in enumerate(locks):
            if not isinstance(lock, dict):
                errors.append(f"writer_locks[{index}] must be an object")
                continue
            errors += [f"writer_locks[{index}].{key} is required" for key in ("path", "owner", "packet_id", "acquired_at", "status") if key not in lock]
            path = lock.get("path")
            errors += unsafe_relative_paths([path], f"writer_locks[{index}].path")
            normalized = str(path).replace("\\", "/").strip("/").lower()
            for existing in seen_paths:
                if normalized == existing or normalized.startswith(existing + "/") or existing.startswith(normalized + "/"):
                    errors.append(f"overlapping writer lock path: {path}")
            seen_paths.add(normalized)
            if lock.get("status") not in {"active", "released", "uncertain"}:
                errors.append(f"writer_locks[{index}].status is invalid")
    working_root = data.get("working_copy_root")
    if working_root is not None and working_root != "working":
        errors.append("working_copy_root must be 'working'")
    releases = data.get("published_releases")
    if releases is not None:
        if not isinstance(releases, list):
            errors.append("published_releases must be an array")
        else:
            seen_revisions: set[int] = set()
            seen_paths: set[str] = set()
            for index, release in enumerate(releases):
                location = f"published_releases[{index}]"
                if not isinstance(release, dict):
                    errors.append(f"{location} must be an object")
                    continue
                revision = release.get("revision")
                path = release.get("path")
                expected_path = f"delivery/accepted/r{revision}" if _strict_int(revision, 1) else None
                if expected_path is None or str(path).replace("\\", "/") != expected_path:
                    errors.append(f"{location}.path must be delivery/accepted/rN and match its positive revision")
                errors += unsafe_relative_paths([path], f"{location}.path")
                if revision in seen_revisions:
                    errors.append(f"duplicate published release revision: {revision}")
                if isinstance(path, str) and path.casefold() in seen_paths:
                    errors.append(f"duplicate published release path: {path}")
                if isinstance(revision, int) and not isinstance(revision, bool):
                    seen_revisions.add(revision)
                if isinstance(path, str):
                    seen_paths.add(path.casefold())
    return errors


def validate_stage_result(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "stage_result")
    errors += missing(data, ["stage", "packet_id", "manifest_revision_consumed", "status", "outputs", "readback_evidence", "unresolved_items", "manifest_patch_path"])
    if data.get("stage") not in {"cad", "blender"}:
        errors.append("stage must be 'cad' or 'blender'")
    if not _strict_int(data.get("manifest_revision_consumed"), 1):
        errors.append("manifest_revision_consumed must be an integer >= 1")
    if data.get("status") not in {"completed", "blocked", "failed"}:
        errors.append("status must be completed, blocked, or failed")
    if data.get("status") == "completed":
        if not isinstance(data.get("outputs"), list) or not data.get("outputs"):
            errors.append("completed result requires non-empty outputs")
        if not isinstance(data.get("readback_evidence"), list) or not data.get("readback_evidence"):
            errors.append("completed result requires non-empty readback_evidence")
        for hit in blocking_items(data.get("unresolved_items", []), "$.unresolved_items"):
            errors.append(f"completed result contains blocker at {hit}")
    errors += unsafe_relative_paths(data.get("outputs"), "outputs")
    errors += unsafe_relative_paths(data.get("readback_evidence"), "readback_evidence")
    if "work_packet" in data:
        errors += _validate_work_packet_ref(data.get("work_packet"))
    if "evidence_contract_version" in data:
        if data.get("evidence_contract_version") != "1.0":
            errors.append("evidence_contract_version must be '1.0'")
        for field in ("promoted_attempt", "completion_receipt"):
            ref = data.get(field)
            errors += _validate_hashed_ref(ref, field, require_revision=False)
            if not isinstance(ref, dict) or not ref.get("attempt_id"):
                errors.append(f"{field}.attempt_id is required")
        if isinstance(data.get("promoted_attempt"), dict) and isinstance(data.get("completion_receipt"), dict) and data["promoted_attempt"].get("attempt_id") != data["completion_receipt"].get("attempt_id"):
            errors.append("promoted_attempt and completion_receipt must identify the same attempt")
        errors += _validate_work_packet_ref(data.get("work_packet"))
    return errors


def legacy_warnings(data: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if data.get("artifact_type") == "stage_result" and "evidence_contract_version" not in data:
        warnings.append("legacy Stage Result: attempt and completion-receipt binding is not present")
    if data.get("artifact_type") == "delivery_report" and "hash_ledger" not in data:
        warnings.append("legacy Delivery Report: outer non-self-referential hash ledger is not present")
    if data.get("artifact_type") == "delivery_report" and data.get("delivery_contract_version") not in {"2.0", "3.0"}:
        warnings.append("legacy Delivery Report: Manifest ancestry is structural/unverified because historical Manifests lack parent_manifest authentication")
        warnings.append("legacy Delivery Report: geometry fidelity and render QA result contracts are not present")
    if data.get("artifact_type") == "delivery_report" and data.get("delivery_contract_version") == "2.0":
        warnings.append("legacy Delivery Report: artifacts are not isolated in immutable delivery/accepted/rN storage")
    return warnings


def validate_manifest_patch(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "manifest_patch")
    errors += missing(data, ["base_revision", "base_sha256", "proposed_revision", "operations", "evidence_paths", "created_by", "status"])
    base, proposed = data.get("base_revision"), data.get("proposed_revision")
    if not _strict_int(base, 1) or not _strict_int(proposed, 1) or proposed != base + 1:
        errors.append("proposed_revision must equal base_revision + 1")
    if data.get("status") not in {"proposed", "accepted", "rejected"}:
        errors.append("status must be proposed, accepted, or rejected")
    if not re.fullmatch(r"[0-9a-f]{64}", str(data.get("base_sha256", ""))):
        errors.append("base_sha256 must be a lowercase SHA-256 digest")
    operations = data.get("operations")
    if not isinstance(operations, list) or not operations:
        errors.append("operations must be a non-empty array")
    else:
        operation_ids: set[str] = set()
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict):
                errors.append(f"operations[{index}] must be an object")
                continue
            errors += [f"operations[{index}].{key} is required" for key in ("operation_id", "op", "path", "evidence") if key not in operation]
            if operation.get("op") not in {"add", "replace", "remove"}:
                errors.append(f"operations[{index}].op is invalid")
            if not str(operation.get("path", "")).startswith("/") or ".." in str(operation.get("path", "")).split("/"):
                errors.append(f"operations[{index}].path must be an absolute JSON Pointer without '..'")
            operation_id = operation.get("operation_id")
            if operation_id in operation_ids:
                errors.append(f"duplicate operation_id: {operation_id}")
            operation_ids.add(operation_id)
            if operation.get("op") != "remove" and "value" not in operation:
                errors.append(f"operations[{index}].value is required for add/replace")
            errors += unsafe_relative_paths([operation.get("evidence")], f"operations[{index}].evidence")
    if not data.get("evidence_paths"):
        errors.append("evidence_paths must not be empty")
    else:
        errors += unsafe_relative_paths(data.get("evidence_paths"), "evidence_paths")
        for index, operation in enumerate(operations or []):
            if isinstance(operation, dict) and operation.get("evidence") not in data.get("evidence_paths", []):
                errors.append(f"operations[{index}].evidence must be listed in evidence_paths")
    return errors


def _validate_hashed_ref(value: Any, location: str, require_revision: bool = True) -> list[str]:
    if not isinstance(value, dict):
        return [f"{location} must be an object"]
    fields = ("path", "revision", "sha256") if require_revision else ("path", "sha256")
    errors = [f"{location}.{key} is required" for key in fields if key not in value]
    errors += unsafe_relative_paths([value.get("path")], f"{location}.path")
    if require_revision and (not isinstance(value.get("revision"), int) or isinstance(value.get("revision"), bool) or value.get("revision", 0) < 1):
        errors.append(f"{location}.revision must be an integer >= 1")
    if not SHA256_RE.fullmatch(str(value.get("sha256", ""))):
        errors.append(f"{location}.sha256 must be a lowercase SHA-256 digest")
    return errors


def validate_process_evidence(value: Any, location: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{location} must be an object"]
    required = ("process_id", "host", "process_started_at", "executable", "command_sha256", "log")
    errors = [f"{location}.{key} is required" for key in required if key not in value]
    if not _strict_int(value.get("process_id"), 1):
        errors.append(f"{location}.process_id must be a positive integer")
    for field in ("host", "executable"):
        if not isinstance(value.get(field), str) or not value.get(field):
            errors.append(f"{location}.{field} must be a non-empty string")
    if not _valid_utc_timestamp(value.get("process_started_at")):
        errors.append(f"{location}.process_started_at must be a real ISO-8601 UTC timestamp")
    if not SHA256_RE.fullmatch(str(value.get("command_sha256", ""))):
        errors.append(f"{location}.command_sha256 must be a lowercase SHA-256 digest")
    errors += _validate_hashed_ref(value.get("log"), f"{location}.log", require_revision=False)
    return errors


def validate_process_log(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "process_log")
    errors += missing(data, ["stage", "packet_id", "attempt_id", "process_id", "host", "process_started_at", "executable", "process_kind", "command", "command_sha256", "recorded_at", "events"])
    if not _strict_int(data.get("process_id"), 1): errors.append("process_id must be a positive integer")
    for field in ("host", "executable"):
        if not isinstance(data.get(field), str) or not data.get(field): errors.append(f"{field} must be a non-empty string")
    if data.get("stage") not in {"cad", "blender"}:
        errors.append("stage must be cad or blender")
    for field in ("packet_id", "attempt_id"):
        if not isinstance(data.get(field), str) or not data.get(field): errors.append(f"{field} must be a non-empty string")
    for field in ("process_started_at", "recorded_at"):
        if not _valid_utc_timestamp(data.get(field)): errors.append(f"{field} must be a real ISO-8601 UTC timestamp")
    process_kind = data.get("process_kind")
    if process_kind not in {"writer", "readback"}:
        errors.append("process_kind must be writer or readback")
    command = data.get("command")
    if not isinstance(command, dict):
        errors.append("command must be an object")
    else:
        argv, cwd = command.get("argv"), command.get("cwd")
        if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item for item in argv):
            errors.append("command.argv must be a non-empty string array")
        if not isinstance(cwd, str) or not cwd:
            errors.append("command.cwd must be a non-empty string")
        if isinstance(argv, list) and argv and isinstance(data.get("executable"), str) and argv[0] != data.get("executable"):
            errors.append("command.argv[0] must exactly equal executable")
        try:
            command_digest = hashlib.sha256(json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            errors.append("command must be canonically JSON serializable")
        else:
            if data.get("command_sha256") != command_digest:
                errors.append("command_sha256 must equal the canonical command object SHA-256")
    if not SHA256_RE.fullmatch(str(data.get("command_sha256", ""))): errors.append("command_sha256 must be a lowercase SHA-256 digest")
    events = data.get("events")
    required_sequences = {
        "writer": ["process_started", "output_written", "process_exited"],
        "readback": ["process_started", "artifact_verified", "process_exited"],
    }
    if not isinstance(events, list) or not events:
        errors.append("events must be a non-empty array")
    else:
        event_names: list[str] = []
        event_times: list[datetime] = []
        for index, event in enumerate(events):
            if not isinstance(event, dict) or set(event) != {"at", "event", "details"}:
                errors.append(f"events[{index}] must contain exactly at, event, and details")
                continue
            at = event.get("at")
            if not _valid_utc_timestamp(at):
                errors.append(f"events[{index}].at must be a real ISO-8601 UTC timestamp")
            else:
                event_times.append(datetime.fromisoformat(str(at).replace("Z", "+00:00")))
            if not isinstance(event.get("event"), str):
                errors.append(f"events[{index}].event must be a string")
            else:
                event_names.append(event["event"])
            details = event.get("details")
            if not isinstance(details, dict):
                errors.append(f"events[{index}].details must be an object")
            elif event.get("event") == "process_started" and details != {"process_id": data.get("process_id")}:
                errors.append(f"events[{index}] process_started details must bind process_id")
            elif event.get("event") in {"output_written", "artifact_verified"}:
                artifacts = details.get("artifacts")
                if set(details) != {"artifacts"} or not isinstance(artifacts, list) or not artifacts:
                    errors.append(f"events[{index}] artifact event must contain a non-empty artifacts array")
                else:
                    paths: list[Any] = []
                    for artifact_index, artifact in enumerate(artifacts):
                        errors += _validate_hashed_ref(artifact, f"events[{index}].details.artifacts[{artifact_index}]", require_revision=False)
                        if isinstance(artifact, dict): paths.append(artifact.get("path"))
                    if len(set(paths)) != len(paths): errors.append(f"events[{index}] artifact paths must be unique")
            elif event.get("event") == "process_exited" and (set(details) != {"exit_code"} or not isinstance(details.get("exit_code"), int) or isinstance(details.get("exit_code"), bool)):
                errors.append(f"events[{index}] process_exited details must contain an integer exit_code")
        expected = required_sequences.get(process_kind)
        if expected is not None and event_names != expected:
            errors.append(f"events must exactly follow the {process_kind} sequence: {expected}")
        started = datetime.fromisoformat(data["process_started_at"].replace("Z", "+00:00")) if _valid_utc_timestamp(data.get("process_started_at")) else None
        recorded = datetime.fromisoformat(data["recorded_at"].replace("Z", "+00:00")) if _valid_utc_timestamp(data.get("recorded_at")) else None
        if event_times and (event_times != sorted(event_times) or (started is not None and event_times[0] < started) or (recorded is not None and event_times[-1] > recorded)):
            errors.append("event timestamps must be monotonic and bounded by process_started_at/recorded_at")
    return errors


def validate_review_work_packet(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "review_work_packet")
    required = ["packet_id", "review_stage", "reviewer_identity", "reviewer_independent", "reviewer_read_only", "input_paths", "input_hashes", "manifest_lineage", "consumed", "review_profiles", "output_paths", "writer_scope", "prohibited_changes", "acceptance_criteria", "stop_conditions"]
    errors += missing(data, required)
    stage = data.get("review_stage")
    expected_keys = {
        "cad": {"manifest", "stage_result"},
        "blender": {"manifest", "stage_result", "upstream_review"},
        "cross_software": {"manifest", "cad_result", "blender_result", "cad_review", "blender_review"},
    }
    required_profiles = {
        "cad": {"common_acceptance", "cad_drawing_review"},
        "blender": {"common_acceptance", "blender_model_review"},
        "cross_software": {"common_acceptance", "cross_software_consistency"},
    }
    if stage not in expected_keys:
        errors.append("review_stage must be cad, blender, or cross_software")
    if data.get("reviewer_independent") is not True or data.get("reviewer_read_only") is not True or not data.get("reviewer_identity"):
        errors.append("reviewer must be named, independent, and read-only")
    errors += validate_writer_boundary(data, stage if isinstance(stage, str) else "review", review=True)
    inputs, outputs, scope = data.get("input_paths"), data.get("output_paths"), data.get("writer_scope")
    for field, value in (("input_paths", inputs), ("output_paths", outputs), ("writer_scope", scope)):
        errors += unsafe_relative_paths(value, field)
        if not isinstance(value, list) or not value:
            errors.append(f"{field} must be a non-empty array")
    if isinstance(outputs, list) and isinstance(scope, list) and (outputs != scope or len(relative_path_set(scope)) != len(scope)):
        errors.append("writer_scope must exactly equal the explicit output_paths; globs are not supported")
    if isinstance(inputs, list) and isinstance(scope, list) and relative_path_set(inputs) & relative_path_set(scope):
        errors.append("review writer_scope must not overlap read-only inputs")
    if isinstance(scope, list) and any(not path.replace("\\", "/").startswith("reviews/") for path in scope if isinstance(path, str)):
        errors.append("review writer_scope may contain only explicit paths under reviews/")
    hashes = data.get("input_hashes")
    if not isinstance(hashes, dict):
        errors.append("input_hashes must be an object")
    elif isinstance(inputs, list):
        if set(hashes) != relative_path_set(inputs):
            errors.append("input_hashes must cover exactly input_paths")
        for path, digest in hashes.items():
            if not SHA256_RE.fullmatch(str(digest)):
                errors.append(f"input_hashes has invalid SHA-256 for {path}")
    consumed = data.get("consumed")
    if not isinstance(consumed, dict) or stage not in expected_keys or set(consumed) != expected_keys.get(stage, set()):
        errors.append(f"consumed must contain exactly {sorted(expected_keys.get(stage, set()))}")
    else:
        for name, ref in consumed.items():
            errors += _validate_hashed_ref(ref, f"consumed.{name}")
            ref_path = ref.get("path") if isinstance(ref, dict) else None
            if isinstance(inputs, list) and ref_path not in inputs:
                errors.append(f"consumed.{name}.path must be listed in input_paths")
            if isinstance(hashes, dict) and isinstance(ref_path, str) and hashes.get(ref_path) != ref.get("sha256"):
                errors.append(f"consumed.{name}.sha256 must equal input_hashes for its path")
    manifest_revision = consumed.get("manifest", {}).get("revision") if isinstance(consumed, dict) and isinstance(consumed.get("manifest"), dict) else None
    lineage_errors, _ = validate_manifest_lineage(data.get("manifest_lineage"), manifest_revision)
    errors += lineage_errors
    lineage = data.get("manifest_lineage")
    for index, entry in enumerate(lineage if isinstance(lineage, list) else []):
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if isinstance(inputs, list) and path not in inputs:
            errors.append(f"manifest_lineage[{index}].path must be listed in input_paths")
        if isinstance(hashes, dict) and isinstance(path, str) and hashes.get(path) != entry.get("sha256"):
            errors.append(f"manifest_lineage[{index}].sha256 must equal input_hashes for its path")
    manifest_ref = consumed.get("manifest") if isinstance(consumed, dict) and isinstance(consumed.get("manifest"), dict) else {}
    current_lineage = next((entry for entry in (lineage if isinstance(lineage, list) else []) if isinstance(entry, dict) and entry.get("revision") == manifest_revision), None)
    if isinstance(current_lineage, dict) and (manifest_ref.get("path") != current_lineage.get("path") or manifest_ref.get("sha256") != current_lineage.get("sha256")):
        errors.append("consumed.manifest must identify the same path and SHA-256 as its manifest_lineage entry")
    profiles = data.get("review_profiles")
    profile_names = {item for item in profiles if isinstance(item, str)} if isinstance(profiles, list) else set()
    if not isinstance(profiles, list) or len(profile_names) != len(profiles) or stage not in required_profiles or not required_profiles.get(stage, set()).issubset(profile_names):
        errors.append(f"review_profiles do not satisfy the {stage} review stage")
    if not data.get("prohibited_changes"):
        errors.append("prohibited_changes must explicitly forbid engineering artifact modification")
    if stage == "blender":
        errors += validate_geometry_fidelity(data.get("geometry_fidelity"), "geometry_fidelity")
    return errors


def validate_attempt_record(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "attempt_record")
    errors += missing(data, ["stage", "packet_id", "attempt_id", "attempt_directory", "interface", "writer_process", "started_at", "ended_at", "exit_code", "logs", "expected_outputs", "actual_outputs", "failure_classification", "promoted_to_stage_result"])
    errors += _validate_work_packet_ref(data.get("work_packet"))
    if data.get("stage") not in {"cad", "blender"}:
        errors.append("stage must be cad or blender")
    errors += unsafe_relative_paths([data.get("attempt_directory")], "attempt_directory")
    expected_prefix = f"{data.get('stage')}/attempts/{data.get('packet_id')}/{data.get('attempt_id')}"
    if str(data.get("attempt_directory", "")).replace("\\", "/") != expected_prefix:
        errors.append("attempt_directory must be <stage>/attempts/<packet-id>/<attempt-id>")
    for field in ("started_at", "ended_at"):
        if not _valid_utc_timestamp(data.get(field)):
            errors.append(f"{field} must be an ISO-8601 UTC timestamp")
    interface = data.get("interface")
    if not isinstance(interface, dict) or any(not interface.get(key) for key in ("application", "interface_type", "command_summary")):
        errors.append("interface must identify application, interface_type, and command_summary")
    errors += validate_process_evidence(data.get("writer_process"), "writer_process")
    if data.get("exit_code") is not None and not _strict_int(data.get("exit_code"), 0):
        errors.append("exit_code must be an integer or null")
    logs = data.get("logs")
    if not isinstance(logs, dict) or not logs.get("summary"):
        errors.append("logs must include a summary")
    else:
        for key in ("stdout_path", "stderr_path"):
            if logs.get(key) is not None:
                errors += unsafe_relative_paths([logs.get(key)], f"logs.{key}")
                normalized_log = str(logs.get(key)).replace("\\", "/")
                if not normalized_log.startswith(expected_prefix + "/"):
                    errors.append(f"logs.{key} must stay inside attempt_directory")
    expected_outputs = data.get("expected_outputs")
    errors += unsafe_relative_paths(expected_outputs if isinstance(expected_outputs, list) else [], "expected_outputs")
    if not isinstance(expected_outputs, list) or not expected_outputs or any(not isinstance(item, str) for item in expected_outputs) or len(set(expected_outputs)) != len(expected_outputs):
        errors.append("expected_outputs must be a non-empty unique path array")
    actual = data.get("actual_outputs")
    if not isinstance(actual, list):
        errors.append("actual_outputs must be an array")
    else:
        actual_paths: list[Any] = []
        for index, ref in enumerate(actual):
            errors += _validate_hashed_ref(ref, f"actual_outputs[{index}]", require_revision=False)
            if isinstance(ref, dict):
                actual_paths.append(ref.get("path"))
        if len(set(actual_paths)) != len(actual_paths):
            errors.append("actual_outputs paths must be unique")
    failure = data.get("failure_classification")
    if failure not in {None, "input", "capability", "execution", "artifact_missing", "hash_mismatch", "readback_failed", "validation_failed", "cancelled"}:
        errors.append("failure_classification is invalid")
    if data.get("promoted_to_stage_result") is True:
        if data.get("exit_code") != 0 or failure is not None:
            errors.append("a promoted attempt must have exit_code 0 and no failure classification")
        actual_paths = {item.get("path") for item in actual or [] if isinstance(item, dict)}
        if not set(expected_outputs if isinstance(expected_outputs, list) else []).issubset(actual_paths):
            errors.append("a promoted attempt must hash every expected output")
    return errors


def validate_completion_receipt(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "completion_receipt")
    errors += missing(data, ["stage", "packet_id", "attempt_id", "application", "interface_type", "status", "process_identity_assurance", "writer_process", "readback_process", "independent_readback", "outputs", "completed_at"])
    errors += _validate_work_packet_ref(data.get("work_packet"))
    if data.get("stage") not in {"cad", "blender"}:
        errors.append("stage must be cad or blender")
    if data.get("status") != "completed":
        errors.append("completion receipt status must be completed")
    if data.get("process_identity_assurance") not in {"verified_by_log_hashes", "declared"}:
        errors.append("process_identity_assurance must be verified_by_log_hashes or declared")
    writer, reader = data.get("writer_process"), data.get("readback_process")
    errors += validate_process_evidence(writer, "writer_process")
    errors += validate_process_evidence(reader, "readback_process")
    if isinstance(writer, dict) and isinstance(reader, dict):
        writer_identity = (str(writer.get("host", "")).casefold(), writer.get("process_id"), writer.get("process_started_at"))
        reader_identity = (str(reader.get("host", "")).casefold(), reader.get("process_id"), reader.get("process_started_at"))
        if writer_identity == reader_identity:
            errors.append("read-back must be performed by an independent process identity")
        if writer.get("process_id") == reader.get("process_id"):
            errors.append("writer_process and readback_process must use distinct positive process IDs")
        if writer.get("process_started_at") == reader.get("process_started_at"):
            errors.append("writer_process and readback_process must have distinct process start times")
        writer_log = writer.get("log") if isinstance(writer.get("log"), dict) else {}
        reader_log = reader.get("log") if isinstance(reader.get("log"), dict) else {}
        writer_log_path = canonical_project_path(writer_log.get("path"))
        reader_log_path = canonical_project_path(reader_log.get("path"))
        if writer_log_path is not None and writer_log_path == reader_log_path:
            errors.append("writer_process and readback_process must use distinct independent log paths")
        if writer_log.get("sha256") == reader_log.get("sha256"):
            errors.append("writer_process and readback_process must use distinct independent log hashes")
    if data.get("independent_readback") is not True:
        errors.append("independent_readback must be true")
    if not _valid_utc_timestamp(data.get("completed_at")):
        errors.append("completed_at must be an ISO-8601 UTC timestamp")
    outputs = data.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        errors.append("outputs must be a non-empty array")
    else:
        output_paths: list[Any] = []
        for index, ref in enumerate(outputs):
            errors += _validate_hashed_ref(ref, f"outputs[{index}]", require_revision=False)
            if isinstance(ref, dict):
                output_paths.append(ref.get("path"))
        if len(set(output_paths)) != len(output_paths):
            errors.append("completion receipt output paths must be unique")
    return errors


def validate_render_qa_profile(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "render_qa_profile")
    errors += missing(data, ["minimum_resolution", "subject_min_frame_fraction", "label_readability", "image_quality", "required_views", "color_semantics", "separate_outputs", "deterministic_checks", "manual_visual_checks"])
    resolution = data.get("minimum_resolution")
    if not isinstance(resolution, dict) or any(not isinstance(resolution.get(key), int) or isinstance(resolution.get(key), bool) or resolution.get(key, 0) < 1 for key in ("width_px", "height_px")):
        errors.append("minimum_resolution must contain positive integer width_px and height_px")
    fraction = data.get("subject_min_frame_fraction")
    if not _finite_number(fraction) or not 0 < fraction <= 1:
        errors.append("subject_min_frame_fraction must be a finite number in (0, 1]")
    label = data.get("label_readability")
    pixel_height = label.get("minimum_pixel_height") if isinstance(label, dict) else None
    if not isinstance(label, dict) or ((not isinstance(pixel_height, int) or isinstance(pixel_height, bool) or pixel_height < 1) and label.get("manual_gate_required") is not True):
        errors.append("label_readability requires minimum_pixel_height or a manual gate")
    quality = data.get("image_quality")
    if not isinstance(quality, dict) or any(quality.get(key) is not True for key in ("check_exposure", "check_brightness", "check_contrast")):
        errors.append("image_quality must enable exposure, brightness, and contrast checks")
    views = data.get("required_views")
    if not isinstance(views, dict) or views.get("clean_overview") is not True or not isinstance(views.get("clean_overview_excludes"), list) or not views.get("clean_overview_excludes") or views.get("plan_view_projection") != "orthographic":
        errors.append("required_views must require a clean overview and orthographic plan view")
    colors = data.get("color_semantics")
    if not isinstance(colors, dict) or colors.get("legend_or_labels_required") is not True or colors.get("color_only_prohibited") is not True:
        errors.append("color semantics must require legends/labels and prohibit color-only meaning")
    separate = data.get("separate_outputs")
    if not isinstance(separate, dict) or separate.get("clearance_inspection") is not True or separate.get("presentation_render") is not True:
        errors.append("clearance inspection and presentation render must be separate outputs")
    for field in ("deterministic_checks", "manual_visual_checks"):
        if not isinstance(data.get(field), list) or not data.get(field):
            errors.append(f"{field} must be a non-empty array")
    return errors


def _single_mesh_metrics(data: dict[str, Any]) -> tuple[list[str], dict[str, Any] | None]:
    """Validate a portable canonical mesh and deterministically recompute its topology digest."""
    errors = check_identity(data, "canonical_mesh")
    errors += missing(data, ["source", "units", "object_ids", "vertices", "faces"])
    errors += _validate_hashed_ref(data.get("source"), "source", require_revision=False)
    if data.get("units") not in {"mm", "cm", "m", "in"}:
        errors.append("units must be one of mm, cm, m, or in")
    object_ids = data.get("object_ids")
    if not isinstance(object_ids, list) or not object_ids or any(not isinstance(item, str) or not item for item in object_ids) or len(set(object_ids or [])) != len(object_ids or []):
        errors.append("object_ids must be a non-empty unique string array")
    vertices = data.get("vertices")
    parsed_vertices: list[tuple[float, float, float]] = []
    if not isinstance(vertices, list) or not vertices:
        errors.append("vertices must be a non-empty array")
    else:
        for index, vertex in enumerate(vertices):
            if not isinstance(vertex, list) or len(vertex) != 3 or any(not _finite_number(value) for value in vertex):
                errors.append(f"vertices[{index}] must contain three finite numbers")
            else:
                parsed_vertices.append(tuple(float(value) for value in vertex))
        if len(set(parsed_vertices)) != len(parsed_vertices):
            errors.append("vertices must be unique before canonicalization")
    faces = data.get("faces")
    parsed_faces: list[tuple[int, ...]] = []
    if not isinstance(faces, list) or not faces:
        errors.append("faces must be a non-empty array")
    else:
        for index, face in enumerate(faces):
            if not isinstance(face, list) or len(face) < 3 or any(not _strict_int(value, 0) for value in face):
                errors.append(f"faces[{index}] must contain at least three non-negative integer indices")
                continue
            if len(set(face)) != len(face):
                errors.append(f"faces[{index}] must not repeat a vertex")
            if isinstance(vertices, list) and any(value >= len(vertices) for value in face):
                errors.append(f"faces[{index}] references an out-of-range vertex")
            else:
                parsed_faces.append(tuple(face))
    if errors:
        return errors, None
    vertex_order = sorted(range(len(parsed_vertices)), key=lambda index: parsed_vertices[index])
    old_to_new = {old: new for new, old in enumerate(vertex_order)}
    canonical_vertices = [parsed_vertices[index] for index in vertex_order]

    def normalize_face(face: tuple[int, ...]) -> tuple[int, ...]:
        mapped = tuple(old_to_new[index] for index in face)
        candidates = [mapped[offset:] + mapped[:offset] for offset in range(len(mapped))]
        reversed_face = tuple(reversed(mapped))
        candidates += [reversed_face[offset:] + reversed_face[:offset] for offset in range(len(mapped))]
        return min(candidates)

    canonical_faces = sorted(normalize_face(face) for face in parsed_faces)
    if len(set(canonical_faces)) != len(canonical_faces):
        return ["faces must be unique after canonicalization"], None
    edges: set[tuple[int, int]] = set()
    adjacency: dict[int, set[int]] = {index: set() for index in range(len(canonical_vertices))}
    for face in canonical_faces:
        for index, start in enumerate(face):
            end = face[(index + 1) % len(face)]
            edge = (min(start, end), max(start, end))
            edges.add(edge)
            adjacency[start].add(end)
            adjacency[end].add(start)
    unseen = set(adjacency)
    components = 0
    while unseen:
        components += 1
        stack = [unseen.pop()]
        while stack:
            neighbors = adjacency[stack.pop()] & unseen
            unseen.difference_update(neighbors)
            stack.extend(neighbors)
    payload = {
        "units": data["units"],
        "object_ids": sorted(object_ids),
        "vertices": [list(value) for value in canonical_vertices],
        "faces": [list(value) for value in canonical_faces],
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return [], {
        "vertex_count": len(canonical_vertices),
        "edge_count": len(edges),
        "face_count": len(canonical_faces),
        "component_count": components,
        "geometry_sha256": digest,
    }


def canonical_mesh_metrics(data: dict[str, Any]) -> tuple[list[str], dict[str, Any] | None]:
    """Validate and recompute a canonical mesh partitioned by controlled object ID."""
    errors = check_identity(data, "canonical_mesh")
    errors += missing(data, ["source", "units", "objects"])
    errors += _validate_hashed_ref(data.get("source"), "source", require_revision=False)
    if data.get("units") not in {"mm", "cm", "m", "in"}:
        errors.append("units must be one of mm, cm, m, or in")
    objects = data.get("objects")
    if not isinstance(objects, list) or not objects:
        return errors + ["objects must be a non-empty array of per-object meshes"], None
    object_metrics: dict[str, dict[str, Any]] = {}
    for index, obj in enumerate(objects):
        if not isinstance(obj, dict) or not isinstance(obj.get("object_id"), str) or not obj.get("object_id"):
            errors.append(f"objects[{index}].object_id must be a non-empty string")
            continue
        object_id = obj["object_id"]
        if object_id in object_metrics:
            errors.append(f"duplicate canonical mesh object_id: {object_id}")
            continue
        synthetic = {
            "schema_version": "1.0", "artifact_type": "canonical_mesh", "project_id": data.get("project_id"), "revision": data.get("revision"),
            "source": data.get("source"), "units": data.get("units"), "object_ids": [object_id],
            "vertices": obj.get("vertices"), "faces": obj.get("faces"),
        }
        partition_errors, metrics = _single_mesh_metrics(synthetic)
        errors += [f"objects[{index}]: {item}" for item in partition_errors]
        if metrics is not None:
            object_metrics[object_id] = metrics
    if errors or len(object_metrics) != len(objects):
        return errors, None
    totals = {
        "vertex_count": sum(item["vertex_count"] for item in object_metrics.values()),
        "edge_count": sum(item["edge_count"] for item in object_metrics.values()),
        "face_count": sum(item["face_count"] for item in object_metrics.values()),
        "component_count": sum(item["component_count"] for item in object_metrics.values()),
        "objects": {key: object_metrics[key] for key in sorted(object_metrics)},
    }
    totals["geometry_sha256"] = hashlib.sha256(json.dumps(totals["objects"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return [], totals


def validate_geometry_fidelity_result(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "geometry_fidelity_result")
    errors += missing(data, ["manifest_revision", "declared", "topology_evidence", "measurement_evidence", "controlled_object_coverage", "measurement_results", "outcome"])
    if not _strict_int(data.get("manifest_revision"), 1):
        errors.append("manifest_revision must be an integer >= 1")
    declared = data.get("declared")
    errors += validate_geometry_fidelity(declared, "declared")
    topology = data.get("topology_evidence")
    if not isinstance(topology, dict):
        errors.append("topology_evidence must be an object")
    else:
        errors += _validate_hashed_ref(topology.get("cad_mesh"), "topology_evidence.cad_mesh", require_revision=False)
        errors += _validate_hashed_ref(topology.get("blender_mesh"), "topology_evidence.blender_mesh", require_revision=False)
        for side in ("cad_metrics", "blender_metrics"):
            metrics = topology.get(side)
            object_metrics = metrics.get("objects") if isinstance(metrics, dict) else None
            object_metrics_valid = isinstance(object_metrics, dict) and bool(object_metrics) and all(
                isinstance(item, dict)
                and all(_strict_int(item.get(key), 0) for key in ("vertex_count", "edge_count", "face_count", "component_count"))
                and SHA256_RE.fullmatch(str(item.get("geometry_sha256", "")))
                for item in object_metrics.values()
            )
            if not isinstance(metrics, dict) or any(not _strict_int(metrics.get(key), 0) for key in ("vertex_count", "edge_count", "face_count", "component_count")) or not SHA256_RE.fullmatch(str(metrics.get("geometry_sha256", ""))) or not object_metrics_valid:
                errors.append(f"topology_evidence.{side} must contain global and per-object non-negative counts and geometry_sha256")
        cad_metrics, blender_metrics = topology.get("cad_metrics"), topology.get("blender_metrics")
        computed_match = isinstance(cad_metrics, dict) and isinstance(blender_metrics, dict) and cad_metrics == blender_metrics
        if topology.get("topology_match") is not computed_match or not computed_match:
            errors.append("topology_evidence.topology_match must equal recomputed structured topology metrics")
    measurement_evidence = data.get("measurement_evidence")
    if not isinstance(measurement_evidence, dict):
        errors.append("measurement_evidence must be an object")
    else:
        errors += _validate_hashed_ref(measurement_evidence.get("cad_snapshot"), "measurement_evidence.cad_snapshot", require_revision=False)
        errors += _validate_hashed_ref(measurement_evidence.get("blender_snapshot"), "measurement_evidence.blender_snapshot", require_revision=False)
    coverage = data.get("controlled_object_coverage")
    if not isinstance(coverage, dict):
        errors.append("controlled_object_coverage must be an object")
    else:
        expected, verified = coverage.get("expected_object_ids"), coverage.get("verified_object_ids")
        if not isinstance(expected, list) or not expected or any(not isinstance(item, str) or not item for item in expected):
            errors.append("controlled_object_coverage.expected_object_ids must be a non-empty string array")
        if not isinstance(verified, list) or set(verified) != set(expected or []):
            errors.append("controlled_object_coverage must verify every expected controlled object")
        if coverage.get("coverage_fraction") != 1.0:
            errors.append("controlled_object_coverage.coverage_fraction must equal 1.0")
    measurements = data.get("measurement_results")
    permitted = declared.get("permitted_deviation_mm") if isinstance(declared, dict) else None
    if not isinstance(measurements, list) or not measurements:
        errors.append("measurement_results must be a non-empty array")
    else:
        for index, result in enumerate(measurements):
            if not isinstance(result, dict):
                errors.append(f"measurement_results[{index}] must be an object")
                continue
            for field in ("object_id", "property", "cad_value", "blender_value", "deviation_mm", "passed"):
                if field not in result:
                    errors.append(f"measurement_results[{index}].{field} is required")
            deviation = result.get("deviation_mm")
            computed = _controlled_deviation(result.get("cad_value"), result.get("blender_value"))
            if computed is None:
                errors.append(f"measurement_results[{index}] values must be comparable controlled scalars/vectors")
            elif not _finite_number(deviation) or not math.isclose(float(deviation), computed, rel_tol=0.0, abs_tol=1e-9):
                errors.append(f"measurement_results[{index}].deviation_mm does not equal recomputed deviation {computed}")
            if not _finite_number(deviation) or deviation < 0:
                errors.append(f"measurement_results[{index}].deviation_mm must be finite and non-negative")
            elif _finite_number(permitted) and computed is not None and computed > permitted:
                errors.append(f"measurement_results[{index}] exceeds declared permitted deviation")
            expected_pass = computed is not None and _finite_number(permitted) and computed <= permitted
            if result.get("passed") is not expected_pass or not expected_pass:
                errors.append(f"measurement_results[{index}].passed disagrees with recomputed deviation/tolerance")
    if data.get("outcome") not in {"PASS", "FAIL"}:
        errors.append("outcome must be PASS or FAIL")
    if isinstance(declared, dict) and declared.get("level") == "exact_mesh" and data.get("outcome") != "PASS":
        errors.append("exact_mesh fidelity result must PASS before acceptance")
    return errors


def validate_render_qa_result(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "render_qa_result")
    errors += missing(data, ["profile", "images", "deterministic_checks", "manual_checks", "outcome"])
    errors += _validate_hashed_ref(data.get("profile"), "profile")
    images = data.get("images")
    if not isinstance(images, list) or not images:
        errors.append("images must be a non-empty array")
    else:
        for index, item in enumerate(images):
            errors += _validate_hashed_ref(item, f"images[{index}]", require_revision=False)
            if isinstance(item, dict):
                if not _strict_int(item.get("width_px"), 1) or not _strict_int(item.get("height_px"), 1):
                    errors.append(f"images[{index}] must record positive integer width_px and height_px")
                if not isinstance(item.get("camera"), str) or not item.get("camera"):
                    errors.append(f"images[{index}].camera must identify the actual camera")
                if item.get("camera_check_passed") is not True or item.get("resolution_check_passed") is not True:
                    errors.append(f"images[{index}] camera and resolution checks must pass")
                errors += _validate_hashed_ref(item.get("camera_metadata"), f"images[{index}].camera_metadata", require_revision=False)
    checks = data.get("deterministic_checks")
    allowed_deterministic = {"resolution", "subject_frame_fraction", "plan_projection", "clean_overview_exclusions", "output_separation", "camera_metadata"}
    if not isinstance(checks, list) or not checks or any(not isinstance(item, dict) or item.get("passed") is not True or item.get("check_id") not in allowed_deterministic for item in checks):
        errors.append("deterministic_checks must be non-empty passed structured checks")
    manual = data.get("manual_checks")
    allowed_manual = {"label_readability", "exposure", "brightness", "contrast", "legend_or_labels", "clean_overview"}
    if not isinstance(manual, list) or not manual:
        errors.append("manual_checks must be non-empty")
    else:
        for index, item in enumerate(manual):
            if not isinstance(item, dict) or item.get("check_id") not in allowed_manual or item.get("outcome") != "PASS" or not item.get("reviewer"):
                errors.append(f"manual_checks[{index}] must be an allowed PASS check with reviewer")
                continue
            refs = item.get("evidence")
            if not isinstance(refs, list) or not refs:
                errors.append(f"manual_checks[{index}].evidence must contain hash-bound references")
            else:
                for ref_index, ref in enumerate(refs):
                    errors += _validate_hashed_ref(ref, f"manual_checks[{index}].evidence[{ref_index}]", require_revision=False)
    if data.get("outcome") != "PASS":
        errors.append("render QA result outcome must be PASS")
    return errors


def validate_hash_ledger(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "hash_ledger")
    errors += missing(data, ["self_path", "coverage", "covered_report", "entries", "excluded_paths"])
    errors += unsafe_relative_paths([data.get("self_path")], "self_path")
    if data.get("coverage") != "delivery_report_and_declared_artifacts":
        errors.append("coverage must be delivery_report_and_declared_artifacts")
    errors += _validate_hashed_ref(data.get("covered_report"), "covered_report", require_revision=False)
    entries = data.get("entries")
    if not isinstance(entries, dict) or not entries:
        errors.append("entries must be a non-empty path-to-SHA-256 object")
    else:
        errors += unsafe_relative_paths(list(entries), "entries")
        for path, digest in entries.items():
            if not SHA256_RE.fullmatch(str(digest)):
                errors.append(f"entries has invalid SHA-256 for {path}")
        if data.get("self_path") in entries:
            errors.append("hash ledger must never contain its own path")
        report = data.get("covered_report", {})
        if entries.get(report.get("path")) != report.get("sha256"):
            errors.append("covered_report digest must equal its entry")
    excluded = data.get("excluded_paths")
    if not isinstance(excluded, list):
        errors.append("excluded_paths must be an array")
    else:
        excluded_map = {item.get("path"): item.get("reason") for item in excluded if isinstance(item, dict)}
        if not excluded_map.get(data.get("self_path")):
            errors.append("excluded_paths must state why the ledger excludes itself")
        for index, item in enumerate(excluded):
            if not isinstance(item, dict) or not item.get("reason"):
                errors.append(f"excluded_paths[{index}] must contain path and reason")
            else:
                errors += unsafe_relative_paths([item.get("path")], f"excluded_paths[{index}].path")
        if isinstance(entries, dict) and set(entries) & set(excluded_map):
            errors.append("a path cannot be both hashed and excluded")
    return errors


def validate_delivery_checklist(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "delivery_checklist")
    items = data.get("items")
    if not isinstance(items, list) or not items or any(not isinstance(item, str) or not item for item in items):
        errors.append("items must be a non-empty string array")
    return errors


def validate_delivery_report(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "delivery_report")
    errors += missing(data, ["accepted_revisions", "manifest_lineage", "stage_results", "project_state_path", "deliverables", "readback_evidence", "review_outcomes", "artifact_hashes", "residual_assumptions", "limitations", "opening_instructions", "requires_qualified_review"])
    if not isinstance(data.get("requires_qualified_review"), bool):
        errors.append("requires_qualified_review must be boolean")
    if "delivery_contract_version" in data and data.get("delivery_contract_version") not in {"2.0", "3.0"}:
        errors.append("delivery_contract_version must be '2.0' or '3.0'")
    if data.get("delivery_contract_version") == "3.0":
        release = data.get("accepted_release")
        if not isinstance(release, dict):
            errors.append("delivery contract 3.0 requires accepted_release")
        else:
            release_revision = release.get("revision")
            expected_path = f"delivery/accepted/r{release_revision}" if _strict_int(release_revision, 1) else None
            if expected_path is None or str(release.get("path", "")).replace("\\", "/") != expected_path:
                errors.append("accepted_release.path must be delivery/accepted/rN and match its positive revision")
            if release.get("immutable") is not True:
                errors.append("accepted_release.immutable must be true")
            errors += unsafe_relative_paths([release.get("path")], "accepted_release.path")
        policy = data.get("working_copy_policy")
        if not isinstance(policy, dict) or policy.get("path") != "working" or policy.get("excluded_from_delivery_hashes") is not True:
            errors.append("delivery contract 3.0 requires working_copy_policy.path='working' excluded from delivery hashes")
        package = data.get("package")
        expected_package = f"delivery/package-r{release.get('revision')}.zip" if isinstance(release, dict) else None
        if not isinstance(package, dict) or package.get("path") != expected_package or package.get("release_revision") != release.get("revision"):
            errors.append("delivery contract 3.0 package must be delivery/package-rN.zip for the accepted release")
        elif package.get("path") in (data.get("artifact_hashes") if isinstance(data.get("artifact_hashes"), dict) else {}):
            errors.append("package ZIP must not be included in the Delivery Report artifact hashes")
        hashes_value = data.get("artifact_hashes")
        if isinstance(hashes_value, dict) and any(str(path).replace("\\", "/").casefold().startswith(("working/", "delivery/accepted/")) for path in hashes_value):
            errors.append("artifact_hashes paths must be release-relative and must not include working/ or nested accepted/ paths")
        ledger = data.get("hash_ledger")
        expected_ledger = f"delivery/final-hash-ledger-r{release.get('revision')}.json" if isinstance(release, dict) else None
        if not isinstance(ledger, dict) or ledger.get("path") != expected_ledger or ledger.get("coverage") != "delivery_report_and_declared_artifacts":
            errors.append("delivery contract 3.0 requires delivery/final-hash-ledger-rN.json with complete outer coverage")
    for field in ("accepted_revisions", "deliverables", "readback_evidence", "review_outcomes"):
        if not data.get(field):
            errors.append(f"{field} must not be empty for delivery")
    revisions = data.get("accepted_revisions") if isinstance(data.get("accepted_revisions"), dict) else {}
    for key in ("requirements", "manifest", "cad_result", "blender_result"):
        if not _strict_int(revisions.get(key), 1):
            errors.append(f"accepted_revisions.{key} must be an integer >= 1")
    stage_results = data.get("stage_results")
    if not isinstance(stage_results, dict) or set(stage_results) != {"cad", "blender"}:
        errors.append("stage_results must contain exactly cad and blender paths")
    else:
        errors += unsafe_relative_paths(list(stage_results.values()), "stage_results")
    lineage = data.get("manifest_lineage")
    lineage_errors, _ = validate_manifest_lineage(lineage, revisions.get("manifest"), artifact_hashes=data.get("artifact_hashes") if isinstance(data.get("artifact_hashes"), dict) else None)
    errors += lineage_errors
    errors += unsafe_relative_paths([data.get("project_state_path")], "project_state_path")
    for field in ("deliverables", "readback_evidence", "review_outcomes"):
        errors += unsafe_relative_paths(data.get(field), field)
    hashes = data.get("artifact_hashes")
    if not isinstance(hashes, dict):
        errors.append("artifact_hashes must be an object")
    else:
        errors += unsafe_relative_paths(list(hashes), "artifact_hashes")
        all_paths = relative_path_set(data.get("deliverables")) | relative_path_set(data.get("readback_evidence")) | relative_path_set(data.get("review_outcomes"))
        stage_values = data.get("stage_results", {}).values() if isinstance(data.get("stage_results"), dict) else []
        all_paths |= {value for value in stage_values if isinstance(value, str)} | ({data.get("project_state_path")} if isinstance(data.get("project_state_path"), str) else set())
        all_paths |= {entry.get("path") for entry in (lineage if isinstance(lineage, list) else []) if isinstance(entry, dict)}
        for path in all_paths:
            if not re.fullmatch(r"[0-9a-f]{64}", str(hashes.get(path, ""))):
                errors.append(f"artifact_hashes lacks a valid digest for {path}")
        for index, entry in enumerate(lineage if isinstance(lineage, list) else []):
            if isinstance(entry, dict) and hashes.get(entry.get("path")) != entry.get("sha256"):
                errors.append(f"manifest_lineage[{index}].sha256 must equal artifact_hashes for its path")
    ledger = data.get("hash_ledger")
    if ledger is not None:
        if not isinstance(ledger, dict):
            errors.append("hash_ledger must be an object")
        else:
            errors += unsafe_relative_paths([ledger.get("path")], "hash_ledger.path")
            if ledger.get("coverage") != "delivery_report_and_declared_artifacts":
                errors.append("hash_ledger.coverage must be delivery_report_and_declared_artifacts")
            if isinstance(hashes, dict) and ledger.get("path") in hashes:
                errors.append("artifact_hashes must not contain the outer hash ledger; that would create a cycle")
    fidelity = data.get("geometry_fidelity")
    if fidelity is not None:
        summary = dict(fidelity) if isinstance(fidelity, dict) else fidelity
        if isinstance(summary, dict):
            summary.setdefault("required_checks", ["delivery fidelity evidence"])
        errors += validate_geometry_fidelity(summary, "geometry_fidelity")
        if isinstance(fidelity, dict) and fidelity.get("level") == "envelope_proxy":
            limitations_value = data.get("limitations") if isinstance(data.get("limitations"), list) else []
            limitations = " ".join(str(item).lower() for item in limitations_value)
            if "envelope" not in limitations or not any(term in limitations for term in ("not face", "not final", "proxy")):
                errors.append("envelope_proxy delivery must disclose that it is not face-accurate or a final presentation model")
    if data.get("delivery_contract_version") in {"2.0", "3.0"}:
        for field in ("geometry_fidelity_result", "render_qa_result"):
            errors += _validate_hashed_ref(data.get(field), field)
            ref = data.get(field)
            if isinstance(ref, dict) and isinstance(hashes, dict) and hashes.get(ref.get("path")) != ref.get("sha256"):
                errors.append(f"{field}.sha256 must equal artifact_hashes for its path")
    render_ref = data.get("render_qa_profile")
    if render_ref is not None:
        if not isinstance(render_ref, dict):
            errors.append("render_qa_profile must be an object")
        else:
            errors += unsafe_relative_paths([render_ref.get("path")], "render_qa_profile.path")
            if not _strict_int(render_ref.get("revision"), 1):
                errors.append("render_qa_profile.revision must be an integer >= 1")
            if not SHA256_RE.fullmatch(str(render_ref.get("sha256", ""))):
                errors.append("render_qa_profile.sha256 must be a lowercase SHA-256 digest")
            if isinstance(hashes, dict) and hashes.get(render_ref.get("path")) != render_ref.get("sha256"):
                errors.append("render_qa_profile.sha256 must equal artifact_hashes for its path")
    return errors


def _validate_work_packet_ref(value: Any, location: str = "work_packet") -> list[str]:
    errors = _validate_hashed_ref(value, location, require_revision=True)
    if not isinstance(value, dict) or not isinstance(value.get("packet_id"), str) or not value.get("packet_id"):
        errors.append(f"{location}.packet_id must be a non-empty string")
    return errors


def validate_work_packet_file_binding(reference: Any, project_root: str | Path, expected_project_id: str | None = None,
                                     expected_stage: str | None = None, expected_manifest_revision: int | None = None) -> list[str]:
    errors = _validate_work_packet_ref(reference)
    if not isinstance(reference, dict):
        return errors
    root = Path(project_root).resolve()
    rel = reference.get("path")
    packet_path = (root / str(rel)).resolve() if isinstance(rel, str) else None
    if packet_path is None or root not in packet_path.parents or not packet_path.is_file():
        return errors + [f"work_packet is missing or outside project root: {rel}"]
    if file_sha256(packet_path) != reference.get("sha256"):
        errors.append("work_packet SHA-256 mismatch")
    try:
        packet = load_json(packet_path)
    except Exception as exc:
        return errors + [f"work_packet is invalid JSON: {exc}"]
    errors += validate_work_packet(packet)
    for field in ("packet_id", "revision", "project_id"):
        if packet.get(field) != reference.get(field):
            errors.append(f"work_packet {field} does not match its hash-bound reference")
    if expected_project_id is not None and packet.get("project_id") != expected_project_id:
        errors.append("work_packet project_id does not match consuming artifact")
    if expected_stage is not None and packet.get("stage") != expected_stage:
        errors.append("work_packet stage does not match consuming artifact")
    if expected_manifest_revision is not None and packet.get("manifest_revision") != expected_manifest_revision:
        errors.append("work_packet manifest_revision does not match consuming artifact")
    lineage = packet.get("manifest_lineage")
    lineage_errors, _ = validate_manifest_lineage(
        lineage, packet.get("manifest_revision"), root, packet.get("project_id"), require_authenticated=True,
    )
    errors += [f"work_packet manifest lineage: {item}" for item in lineage_errors]
    current = next((e for e in lineage if isinstance(e, dict) and e.get("revision") == packet.get("manifest_revision")), None) if isinstance(lineage, list) else None
    if not isinstance(current, dict):
        errors.append("work_packet manifest_lineage must contain consumed Manifest revision")
        return errors
    manifest_rel = current.get("path")
    manifest_path = (root / str(manifest_rel)).resolve() if isinstance(manifest_rel, str) else None
    if manifest_path is None or root not in manifest_path.parents or not manifest_path.is_file():
        return errors + ["work_packet consumed Manifest file is missing or outside project root"]
    if manifest_rel not in packet.get("input_paths", []):
        errors.append("consumed Manifest path must be listed in work_packet input_paths")
    if current.get("sha256") != packet.get("manifest_sha256") or file_sha256(manifest_path) != packet.get("manifest_sha256"):
        errors.append("work_packet consumed Manifest SHA-256 mismatch")
    try:
        manifest = load_json(manifest_path)
        errors += validate_manifest(manifest)
        if manifest.get("revision") != packet.get("manifest_revision") or manifest.get("project_id") != packet.get("project_id"):
            errors.append("work_packet consumed Manifest identity mismatch")
    except Exception as exc:
        errors.append(f"work_packet Manifest cannot be loaded: {exc}")
    return errors


def validate_work_packet(data: dict[str, Any]) -> list[str]:
    artifact_type = data.get("artifact_type")
    if artifact_type not in {"cad_work_packet", "blender_work_packet"}:
        errors = ["artifact_type must be cad_work_packet or blender_work_packet"]
    else:
        errors = check_identity(data, artifact_type)
    required = ["packet_id", "stage", "baseline_revision", "manifest_revision", "manifest_sha256", "source_manifest_revision", "manifest_lineage", "input_paths", "allowed_changes", "prohibited_changes", "rules", "output_paths", "acceptance_criteria", "blockers", "stop_conditions", "writer_scope"]
    errors += missing(data, required)
    if data.get("stage") not in {"cad", "blender"}:
        errors.append("stage must be 'cad' or 'blender'")
    if artifact_type == "cad_work_packet":
        lineage_errors, _ = validate_manifest_lineage(data.get("manifest_lineage"), data.get("manifest_revision"))
        errors += lineage_errors
        lineage = data.get("manifest_lineage")
        current = next((entry for entry in lineage if isinstance(entry, dict) and entry.get("revision") == data.get("manifest_revision")), None) if isinstance(lineage, list) else None
        if not isinstance(current, dict) or current.get("sha256") != data.get("manifest_sha256"):
            errors.append("current Manifest lineage SHA-256 must equal manifest_sha256")
        for index, entry in enumerate(lineage if isinstance(lineage, list) else []):
            if isinstance(entry, dict) and entry.get("path") not in (data.get("input_paths") if isinstance(data.get("input_paths"), list) else []):
                errors.append(f"manifest_lineage[{index}].path must be listed in input_paths")
    if not _strict_int(data.get("baseline_revision"), 1):
        errors.append("baseline_revision must be an integer >= 1")
    if not _strict_int(data.get("source_manifest_revision"), 1):
        errors.append("source_manifest_revision must be an integer >= 1")
    if artifact_type == "cad_work_packet" and data.get("stage") != "cad":
        errors.append("cad_work_packet requires stage 'cad'")
    if artifact_type == "blender_work_packet":
        if data.get("stage") != "blender":
            errors.append("blender_work_packet requires stage 'blender'")
        if data.get("upstream_review_outcome") not in {"PASS", "CONDITIONAL_PASS"}:
            errors.append("Blender packet requires accepted upstream_review_outcome")
        upstream = data.get("upstream_review")
        if not isinstance(upstream, dict):
            errors.append("Blender packet requires upstream_review reference")
        else:
            errors += [f"upstream_review.{key} is required" for key in ("path", "sha256", "review_id", "outcome", "manifest_revision") if key not in upstream]
            if not re.fullmatch(r"[0-9a-f]{64}", str(upstream.get("sha256", ""))):
                errors.append("upstream_review.sha256 must be a lowercase SHA-256 digest")
            if not _strict_int(upstream.get("manifest_revision"), 1):
                errors.append("upstream_review.manifest_revision must be an integer >= 1")
        cad_result = data.get("accepted_cad_result")
        if not isinstance(cad_result, dict):
            errors.append("Blender packet requires accepted_cad_result reference")
        else:
            errors += [f"accepted_cad_result.{key} is required" for key in ("path", "sha256", "revision", "manifest_revision", "readback_evidence", "readback_hashes") if key not in cad_result]
            if not re.fullmatch(r"[0-9a-f]{64}", str(cad_result.get("sha256", ""))):
                errors.append("accepted_cad_result.sha256 must be a lowercase SHA-256 digest")
            if not _strict_int(cad_result.get("revision"), 1):
                errors.append("accepted_cad_result.revision must be an integer >= 1")
            if not _strict_int(cad_result.get("manifest_revision"), 1):
                errors.append("accepted_cad_result.manifest_revision must be an integer >= 1")
            if not cad_result.get("readback_evidence"):
                errors.append("accepted_cad_result.readback_evidence must not be empty")
            hashes = cad_result.get("readback_hashes")
            readback_evidence = cad_result.get("readback_evidence") if isinstance(cad_result.get("readback_evidence"), list) else []
            if not isinstance(hashes, dict) or any(not re.fullmatch(r"[0-9a-f]{64}", str(hashes.get(path, ""))) for path in readback_evidence):
                errors.append("accepted_cad_result.readback_hashes must cover every readback evidence path")
        lineage_errors, _ = validate_manifest_lineage(data.get("manifest_lineage"), data.get("manifest_revision"))
        errors += lineage_errors
        lineage = data.get("manifest_lineage")
        input_paths = data.get("input_paths")
        for index, entry in enumerate(lineage if isinstance(lineage, list) else []):
            if isinstance(entry, dict) and entry.get("path") not in (input_paths if isinstance(input_paths, list) else []):
                errors.append(f"manifest_lineage[{index}].path must be listed in input_paths")
        errors += validate_geometry_fidelity(data.get("geometry_fidelity"))
        render_ref = data.get("render_qa_profile")
        errors += _validate_hashed_ref(render_ref, "render_qa_profile")
        if isinstance(render_ref, dict) and render_ref.get("path") not in (input_paths if isinstance(input_paths, list) else []):
            errors.append("render_qa_profile.path must be listed in input_paths")
    if data.get("stage") in {"cad", "blender"}:
        errors += validate_writer_boundary(data, data["stage"])
    if not _strict_int(data.get("manifest_revision"), 1):
        errors.append("manifest_revision must be an integer >= 1")
    if not re.fullmatch(r"[0-9a-f]{64}", str(data.get("manifest_sha256", ""))):
        errors.append("manifest_sha256 must be a lowercase SHA-256 digest")
    if data.get("source_manifest_revision") != data.get("manifest_revision"):
        errors.append("source_manifest_revision must equal manifest_revision")
    if data.get("blockers"):
        errors.append("work packet cannot be issued with unresolved blockers")
    for field in ("input_paths", "output_paths", "writer_scope"):
        errors += unsafe_relative_paths(data.get(field), field)
        if isinstance(data.get(field), list) and any(any(token in path for token in ("*", "?", "[", "]")) for path in data.get(field) if isinstance(path, str)):
            errors.append(f"{field} supports exact paths only; globs are forbidden")
    if isinstance(data.get("output_paths"), list) and isinstance(data.get("writer_scope"), list):
        if len(relative_path_set(data["writer_scope"])) != len(data["writer_scope"]):
            errors.append("writer_scope paths must be unique")
        for output in data["output_paths"]:
            if output not in data["writer_scope"]:
                errors.append(f"output path is outside writer_scope: {output}")
    return errors


def validate_packet_against_manifest_review(
    data: dict[str, Any], manifest: dict[str, Any], review: dict[str, Any] | None = None,
    cad_result: dict[str, Any] | None = None, review_path: str | Path | None = None,
    cad_result_path: str | Path | None = None, manifest_path: str | Path | None = None,
    project_root: str | Path | None = None,
) -> list[str]:
    errors = validate_work_packet(data) + validate_manifest(manifest)
    if data.get("project_id") != manifest.get("project_id"):
        errors.append("work packet project_id does not match manifest")
    if data.get("manifest_revision") != manifest.get("revision"):
        errors.append("work packet manifest_revision does not match supplied Manifest")
    if data.get("baseline_revision") != manifest.get("requirements_revision"):
        errors.append("work packet baseline_revision does not match Manifest requirements_revision")
    if manifest_path is not None and data.get("manifest_sha256") != file_sha256(manifest_path):
        errors.append("work packet manifest_sha256 does not match supplied Manifest")
    if project_root is not None:
        lineage_errors, _ = validate_manifest_lineage(data.get("manifest_lineage"), data.get("manifest_revision"), project_root, data.get("project_id"), require_authenticated=True)
        errors += [f"work packet manifest lineage: {item}" for item in lineage_errors]
    if data.get("artifact_type") == "blender_work_packet":
        lineage_errors, manifest_ancestors = validate_manifest_lineage(
            data.get("manifest_lineage"), data.get("manifest_revision"), project_root,
            data.get("project_id"), require_authenticated=project_root is not None,
        )
        errors += lineage_errors
        lineage = data.get("manifest_lineage")
        current_entry = next((entry for entry in (lineage if isinstance(lineage, list) else []) if isinstance(entry, dict) and entry.get("revision") == data.get("manifest_revision")), {})
        if current_entry.get("sha256") != data.get("manifest_sha256"):
            errors.append("current Manifest lineage SHA-256 must equal manifest_sha256")
        if project_root is not None and manifest_path is not None and isinstance(current_entry.get("path"), str):
            root = Path(project_root).resolve()
            if (root / current_entry["path"]).resolve() != Path(manifest_path).resolve():
                errors.append("current Manifest lineage path does not match supplied Manifest path")
        fidelity = data.get("geometry_fidelity") if isinstance(data.get("geometry_fidelity"), dict) else {}
        comparison_tolerance = manifest.get("comparison_tolerance") if isinstance(manifest.get("comparison_tolerance"), dict) else {}
        tolerance = comparison_tolerance.get("value")
        if fidelity.get("level") == "exact_mesh" and manifest.get("units") == "mm" and _finite_number(tolerance) and fidelity.get("permitted_deviation_mm", math.inf) > tolerance:
            errors.append("exact_mesh permitted_deviation_mm cannot exceed the accepted Manifest tolerance")
        if review is None:
            errors.append("Blender packet requires a supplied upstream review report")
        else:
            errors += validate_review(review)
            ref = data.get("upstream_review", {})
            if review.get("project_id") != data.get("project_id") or review.get("review_id") != ref.get("review_id"):
                errors.append("upstream review identity does not match work packet reference")
            if review.get("outcome") != ref.get("outcome") or review.get("outcome") not in {"PASS", "CONDITIONAL_PASS"}:
                errors.append("upstream review outcome is not accepted or does not match")
            consumed = review.get("consumed_revisions", {})
            if ref.get("manifest_revision") != consumed.get("manifest"):
                errors.append("upstream review historical Manifest revision does not match packet reference")
            if consumed.get("manifest") not in manifest_ancestors:
                errors.append("upstream review consumed Manifest revision is not an ancestor of the Blender packet Manifest")
            if review_path is not None and ref.get("sha256") != file_sha256(review_path):
                errors.append("upstream review SHA-256 does not match packet reference")
            if project_root is not None and review_path is not None and isinstance(ref.get("path"), str):
                if (Path(project_root).resolve() / ref["path"]).resolve() != Path(review_path).resolve():
                    errors.append("upstream review path does not match packet reference")
        if cad_result is None:
            errors.append("Blender packet requires a supplied accepted CAD stage result")
        else:
            errors += validate_stage_result(cad_result)
            accepted = data.get("accepted_cad_result", {})
            if cad_result.get("project_id") != data.get("project_id"):
                errors.append("CAD stage result project_id does not match packet")
            if cad_result.get("stage") != "cad" or cad_result.get("status") != "completed":
                errors.append("accepted CAD stage result must be completed CAD output")
            if cad_result.get("revision") != accepted.get("revision"):
                errors.append("CAD stage result revision does not match packet reference")
            if accepted.get("manifest_revision") != cad_result.get("manifest_revision_consumed"):
                errors.append("CAD stage result historical Manifest revision does not match packet reference")
            if cad_result.get("manifest_revision_consumed") not in manifest_ancestors:
                errors.append("CAD stage result consumed Manifest revision is not an ancestor of the Blender packet Manifest")
            if review is not None:
                consumed = review.get("consumed_revisions", {})
                if consumed.get("stage_result") != cad_result.get("revision"):
                    errors.append("review consumed Stage Result revision does not match accepted CAD result")
                if accepted.get("path") not in review.get("reviewed_artifacts", []):
                    errors.append("reviewed_artifacts does not include accepted CAD result path")
            if accepted.get("readback_evidence") != cad_result.get("readback_evidence"):
                errors.append("packet CAD readback evidence does not match Stage Result")
            if project_root is not None:
                root = Path(project_root).resolve()
                for relative in accepted.get("readback_evidence", []):
                    evidence = (root / relative).resolve()
                    if root not in evidence.parents or not evidence.is_file():
                        errors.append(f"CAD readback evidence is missing or outside project root: {relative}")
                    elif (accepted.get("readback_hashes") if isinstance(accepted.get("readback_hashes"), dict) else {}).get(relative) != file_sha256(evidence):
                        errors.append(f"CAD readback evidence SHA-256 mismatch: {relative}")
            if cad_result_path is not None and accepted.get("sha256") != file_sha256(cad_result_path):
                errors.append("CAD stage result SHA-256 does not match packet reference")
            if project_root is not None and cad_result_path is not None and isinstance(accepted.get("path"), str):
                if (Path(project_root).resolve() / accepted["path"]).resolve() != Path(cad_result_path).resolve():
                    errors.append("CAD stage result path does not match packet reference")
    return errors


def validate_review(data: dict[str, Any]) -> list[str]:
    errors = check_identity(data, "review_report")
    errors += missing(data, ["review_id", "review_stage", "reviewed_artifacts", "review_profiles", "consumed_revisions", "issues", "outcome", "conditions", "evidence_summary", "reviewer_identity", "reviewer_independent", "reviewer_read_only"])
    if data.get("outcome") not in {"PASS", "CONDITIONAL_PASS", "FAIL", "NEEDS_USER_DECISION"}:
        errors.append("invalid review outcome")
    if data.get("review_stage") not in {"cad", "blender", "cross_software"}:
        errors.append("review_stage must be cad, blender, or cross_software")
    if not data.get("reviewed_artifacts"):
        errors.append("reviewed_artifacts must not be empty")
    if not isinstance(data.get("consumed_revisions"), dict) or not data.get("consumed_revisions"):
        errors.append("consumed_revisions must be a non-empty object")
    if not data.get("evidence_summary"):
        errors.append("evidence_summary must not be empty")
    consumed = data.get("consumed_revisions") if isinstance(data.get("consumed_revisions"), dict) else {}
    required_consumed = {"manifest", "stage_result"} if data.get("review_stage") in {"cad", "blender"} else {"manifest", "cad_result", "blender_result"}
    for key in required_consumed:
        if not _strict_int(consumed.get(key), 1):
            errors.append(f"consumed_revisions.{key} must be an integer >= 1 for {data.get('review_stage')} review")
    errors += unsafe_relative_paths(data.get("reviewed_artifacts"), "reviewed_artifacts")
    if data.get("reviewer_independent") is not True or data.get("reviewer_read_only") is not True:
        errors.append("reviewer must be explicitly independent and read-only")
    if not data.get("reviewer_identity"):
        errors.append("reviewer_identity is required")
    if data.get("outcome") == "CONDITIONAL_PASS" and not data.get("conditions"):
        errors.append("CONDITIONAL_PASS requires non-empty conditions")
    if data.get("outcome") != "CONDITIONAL_PASS" and data.get("conditions"):
        errors.append("conditions are only valid with CONDITIONAL_PASS")
    issue_fields = ["issue_id", "severity", "category", "rule", "object_ids", "finding", "evidence", "impact", "recommendation", "confidence", "disposition"]
    ids: set[str] = set()
    issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    if not isinstance(data.get("issues"), list):
        errors.append("issues must be an array")
    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            errors.append(f"issues[{index}] must be an object")
            continue
        errors += [f"issues[{index}].{field} is required" for field in issue_fields if field not in issue]
        if issue.get("severity") not in {"Critical", "Important", "Minor", "Needs Human Decision"}:
            errors.append(f"issues[{index}].severity is invalid")
        issue_id = issue.get("issue_id")
        if issue_id in ids:
            errors.append(f"duplicate issue_id: {issue_id}")
        ids.add(issue_id)
    blocking = any(issue.get("severity") in {"Critical", "Important"} and issue.get("disposition") != "resolved" for issue in issues if isinstance(issue, dict))
    if blocking and data.get("outcome") in {"PASS", "CONDITIONAL_PASS"}:
        errors.append("unresolved Critical/Important issue is incompatible with passing outcome")
    human_decision = any(issue.get("severity") == "Needs Human Decision" and issue.get("disposition") != "resolved" for issue in issues if isinstance(issue, dict))
    if human_decision and data.get("outcome") != "NEEDS_USER_DECISION":
        errors.append("unresolved Needs Human Decision issue requires NEEDS_USER_DECISION outcome")
    if "review_contract_version" in data and data.get("review_contract_version") != "1.0":
        errors.append("review_contract_version must be '1.0'")
    if data.get("review_contract_version") == "1.0" and data.get("review_stage") == "blender":
        assessment = data.get("geometry_fidelity_assessment")
        if not isinstance(assessment, dict):
            errors.append("Blender review requires geometry_fidelity_assessment")
        else:
            errors += validate_geometry_fidelity(assessment.get("declared"), "geometry_fidelity_assessment.declared")
            if assessment.get("outcome") not in {"PASS", "FAIL", "NEEDS_USER_DECISION"}:
                errors.append("geometry_fidelity_assessment.outcome is invalid")
            elif assessment.get("outcome") == "FAIL" and data.get("outcome") != "FAIL":
                errors.append("failed geometry fidelity assessment requires FAIL review outcome")
            elif assessment.get("outcome") == "NEEDS_USER_DECISION" and data.get("outcome") != "NEEDS_USER_DECISION":
                errors.append("geometry fidelity assessment needing user decision requires NEEDS_USER_DECISION review outcome")
            if not isinstance(assessment.get("evidence"), list) or not assessment.get("evidence"):
                errors.append("geometry_fidelity_assessment.evidence must be non-empty")
            errors += _validate_hashed_ref(assessment.get("result"), "geometry_fidelity_assessment.result")
    if data.get("review_contract_version") == "1.0":
        required_profiles = {
            "cad": {"common_acceptance", "cad_drawing_review"},
            "blender": {"common_acceptance", "blender_model_review"},
            "cross_software": {"common_acceptance", "cross_software_consistency"},
        }
        profiles = data.get("review_profiles")
        profile_names = {item for item in profiles if isinstance(item, str)} if isinstance(profiles, list) else set()
        if not isinstance(profiles, list) or len(profile_names) != len(profiles) or not required_profiles.get(data.get("review_stage"), set()).issubset(profile_names):
            errors.append("review_profiles do not match review_stage")
    return errors


VALIDATORS = {
    "requirements_baseline": validate_baseline,
    "project_manifest": validate_manifest,
    "project_state": validate_project_state,
    "cad_work_packet": validate_work_packet,
    "blender_work_packet": validate_work_packet,
    "stage_result": validate_stage_result,
    "manifest_patch": validate_manifest_patch,
    "review_report": validate_review,
    "delivery_report": validate_delivery_report,
    "cad_snapshot": lambda data: validate_snapshot(data, "cad_snapshot"),
    "blender_snapshot": lambda data: validate_snapshot(data, "blender_snapshot"),
    "review_work_packet": validate_review_work_packet,
    "attempt_record": validate_attempt_record,
    "completion_receipt": validate_completion_receipt,
    "process_log": validate_process_log,
    "canonical_mesh": lambda data: canonical_mesh_metrics(data)[0],
    "render_qa_profile": validate_render_qa_profile,
    "geometry_fidelity_result": validate_geometry_fidelity_result,
    "render_qa_result": validate_render_qa_result,
    "hash_ledger": validate_hash_ledger,
    "delivery_checklist": validate_delivery_checklist,
}


def cli(validator, label: str) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=f"Validate a {label} JSON artifact.")
    parser.add_argument("path", help="Path to the JSON artifact")
    args = parser.parse_args()
    try:
        data = load_json(args.path)
        errors = validator(data)
        warnings = legacy_warnings(data)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
        warnings = []
    if errors:
        print(json.dumps({"valid": False, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"valid": True, "artifact": str(Path(args.path).resolve()), "warnings": warnings}, ensure_ascii=False, indent=2))
    return 0
