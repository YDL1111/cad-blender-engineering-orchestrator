from __future__ import annotations

import argparse
import json
from pathlib import Path

from artifact_validation import VALIDATORS, file_sha256, load_json, validate_manifest_lineage, validate_review_work_packet


def validate_files(packet: dict, project_root: Path) -> list[str]:
    errors = validate_review_work_packet(packet)
    root = project_root.resolve()
    consumed_refs = packet.get("consumed") if isinstance(packet.get("consumed"), dict) else {}
    manifest_ref = consumed_refs.get("manifest") if isinstance(consumed_refs.get("manifest"), dict) else {}
    consumed_manifest_revision = manifest_ref.get("revision")
    lineage_errors, ancestors = validate_manifest_lineage(packet.get("manifest_lineage"), consumed_manifest_revision, root, packet.get("project_id"), require_authenticated=True)
    errors += lineage_errors
    expected_types = {
        "manifest": "project_manifest", "stage_result": "stage_result", "cad_result": "stage_result",
        "blender_result": "stage_result", "upstream_review": "review_report", "cad_review": "review_report",
        "blender_review": "review_report",
    }
    input_paths = packet.get("input_paths") if isinstance(packet.get("input_paths"), list) else []
    input_hashes = packet.get("input_hashes") if isinstance(packet.get("input_hashes"), dict) else {}
    for relative in input_paths:
        if not isinstance(relative, str):
            continue
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            errors.append(f"review input is missing or outside project root: {relative}")
        elif input_hashes.get(relative) != file_sha256(path):
            errors.append(f"review input SHA-256 mismatch: {relative}")
    loaded: dict[str, dict] = {}
    for name, ref in consumed_refs.items():
        relative = ref.get("path") if isinstance(ref, dict) else None
        path = (root / str(relative)).resolve()
        if root not in path.parents or not path.is_file():
            continue
        try:
            artifact = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"consumed.{name} is invalid JSON: {exc}")
            continue
        loaded[name] = artifact
        if artifact.get("project_id") != packet.get("project_id"):
            errors.append(f"consumed.{name} project_id mismatch")
        if artifact.get("revision") != ref.get("revision"):
            errors.append(f"consumed.{name} revision mismatch")
        if artifact.get("artifact_type") != expected_types.get(name):
            errors.append(f"consumed.{name} artifact_type mismatch")
        elif artifact.get("artifact_type") in VALIDATORS:
            errors += [f"consumed.{name}: {item}" for item in VALIDATORS[artifact["artifact_type"]](artifact)]
        if name.endswith("result") or name == "stage_result":
            if artifact.get("manifest_revision_consumed") not in ancestors:
                errors.append(f"consumed.{name} historical Manifest revision is not an ancestor")
        if name.endswith("review") or name == "upstream_review":
            consumed_revisions = artifact.get("consumed_revisions") if isinstance(artifact.get("consumed_revisions"), dict) else {}
            if consumed_revisions.get("manifest") not in ancestors:
                errors.append(f"consumed.{name} historical Manifest revision is not an ancestor")
    stage = packet.get("review_stage")
    expected_result_stages = {
        "cad": {"stage_result": "cad"},
        "blender": {"stage_result": "blender"},
        "cross_software": {"cad_result": "cad", "blender_result": "blender"},
    }
    for name, expected_stage in expected_result_stages.get(stage, {}).items():
        result = loaded.get(name)
        if result and result.get("stage") != expected_stage:
            errors.append(f"consumed.{name} must be a {expected_stage} Stage Result")
    expected_review_stages = {
        "blender": {"upstream_review": "cad"},
        "cross_software": {"cad_review": "cad", "blender_review": "blender"},
    }
    for name, expected_stage in expected_review_stages.get(stage, {}).items():
        review = loaded.get(name)
        if not review:
            continue
        if review.get("review_stage") != expected_stage:
            errors.append(f"consumed.{name} must be a {expected_stage} review")
        if review.get("outcome") not in {"PASS", "CONDITIONAL_PASS"}:
            errors.append(f"consumed.{name} must have an accepted outcome")
    if stage == "cross_software":
        for prefix in ("cad", "blender"):
            result_name, review_name = f"{prefix}_result", f"{prefix}_review"
            result, review = loaded.get(result_name), loaded.get(review_name)
            result_ref = consumed_refs.get(result_name, {}) if isinstance(consumed_refs, dict) else {}
            if not result or not review:
                continue
            consumed_revisions = review.get("consumed_revisions") if isinstance(review.get("consumed_revisions"), dict) else {}
            if consumed_revisions.get("stage_result") != result.get("revision"):
                errors.append(f"consumed.{review_name} Stage Result revision does not match consumed.{result_name}")
            if result_ref.get("path") not in review.get("reviewed_artifacts", []):
                errors.append(f"consumed.{review_name} reviewed_artifacts does not include consumed.{result_name}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an independent read-only review work packet and all hashed inputs.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        errors = validate_files(load_json(args.path), args.project_root)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
