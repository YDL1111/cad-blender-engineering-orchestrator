import argparse
import json

from artifact_validation import file_sha256, load_json, validate_packet_against_manifest_review, validate_render_qa_profile

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate a stage Work Packet against its Manifest and, for Blender, the upstream CAD review.")
    parser.add_argument("path", help="Path to Work Packet JSON")
    parser.add_argument("--manifest", required=True, help="Path to the exact referenced Project Manifest")
    parser.add_argument("--upstream-review", help="Required for Blender packets")
    parser.add_argument("--accepted-cad-result", help="Required for Blender packets")
    parser.add_argument("--project-root", required=True, help="Required for all packets; authenticates the actual Manifest lineage and evidence files")
    parser.add_argument("--render-qa-profile", help="Required for Blender packets; exact profile referenced by the packet")
    args = parser.parse_args()
    try:
        packet = load_json(args.path)
        review = load_json(args.upstream_review) if args.upstream_review else None
        cad_result = load_json(args.accepted_cad_result) if args.accepted_cad_result else None
        errors = validate_packet_against_manifest_review(
            packet, load_json(args.manifest), review, cad_result,
            args.upstream_review, args.accepted_cad_result, args.manifest, args.project_root,
        )
        if packet.get("artifact_type") == "blender_work_packet":
            if not args.project_root:
                errors.append("Blender packet validation requires --project-root")
            if not args.render_qa_profile:
                errors.append("Blender packet validation requires --render-qa-profile")
            else:
                profile = load_json(args.render_qa_profile)
                errors += [f"render QA profile: {item}" for item in validate_render_qa_profile(profile)]
                ref = packet.get("render_qa_profile", {})
                if profile.get("project_id") != packet.get("project_id") or profile.get("revision") != ref.get("revision"):
                    errors.append("render QA profile identity/revision mismatch")
                if ref.get("sha256") != file_sha256(args.render_qa_profile):
                    errors.append("render QA profile SHA-256 mismatch")
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)
