"""Exercise validators, safe scaffolding, immutable patching, and comparison."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

from apply_manifest_patch import apply_patch
from adapt_legacy_snapshot import adapt_snapshot
from artifact_validation import (
    VALIDATORS, load_json, validate_baseline, validate_delivery_report, validate_manifest,
    validate_manifest_against_baseline, validate_manifest_lineage, validate_packet_against_manifest_review,
    validate_project_state, validate_review, validate_review_work_packet, validate_snapshot,
    validate_stage_result, validate_work_packet, validate_attempt_record, validate_completion_receipt,
    validate_render_qa_profile, validate_geometry_fidelity_result, validate_render_qa_result, validate_hash_ledger,
    canonical_mesh_metrics, validate_process_log,
)
from compare_cad_blender import compare, typed_differences
from scaffold_project import create_project
from validate_delivery_report import validate_files, validate_stage_evidence, _png_dimensions
from validate_review_work_packet import validate_files as validate_review_packet_files
from publish_delivery import publish_release
from migrate_delivery_layout import _safe_extract
from environment import resolve_executable, resolve_projects_root
from check_environment import inspect_environment
from package_skill import build_zip, collect_files, inspect_zip, SKILL_NAME

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_ROOT / "assets" / "templates"


def template(name: str) -> dict:
    return load_json(TEMPLATES / name)


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS {label}")


def png_bytes(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="portable-skill-test-") as portable_directory:
        portable = Path(portable_directory)
        home = portable / "User Home"
        home.mkdir()
        no_drive_root, source = resolve_projects_root(environ={}, home=home)
        expect(no_drive_root == (home / "CBEngineeringProjects").resolve() and source == "default", "project root defaults under user home without a D drive")
        env_root = portable / "Environment Projects"
        selected, source = resolve_projects_root(environ={"CBE_PROJECTS_ROOT": str(env_root)}, home=home)
        expect(selected == env_root.resolve() and source == "CBE_PROJECTS_ROOT", "project root reads environment variable")
        config = portable / "user-config.json"
        config.write_text(json.dumps({"projects_root": str(portable / "Configured Projects")}), encoding="utf-8")
        selected, source = resolve_projects_root(environ={}, config_path=config, home=home)
        expect(selected == (portable / "Configured Projects").resolve() and source == "user_config", "project root reads user config")
        selected, source = resolve_projects_root(environ={"CBE_PROJECTS_ROOT": str(env_root)}, config_path=config, home=home)
        expect(selected == env_root.resolve() and source == "CBE_PROJECTS_ROOT", "project-root environment variable outranks user config")
        explicit_root = portable / "Explicit Projects"
        selected, source = resolve_projects_root(explicit_root, environ={"CBE_PROJECTS_ROOT": str(env_root)}, config_path=config, home=home)
        expect(selected == explicit_root.resolve() and source == "explicit", "explicit project root outranks environment and config")
        cmd = portable / "Applications With Spaces" / "FreeCAD Cmd.exe"
        exe = portable / "Applications With Spaces" / "blender.exe"
        cmd.parent.mkdir()
        cmd.write_bytes(b"test executable marker")
        exe.write_bytes(b"test executable marker")
        freecad = resolve_executable("freecad", cmd, environ={}, common_candidates=[])
        blender = resolve_executable("blender", exe, environ={}, common_candidates=[])
        expect(freecad["path"] == str(cmd.resolve()) and blender["path"] == str(exe.resolve()), "application paths containing spaces resolve intact")
        expect(resolve_executable("freecad", portable / "missing.exe", environ={"CBE_FREECAD_CMD": str(cmd)}, common_candidates=[])["status"] == "blocked", "invalid explicit application path does not silently fall back")
        expect(resolve_executable("freecad", environ={"CBE_FREECAD_CMD": str(cmd), "PATH": ""}, common_candidates=[])["source"] == "CBE_FREECAD_CMD", "application environment path outranks PATH")
        expect(resolve_executable("blender", environ={"PATH": str(exe.parent)}, common_candidates=[cmd])["source"] == "PATH", "PATH application discovery outranks common locations")
        expect(resolve_executable("blender", environ={"PATH": ""}, common_candidates=[exe])["source"] == "windows_install", "common installation discovery follows PATH")
        missing = inspect_environment(environ={"PATH": ""}, home=home, query_versions=False, common_candidates={"freecad": [], "blender": []})
        expect(not missing["preflight_ready"] and {item["capability"] for item in missing["blockers"]} == {"freecad", "blender"}, "missing applications produce explicit capability blockers")
        invalid_binary = inspect_environment(freecad_cmd=cmd, blender_exe=exe, environ={}, home=home, common_candidates={"freecad": [], "blender": []})
        expect(not invalid_binary["preflight_ready"] and {item["capability"] for item in invalid_binary["blockers"]} == {"freecad", "blender"}, "existing non-executables fail version probes with explicit blockers")
        files = collect_files(SKILL_ROOT)
        archive_bytes = build_zip(files)
        expect(not inspect_zip(archive_bytes, files) and f"{SKILL_NAME}/SKILL.md" in zipfile.ZipFile(io.BytesIO(archive_bytes)).namelist(), "release ZIP extracts with SKILL.md in the top-level Skill directory")
        extracted = portable / "unzipped"
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            archive.extractall(extracted)
        expect((extracted / SKILL_NAME / "SKILL.md").is_file() and (extracted / SKILL_NAME / "scripts" / "scaffold_project.py").is_file(), "extracted release has a usable Skill directory structure")
        expect(not any("__pycache__" in path or path.lower().endswith((".pyc", ".fcstd", ".blend", ".step", ".png", ".zip", ".log")) for path in files), "release files exclude cache, project artifacts, images, ZIPs and logs")
        expect(not any(path.endswith(name) for path in files for name in ("real_project_regression.py", "real_migration_regression.py")), "release omits historical project-specific regression scripts")
        expect(archive_bytes == build_zip(files), "release ZIP is byte-for-byte deterministic")
        private_source = portable / "private-source"
        private_source.mkdir()
        for name, data in files.items():
            destination = private_source / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        (private_source / "README.md").write_text("private path: C:/" + "Users/private-person/model.FCStd", encoding="utf-8")
        try:
            collect_files(private_source)
        except ValueError:
            print("PASS release rejects absolute personal paths")
        else:
            raise AssertionError("release allowed an absolute personal path")
        (private_source / "README.md").write_text(json.dumps({"source": "C:" + "\\Users\\private-person\\model.FCStd"}), encoding="utf-8")
        try:
            collect_files(private_source)
        except ValueError:
            print("PASS release rejects JSON-escaped personal paths")
        else:
            raise AssertionError("release allowed a JSON-escaped personal path")
        (private_source / "README.md").write_text("network share: " + "\\\\" + "server" + "\\" + "share" + "\\private-model.FCStd", encoding="utf-8")
        try:
            collect_files(private_source)
        except ValueError:
            print("PASS release rejects UNC paths")
        else:
            raise AssertionError("release allowed a UNC path")
        (private_source / "README.md").write_bytes((SKILL_ROOT / "README.md").read_bytes())
        (private_source / "scripts" / "__pycache__").mkdir(parents=True)
        (private_source / "scripts" / "__pycache__" / "sample.pyc").write_bytes(b"cache")
        (private_source / "assets" / "templates").mkdir(parents=True, exist_ok=True)
        (private_source / "assets" / "templates" / "real-project.blend").write_bytes(b"model")
        (private_source / "local.log").write_text("local log", encoding="utf-8")
        expect(not any(path.endswith((".pyc", ".blend", ".log")) for path in collect_files(private_source)), "package collector excludes injected cache, real model and log files")
        (private_source / "docs" / "stray-private-note.md").write_text("client data outside the release allowlist", encoding="utf-8")
        expect("docs/stray-private-note.md" not in collect_files(private_source), "release allowlist excludes unknown Markdown files")
    filenames = tuple(path.name for path in sorted(TEMPLATES.glob("*.json")))
    for filename in filenames:
        data = template(filename)
        expect(data["artifact_type"] in VALIDATORS, f"validator registered for authoritative template {filename}")
        expect(not VALIDATORS[data["artifact_type"]](data), f"valid authoritative template {filename}")

    baseline = template("requirements-baseline.json")
    expect(not validate_baseline(baseline), "valid baseline")
    broken = copy.deepcopy(baseline)
    del broken["raw_request"]
    expect(any("raw_request" in item for item in validate_baseline(broken)), "missing required field detected")
    assumed = copy.deepcopy(baseline)
    assumed["requirements"][0].update({"status": "assumed", "blocks": []})
    assumed["confirmation"]["confirmed_by"] = "orchestrator"
    expect(any("human" in item for item in validate_baseline(assumed)), "unaccepted assumption and agent confirmation rejected")

    manifest = template("project-manifest.json")
    expect(not validate_manifest(manifest), "valid manifest")
    duplicate = copy.deepcopy(manifest)
    duplicate["objects"].append(copy.deepcopy(duplicate["objects"][0]))
    expect(any("duplicate object_id" in item for item in validate_manifest(duplicate)), "duplicate object ID detected")
    blocked = copy.deepcopy(manifest)
    blocked["constraints"][0].update({"status": "blocked", "blocks": ["cad"]})
    expect(any("blocker" in item for item in validate_manifest(blocked)), "blocked constraint detected")
    blocked_object = copy.deepcopy(manifest)
    blocked_object["objects"][0].update({"status": "unknown", "blocks": ["cad"]})
    expect(any("blocker" in item for item in validate_manifest(blocked_object)), "blocked object detected")
    baseline_path = TEMPLATES / "requirements-baseline.json"
    expect(not validate_manifest_against_baseline(manifest, baseline, baseline_path), "Manifest bound to confirmed baseline hash/revision")
    orphan = copy.deepcopy(manifest)
    orphan["requirements_revision"] = 999
    orphan["requirements_baseline"]["revision"] = 999
    expect(any("revision" in item for item in validate_manifest_against_baseline(orphan, baseline, baseline_path)), "orphan Manifest revision rejected")
    inflated = copy.deepcopy(manifest)
    inflated["comparison_tolerance"]["value"] = 2.0
    expect(any("comparison_tolerance.value" in item for item in validate_manifest_against_baseline(inflated, baseline, baseline_path)), "inflated comparison tolerance rejected by baseline binding")

    packet = template("work-packet.json")
    expect(not validate_work_packet(packet), "valid CAD work packet")
    bad_version = copy.deepcopy(packet)
    bad_version["source_manifest_revision"] = 2
    expect(any("source_manifest_revision" in item for item in validate_work_packet(bad_version)), "illegal version relation detected")
    unsafe_packet = copy.deepcopy(packet)
    unsafe_packet["output_paths"] = ["X:/unrelated/output.step"]
    unsafe_packet["writer_scope"] = ["X:/unrelated/output.step"]
    expect(any("project root" in item for item in validate_work_packet(unsafe_packet)), "out-of-root writer path rejected")
    case_collision = copy.deepcopy(packet)
    case_collision["input_paths"].append("CAD\\SOURCE\\EXAMPLE.FCSTD")
    expect(any("must not overlap" in item for item in validate_work_packet(case_collision)), "Windows case-insensitive input/output overlap rejected")
    parent_collision = copy.deepcopy(packet)
    parent_collision["input_paths"].append("CAD\\SOURCE")
    expect(any("must not overlap" in item for item in validate_work_packet(parent_collision)), "read-only parent directory cannot contain writable output")
    overlapping_outputs = copy.deepcopy(packet)
    overlapping_outputs["output_paths"] = overlapping_outputs["writer_scope"] = ["cad/source", "cad/source/example.FCStd"]
    expect(any("parent/child" in item for item in validate_work_packet(overlapping_outputs)), "Work Packet output parent/child overlap rejected")
    result_write = copy.deepcopy(packet)
    result_write["output_paths"] = result_write["writer_scope"] = ["cad/stage-result-v2.json"]
    expect(any("authoritative artifact" in item for item in validate_work_packet(result_write)), "operator cannot write an accepted Stage Result")
    for disguised in ("cad/manifest-v2.json", "cad/project-state.json", "cad/stage-result-final.json"):
        disguised_packet = copy.deepcopy(packet)
        disguised_packet["output_paths"] = disguised_packet["writer_scope"] = [disguised]
        expect(any("authoritative artifact" in item for item in validate_work_packet(disguised_packet)), f"operator cannot write disguised authoritative artifact {disguised}")
    escaped_output = copy.deepcopy(packet)
    escaped_output["output_paths"] = escaped_output["writer_scope"] = ["cad/../manifests/manifest-v2.json"]
    expect(validate_work_packet(escaped_output), "canonical path traversal into Manifest storage rejected")
    expect(not validate_packet_against_manifest_review(packet, manifest, manifest_path=TEMPLATES / "project-manifest.json"), "CAD packet bound to Manifest")

    blender_packet = template("blender-work-packet.json")
    review = template("review-report.json")
    cad_result = template("stage-result.json")
    review_path = TEMPLATES / "review-report.json"
    cad_result_path = TEMPLATES / "stage-result.json"
    expect(not validate_packet_against_manifest_review(blender_packet, manifest, review, cad_result, review_path, cad_result_path, TEMPLATES / "project-manifest.json"), "Blender packet bound to accepted CAD review and result")
    weak_blender = copy.deepcopy(blender_packet)
    del weak_blender["upstream_review"]
    expect(any("upstream_review" in item for item in validate_work_packet(weak_blender)), "self-reported Blender upstream acceptance rejected")
    stale_review = copy.deepcopy(review)
    stale_review["consumed_revisions"]["manifest"] = 999
    expect(any("consumed Manifest" in item for item in validate_packet_against_manifest_review(blender_packet, manifest, stale_review, cad_result)), "stale review revision rejected")
    expect(any("supplied accepted CAD" in item for item in validate_packet_against_manifest_review(blender_packet, manifest, review)), "missing CAD stage result rejected")
    wrong_readback = copy.deepcopy(blender_packet)
    wrong_readback["accepted_cad_result"]["readback_evidence"] = ["cad/exports/not-the-result.json"]
    wrong_readback["accepted_cad_result"]["readback_hashes"] = {"cad/exports/not-the-result.json": "0" * 64}
    expect(any("does not match Stage Result" in item for item in validate_packet_against_manifest_review(wrong_readback, manifest, review, cad_result)), "packet read-back evidence must match CAD Stage Result")

    historical_manifest = copy.deepcopy(manifest)
    historical_manifest["revision"] = 3
    historical_manifest["parent_manifest"] = {
        "revision": 2,
        "path": "manifests/manifest-v2.json",
        "sha256": "0" * 64,
    }
    historical_packet = copy.deepcopy(blender_packet)
    historical_packet.update({"manifest_revision": 3, "source_manifest_revision": 3, "manifest_sha256": "0" * 64})
    historical_packet["manifest_lineage"] = [
        {"revision": 1, "parent_revision": None, "path": "manifests/manifest-v1.json", "sha256": "0" * 64},
        {"revision": 2, "parent_revision": 1, "path": "manifests/manifest-v2.json", "sha256": "0" * 64},
        {"revision": 3, "parent_revision": 2, "path": "manifests/manifest-v3.json", "sha256": "0" * 64},
    ]
    historical_packet["input_paths"] = list(dict.fromkeys(historical_packet["input_paths"] + [entry["path"] for entry in historical_packet["manifest_lineage"]]))
    expect(not validate_packet_against_manifest_review(historical_packet, historical_manifest, review, cad_result), "Blender packet accepts truthful ancestor CAD evidence")
    nonancestor = copy.deepcopy(historical_packet)
    nonancestor["accepted_cad_result"]["manifest_revision"] = 99
    cad_nonancestor = copy.deepcopy(cad_result)
    cad_nonancestor["manifest_revision_consumed"] = 99
    expect(any("not an ancestor" in item for item in validate_packet_against_manifest_review(nonancestor, historical_manifest, review, cad_nonancestor)), "non-ancestor CAD evidence rejected")
    branched = copy.deepcopy(historical_packet["manifest_lineage"])
    branched.append({"revision": 4, "parent_revision": 1, "path": "manifests/manifest-v4.json", "sha256": "0" * 64})
    expect(any("branches" in item for item in validate_manifest_lineage(branched, 3)[0]), "branched Manifest lineage rejected")
    missing_parent = copy.deepcopy(historical_packet["manifest_lineage"])
    missing_parent[1]["parent_revision"] = 9
    expect(validate_manifest_lineage(missing_parent, 3)[0], "missing/forward Manifest ancestor rejected")
    cyclic = copy.deepcopy(historical_packet["manifest_lineage"])
    cyclic[0]["parent_revision"] = 3
    expect(any("cycle" in item for item in validate_manifest_lineage(cyclic, 3)[0]), "cyclic Manifest lineage rejected")
    unhashable_parent = copy.deepcopy(historical_packet["manifest_lineage"])
    unhashable_parent[1]["parent_revision"] = []
    expect(validate_manifest_lineage(unhashable_parent, 3)[0], "malformed unhashable Manifest parent returns structured errors")

    review_packet = template("review-work-packet.json")
    expect(not validate_review_work_packet(review_packet), "valid independent review work packet")
    blender_review_packet = copy.deepcopy(review_packet)
    blender_review_packet.update({"packet_id": "REVIEW-WP-BLENDER-001", "review_stage": "blender", "review_profiles": ["common_acceptance", "blender_model_review", "mechanical_review"], "geometry_fidelity": copy.deepcopy(blender_packet["geometry_fidelity"]), "output_paths": ["reviews/blender-review-v1.json", "reviews/evidence/blender-review-readback-v1.json"], "writer_scope": ["reviews/blender-review-v1.json", "reviews/evidence/blender-review-readback-v1.json"]})
    blender_review_packet["consumed"] = {
        "manifest": {"path": "manifests/manifest-v1.json", "revision": 1, "sha256": "0" * 64},
        "stage_result": {"path": "blender/stage-result-v1.json", "revision": 1, "sha256": "0" * 64},
        "upstream_review": {"path": "reviews/cad-review-v1.json", "revision": 1, "sha256": "0" * 64},
    }
    blender_review_packet["input_paths"] = [ref["path"] for ref in blender_review_packet["consumed"].values()]
    blender_review_packet["input_hashes"] = {path: "0" * 64 for path in blender_review_packet["input_paths"]}
    blender_review_errors = validate_review_work_packet(blender_review_packet)
    expect(not blender_review_errors, f"valid Blender review work packet: {blender_review_errors}")
    cross_review_packet = copy.deepcopy(review_packet)
    cross_review_packet.update({"packet_id": "REVIEW-WP-CROSS-001", "review_stage": "cross_software", "review_profiles": ["common_acceptance", "cross_software_consistency"], "output_paths": ["reviews/cross-software-v1.json", "reviews/evidence/cross-software-readback-v1.json"], "writer_scope": ["reviews/cross-software-v1.json", "reviews/evidence/cross-software-readback-v1.json"]})
    cross_review_packet["consumed"] = {
        name: {"path": path, "revision": 1, "sha256": "0" * 64}
        for name, path in {
            "manifest": "manifests/manifest-v1.json", "cad_result": "cad/stage-result-v1.json",
            "blender_result": "blender/stage-result-v1.json", "cad_review": "reviews/cad-review-v1.json",
            "blender_review": "reviews/blender-review-v1.json",
        }.items()
    }
    cross_review_packet["input_paths"] = [ref["path"] for ref in cross_review_packet["consumed"].values()]
    cross_review_packet["input_hashes"] = {path: "0" * 64 for path in cross_review_packet["input_paths"]}
    cross_review_errors = validate_review_work_packet(cross_review_packet)
    expect(not cross_review_errors, f"valid cross-software review work packet: {cross_review_errors}")
    glob_scope = copy.deepcopy(review_packet)
    glob_scope["writer_scope"] = ["reviews/*.json"]
    expect(validate_review_work_packet(glob_scope), "review writer scope rejects globs and non-exact outputs")
    mutable_review = copy.deepcopy(review_packet)
    mutable_review["writer_scope"] = mutable_review["output_paths"] + ["cad/source/example.FCStd"]
    expect(validate_review_work_packet(mutable_review), "review packet cannot grant engineering write access")

    malformed_blender_packet = copy.deepcopy(blender_packet)
    malformed_blender_packet["manifest_lineage"] = None
    expect(validate_work_packet(malformed_blender_packet), "null work-packet Manifest lineage returns structured errors")
    malformed_review_packet = copy.deepcopy(review_packet)
    malformed_review_packet["manifest_lineage"] = None
    expect(validate_review_work_packet(malformed_review_packet), "null review-packet Manifest lineage returns structured errors")
    null_consumed_review_packet = copy.deepcopy(review_packet)
    null_consumed_review_packet["consumed"] = None
    expect(validate_review_work_packet(null_consumed_review_packet), "null review-packet consumed map returns structured errors")
    split_manifest_review_packet = copy.deepcopy(review_packet)
    split_manifest_review_packet["consumed"]["manifest"]["path"] = "manifests/alternate-manifest-v1.json"
    split_manifest_review_packet["input_paths"].append("manifests/alternate-manifest-v1.json")
    split_manifest_review_packet["input_hashes"]["manifests/alternate-manifest-v1.json"] = "0" * 64
    split_manifest_review_packet["input_paths"].remove("manifests/manifest-v1.json")
    split_manifest_review_packet["input_hashes"].pop("manifests/manifest-v1.json")
    expect(any("same path and SHA-256" in item for item in validate_review_work_packet(split_manifest_review_packet)), "consumed Manifest cannot diverge from same-revision lineage entry")
    null_fidelity_packet = copy.deepcopy(blender_packet)
    null_fidelity_packet["geometry_fidelity"] = None
    expect(validate_packet_against_manifest_review(null_fidelity_packet, manifest, review, cad_result), "null Blender fidelity returns structured errors")

    exact = copy.deepcopy(blender_packet)
    exact["geometry_fidelity"]["required_checks"] = ["controlled dimensions"]
    expect(any("topology" in item for item in validate_work_packet(exact)), "exact_mesh requires mesh/topology evidence")
    proxy = copy.deepcopy(blender_packet)
    proxy["geometry_fidelity"].update({"level": "envelope_proxy", "intended_use": "Final presentation face-accurate model"})
    expect(any("overclaims" in item for item in validate_work_packet(proxy)), "envelope proxy overclaim rejected")

    expect(not validate_review(review), "valid review report")
    human_issue = copy.deepcopy(review)
    human_issue["issues"] = [{
        "issue_id": "I-1", "severity": "Needs Human Decision", "category": "input",
        "rule": "authoritative choice required", "object_ids": [], "finding": "unknown finish",
        "evidence": ["request"], "impact": "delivery choice", "recommendation": "ask user",
        "confidence": 1.0, "disposition": "open",
    }]
    human_issue["outcome"] = "CONDITIONAL_PASS"
    human_issue["conditions"] = ["ask later"]
    expect(any("NEEDS_USER_DECISION" in item for item in validate_review(human_issue)), "Needs Human Decision cannot conditionally pass")
    failed_fidelity = copy.deepcopy(review)
    failed_fidelity.update({
        "review_stage": "blender",
        "review_profiles": ["common_acceptance", "blender_model_review", "mechanical_review"],
        "geometry_fidelity_assessment": {"declared": copy.deepcopy(blender_packet["geometry_fidelity"]), "outcome": "FAIL", "evidence": ["blender/exports/blender-snapshot.json"]},
    })
    expect(any("requires FAIL" in item for item in validate_review(failed_fidelity)), "failed fidelity assessment cannot produce a passing Blender review")

    empty_result = template("stage-result.json")
    empty_result["outputs"] = []
    empty_result["readback_evidence"] = []
    expect(len(validate_stage_result(empty_result)) == 2, "empty completed stage evidence rejected")
    empty_delivery = template("delivery-report.json")
    empty_delivery["deliverables"] = []
    expect(any("deliverables" in item for item in validate_delivery_report(empty_delivery)), "empty delivery rejected")
    null_revisions_delivery = template("delivery-report.json")
    null_revisions_delivery["accepted_revisions"] = None
    expect(validate_delivery_report(null_revisions_delivery), "null accepted revisions return structured errors")
    null_lineage_delivery = template("delivery-report.json")
    null_lineage_delivery["manifest_lineage"] = None
    expect(validate_delivery_report(null_lineage_delivery), "null delivery Manifest lineage returns structured errors")
    bad_state = template("project-state.json")
    bad_state["writer_locks"] = [
        {"path": "cad/model.FCStd", "owner": "a", "packet_id": "P1", "acquired_at": "now", "status": "active"},
        {"path": "cad/model.FCStd", "owner": "b", "packet_id": "P2", "acquired_at": "now", "status": "active"},
    ]
    expect(any("overlapping writer lock" in item for item in validate_project_state(bad_state)), "overlapping writer lock rejected")
    jumped_state = template("project-state.json")
    jumped_state.update({
        "current_state": "cad_accepted", "next_allowed_states": ["blender_in_progress"],
        "last_transition": {"from": "raw_request", "to": "cad_accepted", "actor": "orchestrator", "evidence": ["fake"], "revisions": {"manifest": 1}},
    })
    expect(any("allowed state transition" in item for item in validate_project_state(jumped_state)), "illegal state jump rejected")
    blocked_result = template("stage-result.json")
    blocked_result["unresolved_items"] = [{"value": "missing", "source": "review", "status": "blocked", "confidence": 1.0, "reason": "required", "blocks": ["cad_review"]}]
    expect(any("contains blocker" in item for item in validate_stage_result(blocked_result)), "completed result with blocker rejected")

    attempt = template("attempt-record.json")
    receipt = template("completion-receipt.json")
    expect(not validate_attempt_record(attempt), "valid promoted attempt record")
    expect(not validate_completion_receipt(receipt), "valid independent completion receipt")
    failed_promotion = copy.deepcopy(attempt)
    failed_promotion.update({"exit_code": 2, "failure_classification": "execution"})
    expect(any("promoted attempt" in item for item in validate_attempt_record(failed_promotion)), "failed attempt cannot be promoted")
    same_process = copy.deepcopy(receipt)
    same_process["readback_process"] = copy.deepcopy(same_process["writer_process"])
    expect(any("independent process" in item for item in validate_completion_receipt(same_process)), "same-process read-back rejected")
    reused_pid = copy.deepcopy(receipt)
    reused_pid["readback_process"]["process_id"] = reused_pid["writer_process"]["process_id"]
    expect(any("distinct positive process IDs" in item for item in validate_completion_receipt(reused_pid)), "read-back cannot reuse the writer PID")
    boolean_pid = copy.deepcopy(receipt)
    boolean_pid["readback_process"]["process_id"] = True
    expect(any("positive integer" in item for item in validate_completion_receipt(boolean_pid)), "boolean PID rejected")
    boolean_exit = copy.deepcopy(attempt)
    boolean_exit["exit_code"] = False
    expect(any("exit_code" in item for item in validate_attempt_record(boolean_exit)), "boolean exit code rejected")
    boolean_revision = copy.deepcopy(manifest)
    boolean_revision["revision"] = True
    expect(any("revision" in item for item in validate_manifest(boolean_revision)), "boolean Manifest revision rejected")
    boolean_baseline_ref = copy.deepcopy(manifest)
    boolean_baseline_ref["requirements_baseline"]["revision"] = True
    expect(any("requirements_baseline.revision" in item for item in validate_manifest(boolean_baseline_ref)), "boolean baseline reference revision rejected")
    boolean_packet = copy.deepcopy(packet)
    boolean_packet["baseline_revision"] = boolean_packet["source_manifest_revision"] = True
    packet_boolean_errors = validate_work_packet(boolean_packet)
    expect(any("baseline_revision" in item for item in packet_boolean_errors) and any("source_manifest_revision" in item for item in packet_boolean_errors), "boolean work-packet revisions rejected")
    boolean_stage_result = copy.deepcopy(empty_result)
    boolean_stage_result["manifest_revision_consumed"] = True
    expect(any("manifest_revision_consumed" in item for item in validate_stage_result(boolean_stage_result)), "boolean consumed Manifest revision rejected")
    boolean_state = template("project-state.json")
    boolean_state["active_revisions"] = {"manifest": True}
    expect(any("active_revisions" in item for item in validate_project_state(boolean_state)), "boolean active project revision rejected")
    malformed_attempt = copy.deepcopy(attempt)
    malformed_attempt["expected_outputs"] = None
    expect(validate_attempt_record(malformed_attempt), "null expected_outputs returns structured errors")
    impossible_attempt_time = copy.deepcopy(attempt)
    impossible_attempt_time["started_at"] = "2026-99-99T99:99:99Z"
    expect(any("started_at" in item for item in validate_attempt_record(impossible_attempt_time)), "calendar-invalid attempt timestamp rejected")
    impossible_receipt_time = copy.deepcopy(receipt)
    impossible_receipt_time["completed_at"] = "2026-02-30T00:00:00Z"
    expect(any("completed_at" in item for item in validate_completion_receipt(impossible_receipt_time)), "calendar-invalid receipt timestamp rejected")
    render_profile = template("render-qa-profile.json")
    expect(not validate_render_qa_profile(render_profile), "valid configurable render QA profile")
    perspective_plan = copy.deepcopy(render_profile)
    perspective_plan["required_views"]["plan_view_projection"] = "perspective"
    expect(any("orthographic" in item for item in validate_render_qa_profile(perspective_plan)), "perspective plan camera rejected")
    forged_fidelity = template("geometry-fidelity-result.json")
    forged_fidelity["measurement_results"][0].update({"cad_value": 100, "blender_value": 999, "deviation_mm": 0, "passed": True})
    expect(bool(validate_geometry_fidelity_result(forged_fidelity)), "forged exact_mesh measurement PASS rejected")
    forged_topology = template("geometry-fidelity-result.json")
    forged_topology["topology_evidence"]["blender_metrics"]["face_count"] += 1
    expect(any("recomputed structured topology" in item for item in validate_geometry_fidelity_result(forged_topology)), "self-reported topology_match rejected")
    forged_render = template("render-qa-result.json")
    forged_render["deterministic_checks"] = [{"check": "arbitrary", "passed": True}]
    forged_render["manual_checks"] = [{"check_id": "label_readability", "reviewer": "reviewer", "outcome": "PASS", "evidence": ["does-not-exist"]}]
    expect(bool(validate_render_qa_result(forged_render)), "arbitrary Render QA and unhashed manual evidence rejected")
    forged_process_log = template("process-log.json")
    forged_process_log["events"] = [{"at": "2026-01-01T00:00:01Z", "event": "anything"}]
    expect(any("exactly follow" in item for item in validate_process_log(forged_process_log)), "arbitrary structured process events rejected")
    with tempfile.TemporaryDirectory(prefix="fake-png-test-") as png_directory:
        fake_png = Path(png_directory) / "fake.png"
        fake_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (1920).to_bytes(4, "big") + (1080).to_bytes(4, "big"))
        expect(_png_dimensions(fake_png) is None, "truncated 24-byte fake PNG rejected")
        def png_chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        palette_without_plte = Path(png_directory) / "palette-without-plte.png"
        palette_ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0)
        palette_without_plte.write_bytes(b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", palette_ihdr) + png_chunk(b"IDAT", zlib.compress(b"\x00\x00")) + png_chunk(b"IEND", b""))
        expect(_png_dimensions(palette_without_plte) is None, "indexed PNG without mandatory PLTE rejected")
        rgb_ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        invalid_chunk_type = Path(png_directory) / "invalid-chunk-type.png"
        invalid_chunk_type.write_bytes(b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", rgb_ihdr) + png_chunk(b"a1CD", b"") + png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + png_chunk(b"IEND", b""))
        expect(_png_dimensions(invalid_chunk_type) is None, "PNG chunk types must be four ASCII letters with a valid reserved bit")
        trailing_zlib = Path(png_directory) / "trailing-zlib.png"
        trailing_zlib.write_bytes(b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", rgb_ihdr) + png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00") + b"TRAILING") + png_chunk(b"IEND", b""))
        expect(_png_dimensions(trailing_zlib) is None, "PNG IDAT rejects bytes trailing the zlib stream")
    ledger = template("hash-ledger.json")
    expect(not validate_hash_ledger(ledger), "valid non-self-referential hash ledger")
    self_hashing = copy.deepcopy(ledger)
    self_hashing["entries"][self_hashing["self_path"]] = "0" * 64
    expect(any("own" in item for item in validate_hash_ledger(self_hashing)), "self-referential hash ledger rejected")

    with tempfile.TemporaryDirectory(prefix="cad-blender-skill-test-") as directory:
        root = Path(directory)
        target = create_project(root, "test-project", TEMPLATES)
        expect(target.exists() and (target / "project-state.json").exists() and (target / "render").is_dir(), "isolated scaffold created")
        expect(load_json(target / "delivery" / "delivery-checklist.json")["project_id"] == "test-project", "scaffolded checklist identity updated")
        try:
            create_project(root, "test-project", TEMPLATES)
        except FileExistsError:
            print("PASS existing project overwrite refused")
        else:
            raise AssertionError("existing project overwrite was not refused")
        try:
            create_project(root, "../escape", TEMPLATES)
        except ValueError:
            print("PASS path traversal rejected")
        else:
            raise AssertionError("path traversal was not rejected")
        for zip_name, entries, label in (
            ("zip-traversal.zip", [("../escape.txt", b"x")], "ZIP traversal rejected"),
            ("zip-collision.zip", [("CAD/model.FCStd", b"a"), ("cad/MODEL.fcstd", b"b")], "ZIP Windows path collision rejected"),
        ):
            archive_path = root / zip_name
            with zipfile.ZipFile(archive_path, "w") as archive:
                for name, payload in entries:
                    archive.writestr(name, payload)
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    _safe_extract(archive, root / f"extract-{zip_name}")
            except ValueError:
                print(f"PASS {label}")
            else:
                raise AssertionError(label)
        special_zip = root / "zip-special-file.zip"
        special_info = zipfile.ZipInfo("link")
        special_info.create_system = 3
        special_info.external_attr = 0o120777 << 16
        with zipfile.ZipFile(special_zip, "w") as archive:
            archive.writestr(special_info, b"target")
        try:
            with zipfile.ZipFile(special_zip) as archive:
                _safe_extract(archive, root / "extract-special")
        except ValueError:
            print("PASS ZIP special file rejected")
        else:
            raise AssertionError("ZIP special file was not rejected")

        role_manifest = copy.deepcopy(manifest)
        role_manifest["project_id"] = "test-project"
        role_manifest["requirements_baseline"]["project_id"] = "test-project"
        role_artifacts = {
            "manifests/manifest-v1.json": role_manifest,
            "cad/stage-result-v1.json": copy.deepcopy(cad_result),
            "blender/stage-result-v1.json": copy.deepcopy(cad_result),
            "reviews/cad-review-v1.json": copy.deepcopy(review),
            "reviews/blender-review-v1.json": copy.deepcopy(review),
        }
        for key in ("cad/stage-result-v1.json", "blender/stage-result-v1.json"):
            for field in ("evidence_contract_version", "promoted_attempt", "completion_receipt"):
                role_artifacts[key].pop(field, None)
            role_artifacts[key]["project_id"] = "test-project"
        role_artifacts["blender/stage-result-v1.json"].update({"stage": "blender", "packet_id": "BLENDER-WP-001", "outputs": ["blender/example.blend"], "readback_evidence": ["blender/exports/blender-snapshot.json"]})
        role_artifacts["reviews/cad-review-v1.json"].update({"project_id": "test-project", "review_id": "REV-CAD", "reviewed_artifacts": ["cad/stage-result-v1.json"]})
        role_artifacts["reviews/blender-review-v1.json"].update({
            "project_id": "test-project", "review_id": "REV-BLENDER", "review_stage": "blender",
            "reviewed_artifacts": ["blender/stage-result-v1.json"],
            "review_profiles": ["common_acceptance", "blender_model_review", "mechanical_review"],
            "geometry_fidelity_assessment": {
                "declared": copy.deepcopy(blender_packet["geometry_fidelity"]),
                "outcome": "PASS",
                "evidence": ["blender/exports/blender-snapshot.json"],
                "result": {"path": "blender/qa/geometry-fidelity-result-v1.json", "revision": 1, "sha256": "0" * 64},
            },
        })
        role_paths: dict[str, Path] = {}
        for relative, payload in role_artifacts.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            role_paths[relative] = path
        role_packet = copy.deepcopy(cross_review_packet)
        role_packet["project_id"] = "test-project"
        role_packet["manifest_lineage"] = [{"revision": 1, "parent_revision": None, "path": "manifests/manifest-v1.json", "sha256": hashlib.sha256(role_paths["manifests/manifest-v1.json"].read_bytes()).hexdigest()}]
        for name, relative in {
            "manifest": "manifests/manifest-v1.json", "cad_result": "cad/stage-result-v1.json",
            "blender_result": "blender/stage-result-v1.json", "cad_review": "reviews/cad-review-v1.json",
            "blender_review": "reviews/blender-review-v1.json",
        }.items():
            role_packet["consumed"][name] = {"path": relative, "revision": 1, "sha256": hashlib.sha256(role_paths[relative].read_bytes()).hexdigest()}
        role_packet["input_paths"] = [ref["path"] for ref in role_packet["consumed"].values()]
        role_packet["input_hashes"] = {ref["path"]: ref["sha256"] for ref in role_packet["consumed"].values()}
        role_packet_errors = validate_review_packet_files(role_packet, target)
        expect(not role_packet_errors, f"cross-software review packet binds correctly typed CAD/Blender results and reviews: {role_packet_errors}")
        drift_manifest = copy.deepcopy(role_manifest)
        drift_manifest["objects"][0]["critical_dimensions"]["width"] = 999.0
        drift_cad, drift_blender = copy.deepcopy(template("cad-snapshot.json")), copy.deepcopy(template("blender-snapshot.json"))
        for snapshot in (drift_cad, drift_blender):
            snapshot["project_id"] = "test-project"
            snapshot["manifest_lineage"] = copy.deepcopy(role_packet["manifest_lineage"])
            snapshot["objects"][0]["critical_dimensions"]["width"] = 999.0
        bound_compare = compare(drift_cad, drift_blender, drift_manifest, target, role_packet["manifest_lineage"], role_paths["manifests/manifest-v1.json"])
        expect(any(item["category"] == "accepted_manifest_content_mismatch" for item in bound_compare["differences"]), "comparison binds accepted Manifest object to final canonical lineage file")
        swapped_results = copy.deepcopy(role_packet)
        swapped_results["consumed"]["cad_result"], swapped_results["consumed"]["blender_result"] = swapped_results["consumed"]["blender_result"], swapped_results["consumed"]["cad_result"]
        expect(any("must be a cad Stage Result" in item or "must be a blender Stage Result" in item for item in validate_review_packet_files(swapped_results, target)), "swapped CAD/Blender Stage Result roles rejected")
        swapped_reviews = copy.deepcopy(role_packet)
        swapped_reviews["consumed"]["cad_review"], swapped_reviews["consumed"]["blender_review"] = swapped_reviews["consumed"]["blender_review"], swapped_reviews["consumed"]["cad_review"]
        expect(any("must be a cad review" in item or "must be a blender review" in item for item in validate_review_packet_files(swapped_reviews, target)), "swapped CAD/Blender review roles rejected")
        failed_upstream = copy.deepcopy(role_artifacts["reviews/cad-review-v1.json"])
        failed_upstream["outcome"] = "FAIL"
        role_paths["reviews/cad-review-v1.json"].write_text(json.dumps(failed_upstream, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        failed_packet = copy.deepcopy(role_packet)
        failed_digest = hashlib.sha256(role_paths["reviews/cad-review-v1.json"].read_bytes()).hexdigest()
        failed_packet["consumed"]["cad_review"]["sha256"] = failed_digest
        failed_packet["input_hashes"]["reviews/cad-review-v1.json"] = failed_digest
        expect(any("accepted outcome" in item for item in validate_review_packet_files(failed_packet, target)), "failed upstream review cannot feed cross-software review")
        role_paths["reviews/cad-review-v1.json"].write_text(json.dumps(role_artifacts["reviews/cad-review-v1.json"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mismatched_review = copy.deepcopy(role_artifacts["reviews/cad-review-v1.json"])
        mismatched_review["consumed_revisions"]["stage_result"] = 2
        role_paths["reviews/cad-review-v1.json"].write_text(json.dumps(mismatched_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mismatch_packet = copy.deepcopy(role_packet)
        mismatch_digest = hashlib.sha256(role_paths["reviews/cad-review-v1.json"].read_bytes()).hexdigest()
        mismatch_packet["consumed"]["cad_review"]["sha256"] = mismatch_digest
        mismatch_packet["input_hashes"]["reviews/cad-review-v1.json"] = mismatch_digest
        expect(any("revision does not match" in item for item in validate_review_packet_files(mismatch_packet, target)), "review consumed Stage Result revision mismatch rejected")
        mismatched_review["consumed_revisions"]["stage_result"] = 1
        mismatched_review["reviewed_artifacts"] = ["cad/stage-result-v2.json"]
        role_paths["reviews/cad-review-v1.json"].write_text(json.dumps(mismatched_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mismatch_digest = hashlib.sha256(role_paths["reviews/cad-review-v1.json"].read_bytes()).hexdigest()
        mismatch_packet["consumed"]["cad_review"]["sha256"] = mismatch_digest
        mismatch_packet["input_hashes"]["reviews/cad-review-v1.json"] = mismatch_digest
        expect(any("reviewed_artifacts does not include" in item for item in validate_review_packet_files(mismatch_packet, target)), "reviewed Stage Result path mismatch rejected")
        role_paths["reviews/cad-review-v1.json"].write_text(json.dumps(role_artifacts["reviews/cad-review-v1.json"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        null_consumed_file_packet = copy.deepcopy(role_packet)
        null_consumed_file_packet["consumed"] = None
        expect(validate_review_packet_files(null_consumed_file_packet, target), "file-level review validator handles null consumed map without exception")

        evidence_result = template("stage-result.json")
        evidence_result["project_id"] = "test-project"
        for relative in evidence_result["outputs"]:
            output_path = target / relative
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"attempt-output\n")
        evidence_attempt = template("attempt-record.json")
        evidence_receipt = template("completion-receipt.json")
        evidence_packet = template("work-packet.json")
        evidence_packet["project_id"] = "test-project"
        evidence_manifest_digest = hashlib.sha256(role_paths["manifests/manifest-v1.json"].read_bytes()).hexdigest()
        evidence_packet["manifest_sha256"] = evidence_manifest_digest
        evidence_packet["manifest_lineage"][0]["sha256"] = evidence_manifest_digest
        evidence_packet_path = target / "work-packets" / "cad-work-v1.json"
        evidence_packet_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_packet_path.write_text(json.dumps(evidence_packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence_packet_ref = {"path": "work-packets/cad-work-v1.json", "project_id": "test-project", "revision": 1, "packet_id": "CAD-WP-001", "sha256": hashlib.sha256(evidence_packet_path.read_bytes()).hexdigest()}
        evidence_result["work_packet"] = copy.deepcopy(evidence_packet_ref)
        for artifact in (evidence_attempt, evidence_receipt):
            artifact["project_id"] = "test-project"
            artifact["work_packet"] = copy.deepcopy(evidence_packet_ref)
            for output_ref in artifact.get("actual_outputs", artifact.get("outputs", [])):
                output_ref["sha256"] = hashlib.sha256((target / output_ref["path"]).read_bytes()).hexdigest()
        def process_log_bytes(process: dict, kind: str) -> bytes:
            if kind == "writer":
                command = {"argv": [process["executable"], "operator.py"], "cwd": "<PROJECT_ROOT>"}
                times = ["2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", "2026-01-01T00:00:03Z"]
                names = ["process_started", "output_written", "process_exited"]
                artifacts = copy.deepcopy(evidence_attempt["actual_outputs"])
            else:
                command = {"argv": [process["executable"], "readback.py"], "cwd": "<PROJECT_ROOT>"}
                times = ["2026-01-01T00:01:01Z", "2026-01-01T00:01:02Z", "2026-01-01T00:01:03Z"]
                names = ["process_started", "artifact_verified", "process_exited"]
                artifacts = copy.deepcopy(evidence_receipt["outputs"])
            details = [{"process_id": process["process_id"]}, {"artifacts": artifacts}, {"exit_code": 0}]
            payload = {"schema_version": "1.0", "artifact_type": "process_log", "project_id": "test-project", "revision": 1,
                       "stage": "cad", "packet_id": "CAD-WP-001", "attempt_id": "ATTEMPT-001",
                       **{key: process[key] for key in ("process_id", "host", "process_started_at", "executable", "command_sha256")},
                       "process_kind": kind, "command": command, "recorded_at": times[-1],
                       "events": [{"at": at, "event": event, "details": detail} for at, event, detail in zip(times, names, details)]}
            return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        process_logs = {
            evidence_attempt["writer_process"]["log"]["path"]: process_log_bytes(evidence_attempt["writer_process"], "writer"),
            evidence_receipt["readback_process"]["log"]["path"]: process_log_bytes(evidence_receipt["readback_process"], "readback"),
        }
        for relative, content in process_logs.items():
            log_path = target / relative
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_bytes(content)
        writer_log_hash = hashlib.sha256(process_logs[evidence_attempt["writer_process"]["log"]["path"]]).hexdigest()
        readback_log_hash = hashlib.sha256(process_logs[evidence_receipt["readback_process"]["log"]["path"]]).hexdigest()
        evidence_attempt["writer_process"]["log"]["sha256"] = writer_log_hash
        evidence_receipt["writer_process"]["log"]["sha256"] = writer_log_hash
        evidence_receipt["readback_process"]["log"]["sha256"] = readback_log_hash
        for field, artifact in (("promoted_attempt", evidence_attempt), ("completion_receipt", evidence_receipt)):
            evidence_path = target / evidence_result[field]["path"]
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            evidence_result[field]["sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        expect(not validate_stage_evidence(evidence_result, target), "successful attempt and independent receipt promote a Stage Result")
        downgraded_result = copy.deepcopy(evidence_result)
        for field in ("evidence_contract_version", "work_packet", "promoted_attempt", "completion_receipt"):
            downgraded_result.pop(field, None)
        expect(any("formal Stage Result" in item for item in validate_stage_evidence(downgraded_result, target)), "Stage Result cannot delete evidence_contract_version to downgrade formal validation")
        writer_log_path = target / evidence_attempt["writer_process"]["log"]["path"]
        authentic_writer_log = writer_log_path.read_bytes()
        foreign_log = load_json(writer_log_path)
        foreign_log["attempt_id"] = "OTHER-ATTEMPT"
        writer_log_path.write_text(json.dumps(foreign_log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        foreign_hash = hashlib.sha256(writer_log_path.read_bytes()).hexdigest()
        foreign_attempt, foreign_receipt = copy.deepcopy(evidence_attempt), copy.deepcopy(evidence_receipt)
        foreign_attempt["writer_process"]["log"]["sha256"] = foreign_hash
        foreign_receipt["writer_process"]["log"]["sha256"] = foreign_hash
        attempt_path = target / evidence_result["promoted_attempt"]["path"]
        receipt_path = target / evidence_result["completion_receipt"]["path"]
        attempt_path.write_text(json.dumps(foreign_attempt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt_path.write_text(json.dumps(foreign_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        foreign_result = copy.deepcopy(evidence_result)
        foreign_result["promoted_attempt"]["sha256"] = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
        foreign_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        expect(any("attempt_id does not match" in item for item in validate_stage_evidence(foreign_result, target)), "process logs cannot be replayed from another attempt")
        writer_log_path.write_bytes(authentic_writer_log)
        attempt_path.write_text(json.dumps(evidence_attempt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt_path.write_text(json.dumps(evidence_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence_result["promoted_attempt"]["sha256"] = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
        evidence_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        late_writer_log = load_json(writer_log_path)
        late_writer_log["events"][-1]["at"] = "2026-01-01T00:01:01Z"
        late_writer_log["recorded_at"] = "2026-01-01T00:01:01Z"
        writer_log_path.write_text(json.dumps(late_writer_log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        late_hash = hashlib.sha256(writer_log_path.read_bytes()).hexdigest()
        late_attempt, late_receipt = copy.deepcopy(evidence_attempt), copy.deepcopy(evidence_receipt)
        late_attempt["writer_process"]["log"]["sha256"] = late_hash
        late_receipt["writer_process"]["log"]["sha256"] = late_hash
        attempt_path.write_text(json.dumps(late_attempt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt_path.write_text(json.dumps(late_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        late_result = copy.deepcopy(evidence_result)
        late_result["promoted_attempt"]["sha256"] = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
        late_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        expect(any("after its attempt/receipt ended" in item for item in validate_stage_evidence(late_result, target)), "writer log cannot claim events after writer attempt ended")
        late_writer_log["events"][-1]["at"] = "2026-01-01T00:00:03"
        late_writer_log["recorded_at"] = "2026-01-01T00:00:03Z"
        writer_log_path.write_text(json.dumps(late_writer_log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        malformed_hash = hashlib.sha256(writer_log_path.read_bytes()).hexdigest()
        malformed_attempt, malformed_receipt = copy.deepcopy(evidence_attempt), copy.deepcopy(evidence_receipt)
        malformed_attempt["writer_process"]["log"]["sha256"] = malformed_hash
        malformed_receipt["writer_process"]["log"]["sha256"] = malformed_hash
        attempt_path.write_text(json.dumps(malformed_attempt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt_path.write_text(json.dumps(malformed_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        malformed_result = copy.deepcopy(evidence_result)
        malformed_result["promoted_attempt"]["sha256"] = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
        malformed_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        expect(any("at must be a real ISO-8601 UTC timestamp" in item for item in validate_stage_evidence(malformed_result, target)), "malformed process event timestamp returns structured errors")
        readback_log_path = target / evidence_receipt["readback_process"]["log"]["path"]
        authentic_readback_log = readback_log_path.read_bytes()
        late_readback_log = load_json(readback_log_path)
        late_readback_log["events"][-1]["at"] = "2026-01-01T00:01:04Z"
        late_readback_log["recorded_at"] = "2026-01-01T00:01:04Z"
        readback_log_path.write_text(json.dumps(late_readback_log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        late_readback_receipt = copy.deepcopy(evidence_receipt)
        late_readback_receipt["readback_process"]["log"]["sha256"] = hashlib.sha256(readback_log_path.read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(late_readback_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        late_readback_result = copy.deepcopy(evidence_result)
        late_readback_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        expect(any("readback_process log event occurs after its attempt/receipt ended" in item for item in validate_stage_evidence(late_readback_result, target)), "readback log cannot claim events after receipt completion")
        readback_log_path.write_bytes(authentic_readback_log)
        writer_log_path.write_bytes(authentic_writer_log)
        attempt_path.write_text(json.dumps(evidence_attempt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt_path.write_text(json.dumps(evidence_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence_result["promoted_attempt"]["sha256"] = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
        evidence_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        wrong_packet_receipt = copy.deepcopy(evidence_receipt)
        wrong_packet_receipt["work_packet"]["sha256"] = "f" * 64
        receipt_path = target / evidence_result["completion_receipt"]["path"]
        receipt_path.write_text(json.dumps(wrong_packet_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        wrong_packet_result = copy.deepcopy(evidence_result)
        wrong_packet_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        expect(any("work_packet reference" in item for item in validate_stage_evidence(wrong_packet_result, target)), "Attempt/Receipt/Stage Result cannot cite different Work Packets")
        receipt_path.write_text(json.dumps(evidence_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        rogue_result = copy.deepcopy(evidence_result)
        rogue_result["outputs"].append("manifests/manifest-v999.json")
        expect(any("exceed Work Packet" in item for item in validate_stage_evidence(rogue_result, target)), "Stage Result cannot self-authorize rogue Manifest output")
        rogue_attempt = copy.deepcopy(evidence_attempt)
        rogue_attempt["expected_outputs"].append("cad/rogue.step")
        attempt_path = target / evidence_result["promoted_attempt"]["path"]
        attempt_path.write_text(json.dumps(rogue_attempt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rogue_attempt_result = copy.deepcopy(evidence_result)
        rogue_attempt_result["promoted_attempt"]["sha256"] = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
        expect(any("authorization" in item for item in validate_stage_evidence(rogue_attempt_result, target)), "Attempt authorization must exactly match Work Packet outputs")
        attempt_path.write_text(json.dumps(evidence_attempt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence_result["promoted_attempt"]["sha256"] = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
        writer_log_path = target / evidence_attempt["writer_process"]["log"]["path"]
        authentic_writer_log = writer_log_path.read_bytes()
        writer_log_path.write_text("plain text log\n", encoding="utf-8")
        plaintext_attempt = copy.deepcopy(evidence_attempt)
        plaintext_receipt = copy.deepcopy(evidence_receipt)
        plaintext_hash = hashlib.sha256(writer_log_path.read_bytes()).hexdigest()
        plaintext_attempt["writer_process"]["log"]["sha256"] = plaintext_hash
        plaintext_receipt["writer_process"]["log"]["sha256"] = plaintext_hash
        attempt_path.write_text(json.dumps(plaintext_attempt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt_path.write_text(json.dumps(plaintext_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        plaintext_result = copy.deepcopy(evidence_result)
        plaintext_result["promoted_attempt"]["sha256"] = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
        plaintext_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        expect(any("process_log JSON" in item for item in validate_stage_evidence(plaintext_result, target)), "plain-text process log cannot claim verified assurance")
        writer_log_path.write_bytes(authentic_writer_log)
        attempt_path.write_text(json.dumps(evidence_attempt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt_path.write_text(json.dumps(evidence_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        shared_log_receipt = copy.deepcopy(evidence_receipt)
        shared_log_receipt["readback_process"]["log"] = copy.deepcopy(shared_log_receipt["writer_process"]["log"])
        receipt_path = target / evidence_result["completion_receipt"]["path"]
        receipt_path.write_text(json.dumps(shared_log_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        shared_log_result = copy.deepcopy(evidence_result)
        shared_log_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        expect(any("distinct independent log" in item for item in validate_stage_evidence(shared_log_result, target)), "writer/readback cannot share one process log")
        receipt_path.write_text(json.dumps(evidence_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mismatched_receipt = copy.deepcopy(evidence_receipt)
        mismatched_receipt["application"] = "Blender"
        receipt_path = target / evidence_result["completion_receipt"]["path"]
        receipt_path.write_text(json.dumps(mismatched_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mismatch_result = copy.deepcopy(evidence_result)
        mismatch_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        expect(any("application does not match" in item for item in validate_stage_evidence(mismatch_result, target)), "attempt and receipt application mismatch rejected")
        mismatched_receipt = copy.deepcopy(evidence_receipt)
        mismatched_receipt["interface_type"] = "mcp"
        receipt_path.write_text(json.dumps(mismatched_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mismatch_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        expect(any("interface_type does not match" in item for item in validate_stage_evidence(mismatch_result, target)), "attempt and receipt interface type mismatch rejected")
        reversed_attempt = copy.deepcopy(evidence_attempt)
        reversed_attempt["ended_at"] = "2025-12-31T23:59:59Z"
        attempt_path = target / evidence_result["promoted_attempt"]["path"]
        attempt_path.write_text(json.dumps(reversed_attempt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt_path.write_text(json.dumps(evidence_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        reversed_result = copy.deepcopy(evidence_result)
        reversed_result["promoted_attempt"]["sha256"] = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
        reversed_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        expect(any("timestamps" in item for item in validate_stage_evidence(reversed_result, target)), "reversed attempt/receipt timestamps rejected")
        attempt_path.write_text(json.dumps(evidence_attempt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence_result["promoted_attempt"]["sha256"] = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
        evidence_result["completion_receipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        forged_receipt = copy.deepcopy(evidence_result)
        forged_receipt["completion_receipt"]["sha256"] = "0" * 64
        expect(any("SHA-256 mismatch" in item for item in validate_stage_evidence(forged_receipt, target)), "forged completion receipt hash rejected")
        unbound_readback = copy.deepcopy(evidence_result)
        rogue_readback = target / "cad" / "exports" / "rogue-readback.json"
        rogue_readback.write_text("{}\n", encoding="utf-8")
        unbound_readback["readback_evidence"].append("cad/exports/rogue-readback.json")
        expect(any("output/read-back" in item for item in validate_stage_evidence(unbound_readback, target)), "unbound read-back evidence cannot be promoted")

        base_path = root / "manifest-v1.json"
        base_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (root / "cad" / "exports").mkdir(parents=True)
        (root / "cad" / "exports" / "cad-snapshot.json").write_text("{}\n", encoding="utf-8")
        (root / "cad" / "stage-result-v1.json").write_text("{}\n", encoding="utf-8")
        patch = template("manifest-patch.json")
        patch["base_sha256"] = hashlib.sha256(base_path.read_bytes()).hexdigest()
        result = apply_patch(manifest, patch, base_path, baseline, baseline_path, root)
        expect(result["revision"] == 2 and result["stage_status"] == "cad_review", "hash-bound Manifest Patch applied to new revision")
        protected = copy.deepcopy(patch)
        protected["operations"] = [{"operation_id": "OP-X", "op": "replace", "path": "/project_id", "value": "other-project", "evidence": "cad/exports/cad-snapshot.json"}]
        try:
            apply_patch(manifest, protected, base_path, baseline, baseline_path, root)
        except ValueError:
            print("PASS protected Manifest identity cannot be patched")
        else:
            raise AssertionError("protected Manifest identity was patchable")

        delivery = template("delivery-report.json")
        delivery["project_id"] = "test-project"
        delivery["delivery_contract_version"] = "2.0"
        delivery["accepted_revisions"] = {"requirements": 1, "manifest": 7, "cad_result": 1, "blender_result": 1}
        delivery["manifest_lineage"] = [
            {"revision": revision, "parent_revision": revision - 1 if revision > 1 else None, "path": f"manifests/manifest-v{revision}.json", "sha256": "0" * 64}
            for revision in range(1, 8)
        ]
        for entry in delivery["manifest_lineage"]:
            lineage_manifest = copy.deepcopy(manifest)
            lineage_manifest["project_id"] = "test-project"
            lineage_manifest["requirements_baseline"]["project_id"] = "test-project"
            lineage_manifest["revision"] = entry["revision"]
            lineage_manifest["parent_manifest"] = None if entry["revision"] == 1 else {
                "revision": entry["revision"] - 1,
                "path": f"manifests/manifest-v{entry['revision'] - 1}.json",
                "sha256": delivery["manifest_lineage"][entry["revision"] - 2]["sha256"],
            }
            lineage_path = target / entry["path"]
            lineage_path.parent.mkdir(parents=True, exist_ok=True)
            lineage_path.write_text(json.dumps(lineage_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            entry["sha256"] = hashlib.sha256(lineage_path.read_bytes()).hexdigest()
            delivery["artifact_hashes"][entry["path"]] = entry["sha256"]
        wrong_lineage_hash = copy.deepcopy(delivery["manifest_lineage"])
        wrong_lineage_hash[0]["sha256"] = "0" * 64
        expect(any("SHA-256 mismatch" in item for item in validate_manifest_lineage(wrong_lineage_hash, 7, target, "test-project")[0]), "Manifest lineage wrong hash rejected")
        forged_parent_path = target / "manifests" / "manifest-v3.json"
        authentic_parent_bytes = forged_parent_path.read_bytes()
        forged_parent_manifest = load_json(forged_parent_path)
        forged_parent_manifest["parent_manifest"]["sha256"] = "0" * 64
        forged_parent_path.write_text(json.dumps(forged_parent_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        forged_lineage = copy.deepcopy(delivery["manifest_lineage"])
        forged_lineage[2]["sha256"] = hashlib.sha256(forged_parent_path.read_bytes()).hexdigest()
        expect(any("does not authenticate" in item for item in validate_manifest_lineage(forged_lineage, 7, target, "test-project", require_authenticated=True)[0]), "forged parent_manifest ancestor rejected")
        forged_parent_path.write_bytes(authentic_parent_bytes)
        escaped_lineage = copy.deepcopy(delivery["manifest_lineage"])
        escaped_lineage[0]["path"] = "../manifest-v1.json"
        expect(any("project root" in item for item in validate_manifest_lineage(escaped_lineage, 7)[0]), "Manifest lineage path escape rejected")
        for relative in delivery["deliverables"] + delivery["readback_evidence"]:
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"verified-artifact\n")
        cad_delivery_result = copy.deepcopy(cad_result)
        for field in ("evidence_contract_version", "promoted_attempt", "completion_receipt", "work_packet"):
            cad_delivery_result.pop(field, None)
        cad_delivery_result.update({"project_id": "test-project", "manifest_revision_consumed": 2})
        blender_delivery_result = copy.deepcopy(cad_result)
        for field in ("evidence_contract_version", "promoted_attempt", "completion_receipt", "work_packet"):
            blender_delivery_result.pop(field, None)
        blender_delivery_result.update({
            "project_id": "test-project", "stage": "blender", "packet_id": "BLENDER-WP-001",
            "manifest_revision_consumed": 4,
            "outputs": ["blender/example.blend", "blender/exports/blender-snapshot.json"],
            "readback_evidence": ["blender/exports/blender-snapshot.json"],
        })
        for stage, stage_data in (("cad", cad_delivery_result), ("blender", blender_delivery_result)):
            path = target / delivery["stage_results"][stage]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(stage_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        review_payloads = []
        for stage, relative in zip(("cad", "blender", "cross_software"), delivery["review_outcomes"]):
            delivery_review = copy.deepcopy(review)
            delivery_review.update({"project_id": "test-project", "review_id": f"REV-{stage}", "review_stage": stage})
            delivery_review["review_profiles"] = {
                "cad": ["common_acceptance", "cad_drawing_review", "mechanical_review"],
                "blender": ["common_acceptance", "blender_model_review", "mechanical_review"],
                "cross_software": ["common_acceptance", "cross_software_consistency", "mechanical_review"],
            }[stage]
            if stage in {"cad", "blender"}:
                delivery_review["consumed_revisions"] = {"manifest": 2 if stage == "cad" else 4, "stage_result": 1}
                delivery_review["reviewed_artifacts"] = [delivery["stage_results"][stage]]
                if stage == "blender":
                    delivery_review["geometry_fidelity_assessment"] = {
                        "declared": copy.deepcopy(delivery["geometry_fidelity"]),
                        "outcome": "PASS",
                        "evidence": ["blender/exports/blender-snapshot.json"],
                        "result": copy.deepcopy(delivery["geometry_fidelity_result"]),
                    }
            else:
                delivery_review["consumed_revisions"] = {"manifest": 6, "cad_result": 1, "blender_result": 1}
                delivery_review["reviewed_artifacts"] = list(delivery["stage_results"].values())
            review_payloads.append((relative, delivery_review))
        for relative, delivery_review in review_payloads:
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(delivery_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ready_state = template("project-state.json")
        ready_state.update({
            "project_id": "test-project", "revision": 2, "current_state": "ready_for_delivery",
            "active_revisions": copy.deepcopy(delivery["accepted_revisions"]),
            "last_transition": {"from": "cross_software_review", "to": "ready_for_delivery", "actor": "orchestrator", "evidence": list(delivery["review_outcomes"]), "revisions": copy.deepcopy(delivery["accepted_revisions"])},
            "next_allowed_states": ["delivered"],
        })
        (target / delivery["project_state_path"]).write_text(json.dumps(ready_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        project_render_profile = template("render-qa-profile.json")
        project_render_profile["project_id"] = "test-project"
        render_path = target / delivery["render_qa_profile"]["path"]
        render_path.parent.mkdir(parents=True, exist_ok=True)
        render_path.write_text(json.dumps(project_render_profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery["render_qa_profile"]["sha256"] = hashlib.sha256(render_path.read_bytes()).hexdigest()
        delivery["artifact_hashes"][delivery["render_qa_profile"]["path"]] = delivery["render_qa_profile"]["sha256"]
        geometry_result = template("geometry-fidelity-result.json")
        geometry_result.update({"project_id": "test-project", "manifest_revision": 4, "declared": copy.deepcopy(delivery["geometry_fidelity"])})
        consumed_geometry_manifest = load_json(target / "manifests" / "manifest-v4.json")
        snapshot_objects = [
            {
                "object_id": obj["object_id"], "controlled": True, "parent_id": obj.get("parent_id"),
                "critical_dimensions": copy.deepcopy(obj.get("critical_dimensions", {})),
                "transform": {"location": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]},
            }
            for obj in consumed_geometry_manifest["objects"]
        ]
        for side, template_name, ref_name in (("cad", "cad-snapshot.json", "cad_snapshot"), ("blender", "blender-snapshot.json", "blender_snapshot")):
            snapshot = template(template_name)
            snapshot.update({"project_id": "test-project", "manifest_revision": 4, "manifest_lineage": copy.deepcopy(delivery["manifest_lineage"][:4]), "objects": copy.deepcopy(snapshot_objects)})
            snapshot_path = target / geometry_result["measurement_evidence"][ref_name]["path"]
            snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            geometry_result["measurement_evidence"][ref_name]["sha256"] = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
        geometry_result["measurement_results"] = []
        for obj in consumed_geometry_manifest["objects"]:
            for key, value in obj["critical_dimensions"].items():
                geometry_result["measurement_results"].append({"object_id": obj["object_id"], "property": f"critical_dimensions.{key}", "cad_value": value, "blender_value": copy.deepcopy(value), "deviation_mm": 0.0, "passed": True})
            identity_transform = {"location": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}
            geometry_result["measurement_results"].append({"object_id": obj["object_id"], "property": "transform", "cad_value": identity_transform, "blender_value": copy.deepcopy(identity_transform), "deviation_mm": 0.0, "passed": True})
            geometry_result["measurement_results"].append({"object_id": obj["object_id"], "property": "parent_id", "cad_value": obj.get("parent_id"), "blender_value": obj.get("parent_id"), "deviation_mm": 0.0, "passed": True})
        base_mesh = template("canonical-mesh.json")
        mesh_prototype = copy.deepcopy(base_mesh["objects"][0])
        base_mesh.update({
            "project_id": "test-project",
            "objects": [dict(copy.deepcopy(mesh_prototype), object_id=obj["object_id"]) for obj in consumed_geometry_manifest["objects"]],
        })
        for side, source_path, ref_name, metrics_name in (
            ("cad", "cad/source/example.FCStd", "cad_mesh", "cad_metrics"),
            ("blender", "blender/example.blend", "blender_mesh", "blender_metrics"),
        ):
            mesh = copy.deepcopy(base_mesh)
            mesh["source"] = {"path": source_path, "sha256": hashlib.sha256((target / source_path).read_bytes()).hexdigest()}
            mesh_path = target / geometry_result["topology_evidence"][ref_name]["path"]
            mesh_path.parent.mkdir(parents=True, exist_ok=True)
            mesh_path.write_text(json.dumps(mesh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            geometry_result["topology_evidence"][ref_name]["sha256"] = hashlib.sha256(mesh_path.read_bytes()).hexdigest()
            mesh_errors, metrics = canonical_mesh_metrics(mesh)
            expect(not mesh_errors and metrics is not None, f"valid {side} canonical mesh evidence")
            geometry_result["topology_evidence"][metrics_name] = metrics
        geometry_path = target / delivery["geometry_fidelity_result"]["path"]
        geometry_path.write_text(json.dumps(geometry_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery["geometry_fidelity_result"]["sha256"] = hashlib.sha256(geometry_path.read_bytes()).hexdigest()
        render_result = template("render-qa-result.json")
        render_result["project_id"] = "test-project"
        render_result["profile"] = copy.deepcopy(delivery["render_qa_profile"])
        for image_ref in render_result["images"]:
            image_path = target / image_ref["path"]
            image_path.write_bytes(png_bytes(1920, 1080))
            image_ref["sha256"] = hashlib.sha256((target / image_ref["path"]).read_bytes()).hexdigest()
            metadata_path = target / image_ref["camera_metadata"]["path"]
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            presentation = image_ref["path"].endswith("overview.png")
            metadata_payload = {
                "schema_version": "1.0", "artifact_type": "render_camera_metadata", "project_id": "test-project", "revision": 1,
                "image": {"path": image_ref["path"], "sha256": image_ref["sha256"]},
                "camera_name": image_ref["camera"], "projection": "perspective" if presentation else "orthographic",
                "view_matrix": [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0],
                "render_settings": {"width_px": 1920, "height_px": 1080, "engine": "BLENDER_EEVEE_NEXT"},
                "subject_frame_fraction": 0.72 if presentation else 0.5,
                "excluded_collections": ["maintenance_envelopes", "cable_tray_occlusion"] if presentation else [],
                "output_role": "presentation_render" if presentation else "clearance_inspection",
            }
            metadata_path.write_text(json.dumps(metadata_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            image_ref["camera_metadata"]["sha256"] = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
            delivery["artifact_hashes"][image_ref["camera_metadata"]["path"]] = image_ref["camera_metadata"]["sha256"]
            for check in render_result["manual_checks"]:
                for evidence in check["evidence"]:
                    if evidence["path"] == image_ref["path"]: evidence["sha256"] = image_ref["sha256"]
        render_result_path = target / delivery["render_qa_result"]["path"]
        render_result_path.write_text(json.dumps(render_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery["render_qa_result"]["sha256"] = hashlib.sha256(render_result_path.read_bytes()).hexdigest()
        blender_review_path = target / delivery["review_outcomes"][1]
        blender_delivery_review = load_json(blender_review_path)
        blender_delivery_review["geometry_fidelity_assessment"]["result"] = copy.deepcopy(delivery["geometry_fidelity_result"])
        blender_review_path.write_text(json.dumps(blender_delivery_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        review_payloads[1] = (review_payloads[1][0], blender_delivery_review)

        def write_formal_stage(stage: str, manifest_revision: int, outputs: list[str], readback: list[str], formal_revision: int = 1) -> dict:
            packet = template("work-packet.json" if stage == "cad" else "blender-work-packet.json")
            packet.update({
                "project_id": "test-project", "revision": formal_revision, "packet_id": f"{stage.upper()}-WP-{formal_revision:03d}", "stage": stage,
                "manifest_revision": manifest_revision, "source_manifest_revision": manifest_revision,
                "manifest_sha256": delivery["manifest_lineage"][manifest_revision - 1]["sha256"],
                "manifest_lineage": copy.deepcopy(delivery["manifest_lineage"][:manifest_revision]),
                "output_paths": list(dict.fromkeys(outputs + readback)), "writer_scope": list(dict.fromkeys(outputs + readback)),
            })
            packet["input_paths"] = list(dict.fromkeys(packet.get("input_paths", []) + [entry["path"] for entry in packet["manifest_lineage"]]))
            if stage == "blender":
                cad_review_path = target / delivery["review_outcomes"][0]
                cad_stage_path = target / delivery["stage_results"]["cad"]
                packet["render_qa_profile"] = copy.deepcopy(delivery["render_qa_profile"])
                packet["upstream_review"] = {
                    "path": delivery["review_outcomes"][0], "sha256": hashlib.sha256(cad_review_path.read_bytes()).hexdigest(),
                    "review_id": "REV-cad", "outcome": "PASS", "manifest_revision": 2,
                }
                packet["accepted_cad_result"] = {
                    "path": delivery["stage_results"]["cad"], "sha256": hashlib.sha256(cad_stage_path.read_bytes()).hexdigest(),
                    "revision": 1, "manifest_revision": 2, "readback_evidence": ["cad/exports/cad-snapshot.json"],
                    "readback_hashes": {"cad/exports/cad-snapshot.json": hashlib.sha256((target / "cad/exports/cad-snapshot.json").read_bytes()).hexdigest()},
                }
            packet_relative = f"work-packets/{stage}-work-v{formal_revision}.json"
            packet_path = target / packet_relative
            packet_path.parent.mkdir(parents=True, exist_ok=True)
            packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            packet_ref = {"path": packet_relative, "project_id": "test-project", "revision": formal_revision, "packet_id": packet["packet_id"], "sha256": hashlib.sha256(packet_path.read_bytes()).hexdigest()}

            attempt = template("attempt-record.json")
            receipt = template("completion-receipt.json")
            attempt_id = f"{stage.upper()}-ATTEMPT-{formal_revision:03d}"
            application = "FreeCAD" if stage == "cad" else "Blender"
            base_dir = f"{stage}/attempts/{packet['packet_id']}/{attempt_id}"
            writer_log = f"{base_dir}/writer-process.json"
            reader_log = f"{base_dir}/readback-process.json"
            writer_process = copy.deepcopy(attempt["writer_process"])
            writer_process["log"]["path"] = writer_log
            reader_process = copy.deepcopy(receipt["readback_process"])
            reader_process["process_id"] = 1002 if stage == "cad" else 2002
            writer_process["process_id"] = 1001 if stage == "cad" else 2001
            reader_process["log"]["path"] = reader_log
            for artifact in (attempt, receipt):
                artifact.update({"project_id": "test-project", "revision": formal_revision, "stage": stage, "packet_id": packet["packet_id"], "attempt_id": attempt_id, "work_packet": copy.deepcopy(packet_ref)})
            attempt.update({"attempt_directory": base_dir, "interface": {"application": application, "interface_type": "script", "command_summary": "Execute the packet-scoped operator."},
                            "writer_process": writer_process, "expected_outputs": list(packet["output_paths"]),
                            "logs": {"stdout_path": f"{base_dir}/stdout.txt", "stderr_path": f"{base_dir}/stderr.txt", "summary": "Command completed."},
                            "actual_outputs": [{"path": path, "sha256": hashlib.sha256((target / path).read_bytes()).hexdigest()} for path in dict.fromkeys(outputs + readback) if (target / path).is_file()]})
            receipt.update({"application": application, "interface_type": "script", "writer_process": copy.deepcopy(writer_process), "readback_process": reader_process,
                            "outputs": [{"path": path, "sha256": hashlib.sha256((target / path).read_bytes()).hexdigest()} for path in dict.fromkeys(outputs + readback) if (target / path).is_file()]})
            for kind, process, log_relative, artifacts in (
                ("writer", writer_process, writer_log, attempt["actual_outputs"]),
                ("readback", reader_process, reader_log, receipt["outputs"]),
            ):
                event_names = ["process_started", "output_written" if kind == "writer" else "artifact_verified", "process_exited"]
                event_times = ["2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", "2026-01-01T00:00:03Z"] if kind == "writer" else ["2026-01-01T00:01:01Z", "2026-01-01T00:01:02Z", "2026-01-01T00:01:03Z"]
                command = {"argv": [process["executable"], "operator.py" if kind == "writer" else "readback.py"], "cwd": "<PROJECT_ROOT>"}
                process["command_sha256"] = hashlib.sha256(json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
                log = {"schema_version": "1.0", "artifact_type": "process_log", "project_id": "test-project", "revision": formal_revision,
                       "stage": stage, "packet_id": packet["packet_id"], "attempt_id": attempt_id,
                       **{key: process[key] for key in ("process_id", "host", "process_started_at", "executable", "command_sha256")},
                       "process_kind": kind, "command": command,
                       "recorded_at": event_times[-1], "events": [
                           {"at": event_times[0], "event": event_names[0], "details": {"process_id": process["process_id"]}},
                           {"at": event_times[1], "event": event_names[1], "details": {"artifacts": artifacts}},
                           {"at": event_times[2], "event": event_names[2], "details": {"exit_code": 0}},
                       ]}
                log_path = target / log_relative
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                process["log"]["sha256"] = hashlib.sha256(log_path.read_bytes()).hexdigest()
            attempt["writer_process"] = copy.deepcopy(writer_process)
            receipt["writer_process"] = copy.deepcopy(writer_process)
            receipt["readback_process"] = copy.deepcopy(reader_process)
            result = template("stage-result.json")
            result.update({"project_id": "test-project", "revision": formal_revision, "stage": stage, "packet_id": packet["packet_id"],
                           "manifest_revision_consumed": manifest_revision, "outputs": outputs, "readback_evidence": readback, "work_packet": packet_ref})
            for field, artifact in (("promoted_attempt", attempt), ("completion_receipt", receipt)):
                relative = f"{base_dir}/{'attempt-record' if field == 'promoted_attempt' else 'completion-receipt'}.json"
                artifact_path = target / relative
                artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                result[field] = {"path": relative, "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(), "attempt_id": attempt_id}
            result_path = target / delivery["stage_results"][stage]
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            delivery["artifact_hashes"][delivery["stage_results"][stage]] = hashlib.sha256(result_path.read_bytes()).hexdigest()
            for relative in (packet_relative, writer_log, reader_log, result["promoted_attempt"]["path"], result["completion_receipt"]["path"]):
                delivery["artifact_hashes"][relative] = hashlib.sha256((target / relative).read_bytes()).hexdigest()
            return result

        cad_delivery_result = write_formal_stage("cad", 2, ["cad/source/example.FCStd", "cad/exports/cad-snapshot.json"], ["cad/exports/cad-snapshot.json"])
        blender_delivery_result = write_formal_stage("blender", 4, ["blender/example.blend", "blender/exports/blender-snapshot.json"], ["blender/exports/blender-snapshot.json"])
        for relative in delivery["artifact_hashes"]:
            delivery["artifact_hashes"][relative] = hashlib.sha256((target / relative).read_bytes()).hexdigest()
        report_path = target / "delivery" / "delivery-report-v1.json"
        report_path.write_text(json.dumps(delivery, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery_ledger = template("hash-ledger.json")
        delivery_ledger.update({"project_id": "test-project", "self_path": delivery["hash_ledger"]["path"]})
        report_relative = report_path.relative_to(target).as_posix()
        report_digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
        delivery_ledger["covered_report"] = {"path": report_relative, "sha256": report_digest}
        delivery_ledger["entries"] = dict(delivery["artifact_hashes"])
        delivery_ledger["entries"][report_relative] = report_digest
        delivery_ledger["excluded_paths"] = [{"path": delivery["hash_ledger"]["path"], "reason": "A hash ledger never contains its own digest."}]
        ledger_path = target / delivery["hash_ledger"]["path"]
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(delivery_ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        def refresh_delivery_ledger() -> None:
            for artifact_relative in list(delivery["artifact_hashes"]):
                delivery["artifact_hashes"][artifact_relative] = hashlib.sha256((target / artifact_relative).read_bytes()).hexdigest()
            report_path.write_text(json.dumps(delivery, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
            ledger = template("hash-ledger.json")
            ledger.update({"project_id": "test-project", "self_path": delivery["hash_ledger"]["path"]})
            ledger["covered_report"] = {"path": report_relative, "sha256": digest}
            ledger["entries"] = dict(delivery["artifact_hashes"])
            ledger["entries"][report_relative] = digest
            ledger["excluded_paths"] = [{"path": delivery["hash_ledger"]["path"], "reason": "A hash ledger never contains its own digest."}]
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        delivery_errors = validate_files(delivery, target, report_path)
        expect(not delivery_errors, f"delivery artifacts, lineage, render QA, and outer ledger verified: {delivery_errors}")
        (target / "cad/source/unaccepted.FCStd").write_bytes(b"unaccepted-cad")
        (target / "blender/unaccepted.blend").write_bytes(b"unaccepted-blender")
        release_target = root / "published-project"
        release_target.mkdir()
        published_r1 = publish_release(target, report_path, release_target, 1, read_only_hint=False)
        expect(set(published_r1["working_copies_created"]) == {"working/cad/example.FCStd", "working/blender/example.blend"},
               "working copies derive only from accepted release files")
        r1_report_path = release_target / published_r1["report"]
        r1_report = load_json(r1_report_path)
        expect(not validate_files(r1_report, release_target, r1_report_path), "immutable accepted/r1 validates independently of stage directories")
        accepted_cad_stage = release_target / "delivery/accepted/r1" / r1_report["stage_results"]["cad"]
        accepted_cad_stage_bytes = accepted_cad_stage.read_bytes()
        legacy_v3_stage = load_json(accepted_cad_stage)
        for field in ("evidence_contract_version", "work_packet", "promoted_attempt", "completion_receipt"):
            legacy_v3_stage.pop(field, None)
        accepted_cad_stage.write_text(json.dumps(legacy_v3_stage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        expect(any("formal Stage Result" in item for item in validate_files(r1_report, release_target, r1_report_path)), "v3 validate_files rejects a legacy Stage Result")
        accepted_cad_stage.write_bytes(accepted_cad_stage_bytes)
        ledgerless = copy.deepcopy(r1_report)
        ledgerless.pop("hash_ledger")
        expect(bool(validate_files(ledgerless, release_target, r1_report_path)), "v3 cannot downgrade by removing the outer ledger reference")
        package_path = release_target / r1_report["package"]["path"]
        package_bytes = package_path.read_bytes()
        package_path.unlink()
        expect(any("package" in item for item in validate_files(r1_report, release_target, r1_report_path)), "missing package invalidates v3 delivery")
        package_path.write_bytes(b"not-a-zip")
        expect(any("package" in item for item in validate_files(r1_report, release_target, r1_report_path)), "replaced package invalidates v3 delivery")
        package_path.write_bytes(package_bytes)
        working_blend = next((release_target / "working/blender").glob("*.blend"))
        working_blend.write_bytes(working_blend.read_bytes() + b"display-only-change")
        expect(not validate_files(r1_report, release_target, r1_report_path), "modifying working/ does not invalidate accepted/r1")
        expect(not any(path.casefold().startswith("working/") for path in r1_report["artifact_hashes"]), "working/ is excluded from delivery hashes")
        r1_protected = [release_target / "delivery/accepted/r1", r1_report_path, release_target / "delivery/final-hash-ledger-r1.json", package_path]
        r1_hashes_before = {path.relative_to(release_target).as_posix(): ({item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest() for item in path.rglob("*") if item.is_file()} if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()) for path in r1_protected}
        delivery["revision"] = 2
        delivery["accepted_revisions"]["blender_result"] = 2
        delivery["stage_results"]["blender"] = "blender/stage-result-v2.json"
        blender_delivery_result = write_formal_stage("blender", 4, ["blender/example.blend", "blender/exports/blender-snapshot.json"], ["blender/exports/blender-snapshot.json"], 2)
        for index in (1, 2):
            review_relative = delivery["review_outcomes"][index]
            revised_review = load_json(target / review_relative)
            revised_review["revision"] = 2
            if revised_review["review_stage"] == "blender":
                revised_review["consumed_revisions"]["stage_result"] = 2
                revised_review["reviewed_artifacts"] = [delivery["stage_results"]["blender"]]
            else:
                revised_review["consumed_revisions"]["blender_result"] = 2
                revised_review["reviewed_artifacts"] = list(delivery["stage_results"].values())
            (target / review_relative).write_text(json.dumps(revised_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        revised_state = load_json(target / delivery["project_state_path"])
        revised_state["revision"] = 3
        revised_state["active_revisions"] = copy.deepcopy(delivery["accepted_revisions"])
        revised_state["last_transition"]["revisions"] = copy.deepcopy(delivery["accepted_revisions"])
        (target / delivery["project_state_path"]).write_text(json.dumps(revised_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        refresh_delivery_ledger()
        authentic_r1_report_bytes = r1_report_path.read_bytes()
        forged_previous = load_json(r1_report_path)
        forged_previous["accepted_revisions"]["blender_result"] = 0
        r1_report_path.write_text(json.dumps(forged_previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            publish_release(target, report_path, release_target, 2, read_only_hint=False)
        except ValueError as exc:
            expect("previous immutable release failed authentication" in str(exc), "r2 progression rejects an unauthenticated prior release")
        else:
            raise AssertionError("r2 progression trusted an unauthenticated prior release")
        finally:
            r1_report_path.write_bytes(authentic_r1_report_bytes)
        publish_release(target, report_path, release_target, 2, read_only_hint=False)
        r1_hashes_after = {path.relative_to(release_target).as_posix(): ({item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest() for item in path.rglob("*") if item.is_file()} if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()) for path in r1_protected}
        expect(r1_hashes_before == r1_hashes_after, "publishing r2 does not overwrite any r1 evidence")
        try:
            publish_release(target, report_path, release_target, 1, read_only_hint=False)
        except FileExistsError:
            pass
        else:
            raise AssertionError("publisher overwrote existing accepted/r1")
        expect(r1_hashes_after == {path.relative_to(release_target).as_posix(): ({item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest() for item in path.rglob("*") if item.is_file()} if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()) for path in r1_protected}, "failed republish leaves all r1 evidence unchanged")
        overlap_report = copy.deepcopy(r1_report)
        overlap_report["accepted_release"] = {"revision": 1, "path": "working", "immutable": True}
        expect(any("accepted" in item or "overlap" in item for item in validate_files(overlap_report, release_target, r1_report_path)), "accepted and working overlap is rejected")
        traversal_report = copy.deepcopy(r1_report)
        traversal_report["accepted_release"] = {"revision": 1, "path": "delivery/accepted/r1/../../working", "immutable": True}
        expect(bool(validate_files(traversal_report, release_target, r1_report_path)), "accepted path traversal into working is rejected")
        accepted_fcstd = next((release_target / "delivery/accepted/r1").rglob("*.FCStd"))
        accepted_fcstd.write_bytes(accepted_fcstd.read_bytes() + b"tamper")
        expect(any("hash mismatch" in item or "ledger entry mismatch" in item for item in validate_files(r1_report, release_target, r1_report_path)), "modifying accepted/r1 invalidates delivery")
        authentic_delivery = copy.deepcopy(delivery)
        authentic_geometry = copy.deepcopy(geometry_result)
        authentic_mesh_bytes = (target / geometry_result["topology_evidence"]["blender_mesh"]["path"]).read_bytes()
        forged_mesh_path = target / geometry_result["topology_evidence"]["blender_mesh"]["path"]
        forged_mesh = load_json(forged_mesh_path)
        forged_mesh["objects"][0]["vertices"][0][0] = 0.25
        forged_mesh_path.write_text(json.dumps(forged_mesh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        geometry_result["topology_evidence"]["blender_mesh"]["sha256"] = hashlib.sha256(forged_mesh_path.read_bytes()).hexdigest()
        geometry_path.write_text(json.dumps(geometry_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery["geometry_fidelity_result"]["sha256"] = hashlib.sha256(geometry_path.read_bytes()).hexdigest()
        refresh_delivery_ledger()
        expect(any("metrics do not equal the canonical mesh recomputation" in item for item in validate_files(delivery, target, report_path)), "different actual canonical meshes cannot pass with identical claimed metrics")
        forged_mesh_path.write_bytes(authentic_mesh_bytes)
        geometry_result = authentic_geometry
        geometry_path.write_text(json.dumps(geometry_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery = copy.deepcopy(authentic_delivery)
        refresh_delivery_ledger()

        authentic_delivery = copy.deepcopy(delivery)
        authentic_geometry = copy.deepcopy(geometry_result)
        cad_mesh_path = target / geometry_result["topology_evidence"]["cad_mesh"]["path"]
        blender_mesh_path = target / geometry_result["topology_evidence"]["blender_mesh"]["path"]
        authentic_cad_mesh_bytes, authentic_blender_mesh_bytes = cad_mesh_path.read_bytes(), blender_mesh_path.read_bytes()
        for mesh_path, ref_name, metrics_name in ((cad_mesh_path, "cad_mesh", "cad_metrics"), (blender_mesh_path, "blender_mesh", "blender_metrics")):
            wrong_object_mesh = load_json(mesh_path)
            wrong_object_mesh["objects"][0]["object_id"] = "NOT-IN-MANIFEST"
            mesh_path.write_text(json.dumps(wrong_object_mesh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            geometry_result["topology_evidence"][ref_name]["sha256"] = hashlib.sha256(mesh_path.read_bytes()).hexdigest()
            mesh_errors, wrong_metrics = canonical_mesh_metrics(wrong_object_mesh)
            expect(not mesh_errors and wrong_metrics is not None, f"structurally valid wrong-object {ref_name} evidence")
            geometry_result["topology_evidence"][metrics_name] = wrong_metrics
        geometry_path.write_text(json.dumps(geometry_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery["geometry_fidelity_result"]["sha256"] = hashlib.sha256(geometry_path.read_bytes()).hexdigest()
        refresh_delivery_ledger()
        expect(any("canonical mesh object IDs" in item for item in validate_files(delivery, target, report_path)), "canonical mesh object partitions must exactly cover Manifest controlled IDs")
        cad_mesh_path.write_bytes(authentic_cad_mesh_bytes)
        blender_mesh_path.write_bytes(authentic_blender_mesh_bytes)
        geometry_result = authentic_geometry
        geometry_path.write_text(json.dumps(geometry_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery = copy.deepcopy(authentic_delivery)
        refresh_delivery_ledger()

        authentic_delivery = copy.deepcopy(delivery)
        authentic_render = copy.deepcopy(render_result)
        overview_metadata_ref = render_result["images"][0]["camera_metadata"]
        overview_metadata_path = target / overview_metadata_ref["path"]
        authentic_metadata_bytes = overview_metadata_path.read_bytes()
        forged_metadata = load_json(overview_metadata_path)
        forged_metadata["subject_frame_fraction"] = 0.01
        overview_metadata_path.write_text(json.dumps(forged_metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        overview_metadata_ref["sha256"] = hashlib.sha256(overview_metadata_path.read_bytes()).hexdigest()
        render_result_path.write_text(json.dumps(render_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery["render_qa_result"]["sha256"] = hashlib.sha256(render_result_path.read_bytes()).hexdigest()
        refresh_delivery_ledger()
        expect(any("subject frame fraction" in item for item in validate_files(delivery, target, report_path)), "self-reported subject framing cannot override bound camera metadata")
        overview_metadata_path.write_bytes(authentic_metadata_bytes)
        render_result = authentic_render
        render_result_path.write_text(json.dumps(render_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery = copy.deepcopy(authentic_delivery)
        refresh_delivery_ledger()

        authentic_delivery = copy.deepcopy(delivery)
        authentic_render = copy.deepcopy(render_result)
        clearance_metadata_ref = render_result["images"][1]["camera_metadata"]
        clearance_metadata_path = target / clearance_metadata_ref["path"]
        authentic_clearance_metadata = clearance_metadata_path.read_bytes()
        duplicate_role_metadata = load_json(clearance_metadata_path)
        duplicate_role_metadata["output_role"] = "presentation_render"
        clearance_metadata_path.write_text(json.dumps(duplicate_role_metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        clearance_metadata_ref["sha256"] = hashlib.sha256(clearance_metadata_path.read_bytes()).hexdigest()
        render_result_path.write_text(json.dumps(render_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery["render_qa_result"]["sha256"] = hashlib.sha256(render_result_path.read_bytes()).hexdigest()
        refresh_delivery_ledger()
        expect(any("exactly one presentation_render" in item for item in validate_files(delivery, target, report_path)), "duplicate render output roles are rejected")
        clearance_metadata_path.write_bytes(authentic_clearance_metadata)
        render_result = authentic_render
        render_result_path.write_text(json.dumps(render_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery = copy.deepcopy(authentic_delivery)
        refresh_delivery_ledger()
        blender_review_path = target / delivery["review_outcomes"][1]
        rejected_fidelity_review = load_json(blender_review_path)
        rejected_fidelity_review["geometry_fidelity_assessment"]["outcome"] = "FAIL"
        blender_review_path.write_text(json.dumps(rejected_fidelity_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery["artifact_hashes"][delivery["review_outcomes"][1]] = hashlib.sha256(blender_review_path.read_bytes()).hexdigest()
        expect(any("accepted Blender review requires" in item for item in validate_files(delivery, target)), "Delivery Report rejects accepted Blender review with failed fidelity")
        restored_blender_review = review_payloads[1][1]
        blender_review_path.write_text(json.dumps(restored_blender_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery["artifact_hashes"][delivery["review_outcomes"][1]] = hashlib.sha256(blender_review_path.read_bytes()).hexdigest()
        delivery.pop("hash_ledger")
        delivery.pop("render_qa_profile")
        bad_review_path = target / delivery["review_outcomes"][0]
        bad_review = load_json(bad_review_path)
        bad_review["consumed_revisions"]["manifest"] = 99
        bad_review_path.write_text(json.dumps(bad_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delivery["artifact_hashes"][delivery["review_outcomes"][0]] = hashlib.sha256(bad_review_path.read_bytes()).hexdigest()
        expect(any("not an ancestor" in item for item in validate_files(delivery, target)), "non-ancestor historical Manifest revision rejected")
        delivery["artifact_hashes"][delivery["deliverables"][0]] = "0" * 64
        expect(any("hash mismatch" in item for item in validate_files(delivery, target)), "delivery hash mismatch rejected")

    cad = template("cad-snapshot.json")
    blender = template("blender-snapshot.json")
    expect(not validate_snapshot(cad, "cad_snapshot") and not validate_snapshot(blender, "blender_snapshot"), "formal CAD and Blender Snapshot contracts pass")
    collinear = copy.deepcopy(cad)
    collinear["coordinate_system"]["forward_axis"] = "-Z"
    expect(any("collinear" in item for item in validate_snapshot(collinear, "cad_snapshot")), "opposite signed axes on the same coordinate axis rejected")
    malformed_axis = copy.deepcopy(cad)
    malformed_axis["coordinate_system"]["up_axis"] = []
    expect(any("signed principal axis" in item for item in validate_snapshot(malformed_axis, "cad_snapshot")), "non-string coordinate axis returns structured errors")
    parent_cycle = copy.deepcopy(cad)
    second_object = copy.deepcopy(parent_cycle["objects"][0])
    second_object["object_id"] = "PART-002"
    parent_cycle["objects"][0]["parent_id"] = "PART-002"
    second_object["parent_id"] = "PART-001"
    parent_cycle["objects"].append(second_object)
    expect(any("parent hierarchy contains a cycle" in item for item in validate_snapshot(parent_cycle, "cad_snapshot")), "multi-object parent cycle rejected")
    expect(compare(cad, blender, manifest)["consistent"], "matching snapshots pass")
    legacy_cad = copy.deepcopy(cad)
    legacy_cad.pop("units")
    legacy_cad.pop("coordinate_system")
    legacy_cad.pop("manifest_lineage")
    for item in legacy_cad["objects"]:
        item.pop("controlled")
        item.pop("transform")
    legacy_root_manifest = copy.deepcopy(manifest)
    with tempfile.TemporaryDirectory(prefix="legacy-adapter-test-") as legacy_directory:
        legacy_root = Path(legacy_directory)
        legacy_manifest_path = legacy_root / "manifests" / "manifest-v1.json"
        legacy_manifest_path.parent.mkdir(parents=True)
        legacy_manifest_path.write_text(json.dumps(legacy_root_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        adapted_cad, migration_warnings = adapt_snapshot(legacy_cad, legacy_root)
    expect(not validate_snapshot(adapted_cad, "cad_snapshot") and migration_warnings, "legacy snapshot adapter yields valid explicitly warned evidence")
    adapted_comparison = compare(adapted_cad, blender, manifest)
    expect(not adapted_comparison["consistent"] and adapted_comparison["migration_warnings"], "legacy adaptation cannot fabricate fully verified zero-difference consistency")
    adapted_cad["objects"][0]["critical_dimensions"]["width"] = 101.0
    adapted_difference = compare(adapted_cad, blender, manifest)
    expect(adapted_difference["engineering_difference_count"] > 0, "legacy migration warnings remain separate from real engineering differences")
    cad_v2, blender_v4, manifest_v4 = copy.deepcopy(cad), copy.deepcopy(blender), copy.deepcopy(manifest)
    manifest_v4["revision"] = 4
    manifest_v4["parent_manifest"] = {"revision": 3, "path": "manifests/manifest-v3.json", "sha256": "0" * 64}
    cad_v2["manifest_revision"], blender_v4["manifest_revision"] = 2, 4
    canonical_lineage = [
        {"revision": revision, "parent_revision": revision - 1 if revision > 1 else None, "path": f"manifests/manifest-v{revision}.json", "sha256": "0" * 64}
        for revision in range(1, 5)
    ]
    cad_v2["manifest_lineage"] = copy.deepcopy(canonical_lineage[:2])
    blender_v4["manifest_lineage"] = copy.deepcopy(canonical_lineage)
    unauthenticated_v4 = compare(cad_v2, blender_v4, manifest_v4)
    expect(not unauthenticated_v4["consistent"] and any(item["category"] == "authentication_project_root_required" for item in unauthenticated_v4["differences"]), "revision > 1 comparison without project root is rejected")
    forged_history = copy.deepcopy(cad_v2)
    forged_history["manifest_revision"] = 9
    expect(any(item["category"] == "snapshot_manifest_not_ancestor" for item in compare(forged_history, blender_v4, manifest_v4)["differences"]), "non-ancestor snapshot Manifest revision rejected")
    malformed_cad = copy.deepcopy(cad)
    malformed_cad["manifest_lineage"] = None
    malformed_cad["objects"] = None
    expect(not compare(malformed_cad, blender_v4, manifest_v4)["consistent"], "comparison returns findings for null lineage and objects without exception")
    expect(not typed_differences("DN100", "DN100", 0.001), "typed string DN100 compares exactly")
    expect(typed_differences("+X", "+Y", 0.001)[0]["reason"] == "value_mismatch", "typed axis string mismatch reported")
    expect(typed_differences(True, 1, 0.001)[0]["reason"] == "type_mismatch", "boolean is not treated as a number")
    expect(not typed_differences({"path": [[0.0, 1.0, 2.0]]}, {"path": [[0.0005, 1.0, 2.0]]}, 0.001), "nested position array passes within tolerance")
    expect(typed_differences({"path": [0.0]}, {"path": [0.01]}, 0.001)[0]["reason"] == "numeric_tolerance_exceeded", "nested numeric tolerance exceedance reported")
    expect(typed_differences({"a": 1}, {}, 0.001)[0]["reason"] == "missing_key_in_blender", "missing typed key reported")
    expect(typed_differences({}, {"a": 1}, 0.001)[0]["reason"] == "extra_key_in_blender", "extra typed key reported")
    expect(typed_differences(None, "", 0.001)[0]["reason"] == "type_mismatch", "null type mismatch reported")
    blender["objects"][0]["critical_dimensions"]["width"] = 101.0
    diff = compare(cad, blender, manifest)
    expect(not diff["consistent"] and diff["difference_count"] == 1, "intentional CAD/Blender difference detected")
    other_project = template("blender-snapshot.json")
    other_project["project_id"] = "other-project"
    expect(any(item["category"] == "project_mismatch" for item in compare(cad, other_project, manifest)["differences"]), "cross-project snapshots rejected")
    expect(compare(cad, other_project, manifest)["engineering_difference_count"] == 0, "project identity mismatch is not classified as an engineering difference")
    wrong_type = template("blender-snapshot.json")
    wrong_type["artifact_type"] = "cad_snapshot"
    expect(any(item["category"] == "invalid_blender_snapshot" for item in compare(cad, wrong_type, manifest)["differences"]), "wrong snapshot type rejected")
    common_drift_cad, common_drift_blender = copy.deepcopy(cad), template("blender-snapshot.json")
    common_drift_cad["units"] = common_drift_blender["units"] = "cm"
    common_drift_cad["objects"][0]["critical_dimensions"]["width"] = 999.0
    common_drift_blender["objects"][0]["critical_dimensions"]["width"] = 999.0
    expect(not compare(common_drift_cad, common_drift_blender, manifest)["consistent"], "common CAD/Blender drift from Manifest rejected")
    bad_tolerance_manifest = copy.deepcopy(manifest)
    bad_tolerance_manifest["comparison_tolerance"]["value"] = float("nan")
    try:
        compare(cad, blender, bad_tolerance_manifest)
    except ValueError:
        print("PASS non-finite Manifest tolerance rejected")
    else:
        raise AssertionError("non-finite Manifest tolerance was accepted")

    with tempfile.TemporaryDirectory(prefix="manifest-cli-auth-") as auth_directory:
        auth_root = Path(auth_directory)
        (auth_root / "manifests").mkdir(parents=True)
        auth_baseline = template("requirements-baseline.json")
        auth_baseline_path = auth_root / "requirements-baseline-v1.json"
        auth_baseline_path.write_text(json.dumps(auth_baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        auth_v1 = template("project-manifest.json")
        auth_v1["requirements_baseline"]["sha256"] = hashlib.sha256(auth_baseline_path.read_bytes()).hexdigest()
        auth_v1_path = auth_root / "manifests" / "manifest-v1.json"
        auth_v1_path.write_text(json.dumps(auth_v1, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        auth_v2 = copy.deepcopy(auth_v1)
        auth_v2["revision"] = 2
        auth_v2["parent_manifest"] = {"revision": 1, "path": "manifests/manifest-v1.json", "sha256": hashlib.sha256(auth_v1_path.read_bytes()).hexdigest()}
        auth_v2_path = auth_root / "manifests" / "manifest-v2.json"
        auth_v2_path.write_text(json.dumps(auth_v2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lineage_path = auth_root / "lineage.json"
        lineage_path.write_text(json.dumps({"manifest_lineage": [
            {"revision": 1, "parent_revision": None, "path": "manifests/manifest-v1.json", "sha256": hashlib.sha256(auth_v1_path.read_bytes()).hexdigest()},
            {"revision": 2, "parent_revision": 1, "path": "manifests/manifest-v2.json", "sha256": hashlib.sha256(auth_v2_path.read_bytes()).hexdigest()},
        ]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest_cli = SKILL_ROOT / "scripts" / "validate_project_manifest.py"
        valid_cli = subprocess.run([sys.executable, str(manifest_cli), str(auth_v2_path), "--baseline", str(auth_baseline_path), "--project-root", str(auth_root), "--manifest-lineage", str(lineage_path)], capture_output=True, text=True, check=False)
        expect(valid_cli.returncode == 0, "Manifest CLI accepts the actual final authenticated lineage file")
        borrowed_path = auth_root / "borrowed-manifest-v2.json"
        borrowed_path.write_bytes(auth_v2_path.read_bytes())
        borrowed_cli = subprocess.run([sys.executable, str(manifest_cli), str(borrowed_path), "--baseline", str(auth_baseline_path), "--project-root", str(auth_root), "--manifest-lineage", str(lineage_path)], capture_output=True, text=True, check=False)
        expect(borrowed_cli.returncode != 0 and "supplied Manifest path" in borrowed_cli.stdout, "Manifest CLI rejects a different file borrowing an authenticated lineage")
        legacy_cli = subprocess.run([sys.executable, str(manifest_cli), str(auth_v2_path), "--baseline", str(auth_baseline_path), "--legacy-unverified"], capture_output=True, text=True, check=False)
        expect(legacy_cli.returncode != 0, "legacy Manifest migration analysis never exits as formal success")

    print("ALL SELF-TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
