import argparse
import json
from pathlib import Path

from artifact_validation import file_sha256, load_json, validate_manifest_against_baseline, validate_manifest_lineage

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate a Project Manifest against its confirmed Requirements Baseline.")
    parser.add_argument("path", help="Path to Project Manifest JSON")
    parser.add_argument("--baseline", required=True, help="Path to the referenced Requirements Baseline JSON")
    parser.add_argument("--project-root", help="Project root for authenticated Manifest parent-chain validation")
    parser.add_argument("--manifest-lineage", help="Optional JSON file containing the final Manifest lineage array")
    parser.add_argument("--legacy-unverified", action="store_true", help="Migration analysis only; revision >1 remains unauthenticated and not formally valid")
    args = parser.parse_args()
    authenticated = False
    try:
        manifest = load_json(args.path)
        validation_manifest = dict(manifest)
        if args.legacy_unverified and "parent_manifest" not in validation_manifest:
            revision = validation_manifest.get("revision")
            validation_manifest["parent_manifest"] = None if revision == 1 else {"revision": revision - 1, "path": "legacy-unverified", "sha256": "0" * 64}
        errors = validate_manifest_against_baseline(validation_manifest, load_json(args.baseline), args.baseline)
        authenticated = manifest.get("revision") == 1
        if args.project_root and args.manifest_lineage:
            lineage = load_json(args.manifest_lineage)
            if isinstance(lineage, dict):
                lineage = lineage.get("manifest_lineage")
            lineage_errors, _ = validate_manifest_lineage(lineage, manifest.get("revision"), Path(args.project_root), manifest.get("project_id"), require_authenticated=True)
            errors += lineage_errors
            final_entry = next((entry for entry in lineage if isinstance(entry, dict) and entry.get("revision") == manifest.get("revision")), None) if isinstance(lineage, list) else None
            supplied_path = Path(args.path).resolve()
            final_path = (Path(args.project_root).resolve() / str(final_entry.get("path"))).resolve() if isinstance(final_entry, dict) and isinstance(final_entry.get("path"), str) else None
            if final_path is None or final_path != supplied_path:
                errors.append("supplied Manifest path must equal the final authenticated lineage file")
            elif final_entry.get("sha256") != file_sha256(supplied_path):
                errors.append("supplied Manifest SHA-256 must equal the final authenticated lineage entry")
            elif load_json(final_path) != manifest:
                errors.append("supplied Manifest content must equal the final authenticated lineage file")
            authenticated = not errors
        elif isinstance(manifest.get("revision"), int) and manifest.get("revision") > 1:
            if not args.legacy_unverified:
                errors.append("revision >1 requires --project-root and --manifest-lineage for authenticated parent-chain validation")
            authenticated = False
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors and authenticated, "migration_analysis_valid": not errors, "authenticated": authenticated, "legacy_unverified": bool(args.legacy_unverified), "errors": errors}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not errors and authenticated else 1)
