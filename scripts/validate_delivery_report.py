from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import zlib
import zipfile
from datetime import datetime
from pathlib import Path

from artifact_validation import (
    file_sha256, legacy_warnings, load_json, validate_attempt_record, validate_completion_receipt,
    relative_path_set, validate_delivery_report, validate_hash_ledger, validate_manifest_lineage, validate_project_state,
    validate_geometry_fidelity_result, validate_render_qa_profile, validate_render_qa_result, validate_review, validate_stage_result,
    validate_work_packet_file_binding, validate_process_log, validate_snapshot, canonical_mesh_metrics,
)


def _inside_file(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve()
    return candidate if root in candidate.parents and candidate.is_file() else None


def _validate_release_package(report: dict, project: Path, report_path: Path, ledger_path: Path, package_path_override: Path | None = None) -> list[str]:
    errors: list[str] = []
    package_ref = report.get("package") if isinstance(report.get("package"), dict) else {}
    package_relative = package_ref.get("path")
    package_path = package_path_override.resolve() if package_path_override is not None else (_inside_file(project, package_relative) if isinstance(package_relative, str) else None)
    if package_path is None:
        return [f"delivery package is missing or outside project root: {package_relative}"]
    accepted = report.get("accepted_release") if isinstance(report.get("accepted_release"), dict) else {}
    accepted_prefix = accepted.get("path")
    expected_hashes = {
        f"{accepted_prefix}/{relative}": digest
        for relative, digest in (report.get("artifact_hashes") if isinstance(report.get("artifact_hashes"), dict) else {}).items()
    }
    report_relative = report_path.relative_to(project).as_posix()
    ledger_relative = ledger_path.relative_to(project).as_posix()
    expected_hashes[report_relative] = file_sha256(report_path)
    expected_hashes[ledger_relative] = file_sha256(ledger_path)
    try:
        with zipfile.ZipFile(package_path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            names = [info.filename.replace("\\", "/") for info in infos]
            if len(names) != len(set(name.casefold() for name in names)):
                errors.append("delivery package contains duplicate or case-colliding member paths")
            if set(names) != set(expected_hashes):
                errors.append("delivery package members do not exactly match accepted artifacts, report, and ledger")
            for info, name in zip(infos, names):
                if name not in expected_hashes:
                    continue
                digest = hashlib.sha256()
                with archive.open(info) as member:
                    for chunk in iter(lambda: member.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != expected_hashes[name]:
                    errors.append(f"delivery package member hash mismatch: {name}")
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        errors.append(f"delivery package is invalid: {exc}")
    return errors


def _utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    if len(payload) < 45 or payload[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    offset, chunks, idat, ended, idat_started, idat_finished = 8, [], bytearray(), False, False, False
    allowed_critical = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
    while offset + 12 <= len(payload):
        length = struct.unpack(">I", payload[offset:offset + 4])[0]
        chunk_type = payload[offset + 4:offset + 8]
        if len(chunk_type) != 4 or any(not (65 <= byte <= 90 or 97 <= byte <= 122) for byte in chunk_type) or not (65 <= chunk_type[2] <= 90):
            return None
        end = offset + 12 + length
        if end > len(payload):
            return None
        chunk_data = payload[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", payload[offset + 8 + length:end])[0]
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            return None
        if chunk_type[:1].isupper() and chunk_type not in allowed_critical:
            return None
        if chunk_type == b"IHDR" and chunks:
            return None
        if chunk_type == b"PLTE" and (idat_started or any(kind == b"PLTE" for kind, _ in chunks)):
            return None
        if chunk_type == b"IDAT":
            if idat_finished: return None
            idat_started = True
        elif idat_started and chunk_type != b"IEND":
            idat_finished = True
        chunks.append((chunk_type, chunk_data))
        if chunk_type == b"IDAT": idat.extend(chunk_data)
        if chunk_type == b"IEND":
            if length != 0 or end != len(payload): return None
            ended = True
            break
        offset = end
    if not ended or not chunks or chunks[0][0] != b"IHDR" or sum(kind == b"IHDR" for kind, _ in chunks) != 1 or sum(kind == b"IEND" for kind, _ in chunks) != 1 or len(chunks[0][1]) != 13 or not idat:
        return None
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    allowed = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    palette_chunks = [data for kind, data in chunks if kind == b"PLTE"]
    if width < 1 or height < 1 or width * height > 100_000_000 or color_type not in allowed or bit_depth not in allowed[color_type] or compression != 0 or filtering != 0 or interlace != 0:
        return None
    if color_type == 3 and (len(palette_chunks) != 1 or not 3 <= len(palette_chunks[0]) <= 768 or len(palette_chunks[0]) % 3 or len(palette_chunks[0]) // 3 > 2 ** bit_depth):
        return None
    if color_type in {0, 4} and palette_chunks:
        return None
    try:
        decompressor = zlib.decompressobj()
        decoded = decompressor.decompress(bytes(idat)) + decompressor.flush()
    except zlib.error:
        return None
    if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
        return None
    row_bytes = (width * channels[color_type] * bit_depth + 7) // 8
    expected_length = height * (row_bytes + 1)
    if len(decoded) != expected_length or any(decoded[row * (row_bytes + 1)] > 4 for row in range(height)):
        return None
    bytes_per_pixel = max(1, (channels[color_type] * bit_depth + 7) // 8)
    prior = bytearray(row_bytes)
    unfiltered_rows: list[bytes] = []
    for row in range(height):
        start = row * (row_bytes + 1)
        filter_type = decoded[start]
        current = bytearray(decoded[start + 1:start + 1 + row_bytes])
        for index in range(row_bytes):
            left = current[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            up = prior[index]
            upper_left = prior[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 1:
                current[index] = (current[index] + left) & 0xFF
            elif filter_type == 2:
                current[index] = (current[index] + up) & 0xFF
            elif filter_type == 3:
                current[index] = (current[index] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                predictor = left + up - upper_left
                pa, pb, pc = abs(predictor - left), abs(predictor - up), abs(predictor - upper_left)
                current[index] = (current[index] + (left if pa <= pb and pa <= pc else up if pb <= pc else upper_left)) & 0xFF
        unfiltered_rows.append(bytes(current))
        prior = current
    if color_type == 3:
        palette_size = len(palette_chunks[0]) // 3
        for row in unfiltered_rows:
            indices: list[int] = []
            if bit_depth == 8:
                indices = list(row[:width])
            else:
                mask = (1 << bit_depth) - 1
                for pixel in range(width):
                    bit_offset = pixel * bit_depth
                    byte = row[bit_offset // 8]
                    shift = 8 - bit_depth - (bit_offset % 8)
                    indices.append((byte >> shift) & mask)
            if any(index >= palette_size for index in indices):
                return None
    return width, height


def _object_value(snapshot: dict, object_id: str, property_name: str) -> object:
    obj = next((item for item in snapshot.get("objects", []) if isinstance(item, dict) and item.get("object_id") == object_id), None)
    if obj is None:
        return None
    value: object = obj
    for token in property_name.split("."):
        if not isinstance(value, dict) or token not in value:
            return None
        value = value[token]
    return value


def _load_bound_json(root: Path, ref: object) -> tuple[Path | None, dict | None]:
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
        return None, None
    path = _inside_file(root, ref["path"])
    if path is None or ref.get("sha256") != file_sha256(path):
        return path, None
    try:
        return path, load_json(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return path, None


def validate_stage_evidence(result: dict, root: Path, allow_legacy_unverified: bool = False) -> list[str]:
    if result.get("evidence_contract_version") != "1.0":
        return [] if allow_legacy_unverified else ["formal Stage Result requires evidence_contract_version 1.0 and hash-bound Work Packet/attempt/receipt evidence"]
    errors: list[str] = []
    packet_ref = result.get("work_packet")
    packet: dict = {}
    errors += validate_work_packet_file_binding(
        packet_ref, root, result.get("project_id"), result.get("stage"), result.get("manifest_revision_consumed"),
    )
    packet_relative = packet_ref.get("path") if isinstance(packet_ref, dict) else None
    packet_path = _inside_file(root, packet_relative) if isinstance(packet_relative, str) else None
    if packet_path is None:
        errors.append(f"work_packet is missing or outside project root: {packet_relative}")
    elif packet_ref.get("sha256") != file_sha256(packet_path):
        errors.append("work_packet SHA-256 mismatch")
    else:
        packet = load_json(packet_path)
        for key in ("project_id", "stage", "packet_id"):
            if packet.get(key) != result.get(key): errors.append(f"work_packet {key} does not match Stage Result")
        if packet.get("revision") != packet_ref.get("revision"): errors.append("work_packet revision does not match reference")
        allowed = relative_path_set(packet.get("output_paths"))
        if not (relative_path_set(result.get("outputs")) | relative_path_set(result.get("readback_evidence"))).issubset(allowed):
            errors.append("Stage Result outputs/read-back evidence exceed Work Packet output_paths")
    loaded: dict[str, dict] = {}
    for field, validator in (("promoted_attempt", validate_attempt_record), ("completion_receipt", validate_completion_receipt)):
        ref = result.get(field, {})
        relative = ref.get("path")
        path = _inside_file(root, relative) if isinstance(relative, str) else None
        if path is None:
            errors.append(f"{field} is missing or outside project root: {relative}")
            continue
        if ref.get("sha256") != file_sha256(path):
            errors.append(f"{field} SHA-256 mismatch: {relative}")
        artifact = load_json(path)
        loaded[field] = artifact
        errors += [f"{relative}: {item}" for item in validator(artifact)]
        for key in ("project_id", "stage", "packet_id"):
            if artifact.get(key) != result.get(key):
                errors.append(f"{field} {key} does not match Stage Result")
        if artifact.get("work_packet") != packet_ref:
            errors.append(f"{field} work_packet reference does not exactly match Stage Result")
        if artifact.get("attempt_id") != ref.get("attempt_id"):
            errors.append(f"{field} attempt_id does not match Stage Result reference")
    attempt, receipt = loaded.get("promoted_attempt", {}), loaded.get("completion_receipt", {})
    if attempt and receipt:
        if attempt.get("attempt_id") != receipt.get("attempt_id"):
            errors.append("attempt record and completion receipt identify different attempts")
        if attempt.get("promoted_to_stage_result") is not True or receipt.get("status") != "completed":
            errors.append("only a successful promoted attempt with completed receipt may create a Stage Result")
        if receipt.get("process_identity_assurance") != "verified_by_log_hashes":
            errors.append("Stage Result promotion requires process_identity_assurance verified_by_log_hashes")
        if packet:
            allowed = relative_path_set(packet.get("output_paths"))
            attempt_expected = relative_path_set(attempt.get("expected_outputs"))
            attempt_actual = {item.get("path") for item in attempt.get("actual_outputs", []) if isinstance(item, dict)}
            receipt_outputs = {item.get("path") for item in receipt.get("outputs", []) if isinstance(item, dict)}
            if attempt_expected != allowed or not attempt_actual.issubset(allowed) or not receipt_outputs.issubset(allowed):
                errors.append("attempt/receipt output authorization does not match Work Packet output_paths")
        interface = attempt.get("interface") if isinstance(attempt.get("interface"), dict) else {}
        if interface.get("application") != receipt.get("application"):
            errors.append("attempt interface application does not match completion receipt")
        if interface.get("interface_type") != receipt.get("interface_type"):
            errors.append("attempt interface_type does not match completion receipt")
        if attempt.get("writer_process") != receipt.get("writer_process"):
            errors.append("attempt writer_process evidence does not exactly match completion receipt")
        started, ended = (_utc_timestamp(attempt.get(key)) for key in ("started_at", "ended_at"))
        completed = _utc_timestamp(receipt.get("completed_at"))
        writer_started = _utc_timestamp((attempt.get("writer_process") or {}).get("process_started_at")) if isinstance(attempt.get("writer_process"), dict) else None
        reader_started = _utc_timestamp((receipt.get("readback_process") or {}).get("process_started_at")) if isinstance(receipt.get("readback_process"), dict) else None
        if all(value is not None for value in (writer_started, started, ended, reader_started, completed)) and not (writer_started <= started <= ended <= reader_started <= completed):
            errors.append("evidence timestamps must satisfy writer_process <= started_at <= ended_at <= readback_process <= completed_at")
        for process_name, process in (("writer_process", attempt.get("writer_process")), ("readback_process", receipt.get("readback_process"))):
            log_ref = process.get("log") if isinstance(process, dict) else None
            relative = log_ref.get("path") if isinstance(log_ref, dict) else None
            log_path = _inside_file(root, relative) if isinstance(relative, str) else None
            if log_path is None:
                errors.append(f"{process_name} independent log is missing or outside project root: {relative}")
            elif log_ref.get("sha256") != file_sha256(log_path):
                errors.append(f"{process_name} independent log SHA-256 mismatch: {relative}")
            else:
                try:
                    process_log = load_json(log_path)
                except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(f"{process_name} log must be process_log JSON: {exc}")
                else:
                    errors += [f"{process_name} log: {item}" for item in validate_process_log(process_log)]
                    expected_kind = "writer" if process_name == "writer_process" else "readback"
                    event_limit = ended if expected_kind == "writer" else completed
                    for event in process_log.get("events", []) if isinstance(process_log.get("events"), list) else []:
                        event_at = _utc_timestamp(event.get("at")) if isinstance(event, dict) else None
                        if event_at is not None and event_limit is not None and event_at > event_limit:
                            errors.append(f"{process_name} log event occurs after its attempt/receipt ended")
                    if process_log.get("process_kind") != expected_kind:
                        errors.append(f"{process_name} log process_kind must be {expected_kind}")
                    for key, expected_value in (("project_id", result.get("project_id")), ("stage", result.get("stage")), ("packet_id", result.get("packet_id")), ("attempt_id", attempt.get("attempt_id"))):
                        if process_log.get(key) != expected_value:
                            errors.append(f"{process_name} log {key} does not match the promoted attempt")
                    for key in ("process_id", "host", "process_started_at", "executable", "command_sha256"):
                        if process_log.get(key) != process.get(key):
                            errors.append(f"{process_name} log {key} does not match process evidence")
                    artifact_event_name = "output_written" if expected_kind == "writer" else "artifact_verified"
                    artifact_event = next((event for event in process_log.get("events", []) if isinstance(event, dict) and event.get("event") == artifact_event_name), {})
                    logged_artifacts = artifact_event.get("details", {}).get("artifacts") if isinstance(artifact_event.get("details"), dict) else None
                    expected_artifacts = attempt.get("actual_outputs") if expected_kind == "writer" else receipt.get("outputs")
                    if logged_artifacts != expected_artifacts:
                        errors.append(f"{process_name} log artifacts do not exactly match the attempt/receipt outputs")
                    exit_event = next((event for event in process_log.get("events", []) if isinstance(event, dict) and event.get("event") == "process_exited"), {})
                    expected_exit = attempt.get("exit_code") if expected_kind == "writer" else 0
                    if not isinstance(exit_event.get("details"), dict) or exit_event["details"].get("exit_code") != expected_exit:
                        errors.append(f"{process_name} log exit_code does not match the promoted evidence")
        attempt_outputs = {item.get("path"): item.get("sha256") for item in attempt.get("actual_outputs", []) if isinstance(item, dict)}
        receipt_outputs = {item.get("path"): item.get("sha256") for item in receipt.get("outputs", []) if isinstance(item, dict)}
        promoted_paths = relative_path_set(result.get("outputs")) | relative_path_set(result.get("readback_evidence"))
        for relative in promoted_paths:
            path = _inside_file(root, relative)
            if path is None:
                errors.append(f"promoted output is missing or outside project root: {relative}")
                continue
            digest = file_sha256(path)
            if attempt_outputs.get(relative) != digest or receipt_outputs.get(relative) != digest:
                errors.append(f"promoted output/read-back evidence is not identically hash-bound by attempt and receipt: {relative}")
    return errors


def validate_files(report: dict, project_root: Path, report_path: Path | None = None, package_path_override: Path | None = None) -> list[str]:
    errors = validate_delivery_report(report)
    project = project_root.resolve()
    root = project
    accepted_prefix: str | None = None
    if report.get("delivery_contract_version") == "3.0":
        release = report.get("accepted_release") if isinstance(report.get("accepted_release"), dict) else {}
        accepted_prefix = str(release.get("path", "")).replace("\\", "/")
        release_root = (project / accepted_prefix).resolve()
        working_root = (project / "working").resolve()
        if project not in release_root.parents or release_root == working_root or working_root in release_root.parents or release_root in working_root.parents:
            errors.append("accepted release path escapes, equals, or overlaps working/")
        elif not release_root.is_dir():
            errors.append(f"accepted release directory is missing: {accepted_prefix}")
        else:
            root = release_root
        if report_path is not None:
            expected_report = (project / "delivery" / f"delivery-report-r{release.get('revision')}.json").resolve()
            if report_path.resolve() != expected_report:
                errors.append("delivery contract 3.0 report path must be delivery/delivery-report-rN.json")
        if errors:
            return errors
    paths = relative_path_set(report.get("deliverables")) | relative_path_set(report.get("readback_evidence")) | relative_path_set(report.get("review_outcomes"))
    stage_values = report.get("stage_results", {}).values() if isinstance(report.get("stage_results"), dict) else []
    paths |= {value for value in stage_values if isinstance(value, str)} | ({report.get("project_state_path")} if isinstance(report.get("project_state_path"), str) else set())
    paths |= {entry.get("path") for entry in report.get("manifest_lineage", []) if isinstance(entry, dict)}
    paths |= {value for value in report.get("artifact_hashes", {}) if isinstance(value, str)} if isinstance(report.get("artifact_hashes"), dict) else set()
    for relative in paths:
        if not isinstance(relative, str) or not relative:
            errors.append(f"invalid delivery artifact path: {relative!r}")
            continue
        artifact = (root / relative).resolve()
        if root not in artifact.parents or not artifact.is_file():
            errors.append(f"missing or out-of-root delivery artifact: {relative}")
            continue
        if report.get("artifact_hashes", {}).get(relative) != file_sha256(artifact):
            errors.append(f"delivery artifact hash mismatch: {relative}")
    final_manifest_revision = report.get("accepted_revisions", {}).get("manifest")
    authenticated_lineage = report.get("delivery_contract_version") in {"2.0", "3.0"}
    lineage_errors, manifest_ancestors = validate_manifest_lineage(
        report.get("manifest_lineage"), final_manifest_revision, root, report.get("project_id"), report.get("artifact_hashes"), require_authenticated=authenticated_lineage
    )
    errors += lineage_errors
    reviews_by_stage: dict[str, dict] = {}
    review_ids: set[str] = set()
    for relative in report.get("review_outcomes", []):
        review_path = (root / relative).resolve()
        if review_path.is_file():
            review = load_json(review_path)
            errors += [f"{relative}: {item}" for item in validate_review(review)]
            if review.get("project_id") != report.get("project_id"):
                errors.append(f"review project mismatch: {relative}")
            if review.get("outcome") not in {"PASS", "CONDITIONAL_PASS"}:
                errors.append(f"review is not accepted: {relative}")
            consumed_manifest = review.get("consumed_revisions", {}).get("manifest")
            if consumed_manifest not in manifest_ancestors:
                errors.append(f"review consumed Manifest revision is not an ancestor of the final Manifest: {relative}")
            stage = review.get("review_stage")
            if stage in reviews_by_stage:
                errors.append(f"duplicate review_stage: {stage}")
            reviews_by_stage[stage] = review
            if review.get("review_id") in review_ids:
                errors.append(f"duplicate review_id: {review.get('review_id')}")
            review_ids.add(review.get("review_id"))
    if set(reviews_by_stage) != {"cad", "blender", "cross_software"}:
        errors.append("delivery requires exactly one accepted cad, blender, and cross_software review")

    accepted = report.get("accepted_revisions", {})
    stage_results: dict[str, dict] = {}
    for stage, relative in report.get("stage_results", {}).items():
        result_path = (root / relative).resolve()
        if not result_path.is_file():
            continue
        result = load_json(result_path)
        stage_results[stage] = result
        errors += [f"{relative}: {item}" for item in validate_stage_result(result)]
        if isinstance(result.get("work_packet"), dict):
            errors += [f"{relative}: {item}" for item in validate_work_packet_file_binding(result.get("work_packet"), root, report.get("project_id"), stage, result.get("manifest_revision_consumed"))]
            packet_ref = result.get("work_packet")
            packet_path = _inside_file(root, packet_ref.get("path")) if isinstance(packet_ref.get("path"), str) else None
            if packet_path is not None:
                packet = load_json(packet_path)
                allowed = relative_path_set(packet.get("output_paths"))
                declared = relative_path_set(result.get("outputs")) | relative_path_set(result.get("readback_evidence"))
                if not declared.issubset(allowed):
                    errors.append(f"{relative}: Stage Result outputs/read-back evidence exceed Work Packet output_paths")
        errors += [f"{relative}: {item}" for item in validate_stage_evidence(result, root, allow_legacy_unverified=report.get("delivery_contract_version") not in {"2.0", "3.0"})]
        if result.get("project_id") != report.get("project_id") or result.get("stage") != stage or result.get("status") != "completed":
            errors.append(f"{stage} Stage Result identity/status mismatch")
        if result.get("revision") != accepted.get(f"{stage}_result"):
            errors.append(f"{stage} Stage Result revision mismatch")
        if result.get("manifest_revision_consumed") not in manifest_ancestors:
            errors.append(f"{stage} Stage Result consumed Manifest is not an ancestor of the final Manifest")
        declared_outputs = relative_path_set(result.get("outputs"))
        declared_readback = relative_path_set(result.get("readback_evidence"))
        delivery_paths = relative_path_set(report.get("deliverables")) | relative_path_set(report.get("readback_evidence"))
        if not declared_outputs.issubset(delivery_paths):
            errors.append(f"{stage} Stage Result outputs are not all declared for delivery/read-back")
        if not declared_readback.issubset(relative_path_set(report.get("readback_evidence"))):
            errors.append(f"{stage} Stage Result read-back evidence is not all declared in Delivery Report")
        for artifact_relative in declared_outputs | declared_readback:
            artifact_path = (root / artifact_relative).resolve()
            if root not in artifact_path.parents or not artifact_path.is_file():
                errors.append(f"{stage} Stage Result references missing/out-of-root artifact: {artifact_relative}")
            elif report.get("artifact_hashes", {}).get(artifact_relative) != file_sha256(artifact_path):
                errors.append(f"{stage} Stage Result artifact hash mismatch: {artifact_relative}")
    for stage in ("cad", "blender"):
        review = reviews_by_stage.get(stage, {})
        relative = report.get("stage_results", {}).get(stage)
        if review and review.get("consumed_revisions", {}).get("stage_result") != accepted.get(f"{stage}_result"):
            errors.append(f"{stage} review consumed Stage Result revision mismatch")
        if review and relative not in review.get("reviewed_artifacts", []):
            errors.append(f"{stage} review does not reference its Stage Result")
        if stage == "blender" and review.get("review_contract_version") == "1.0" and report.get("geometry_fidelity") is not None:
            assessment = review.get("geometry_fidelity_assessment", {})
            declared = assessment.get("declared")
            if declared != report.get("geometry_fidelity"):
                errors.append("Blender review fidelity declaration does not match Delivery Report")
            if assessment.get("outcome") != "PASS":
                errors.append("accepted Blender review requires a PASS geometry fidelity assessment")
    cross = reviews_by_stage.get("cross_software", {})
    if cross:
        consumed = cross.get("consumed_revisions", {})
        if consumed.get("cad_result") != accepted.get("cad_result") or consumed.get("blender_result") != accepted.get("blender_result"):
            errors.append("cross-software review did not consume both accepted Stage Results")
        expected_results = {value for value in report.get("stage_results", {}).values() if isinstance(value, str)} if isinstance(report.get("stage_results"), dict) else set()
        if not expected_results.issubset(relative_path_set(cross.get("reviewed_artifacts"))):
            errors.append("cross-software review does not reference both Stage Results")

    state_path = (root / str(report.get("project_state_path", ""))).resolve()
    if state_path.is_file():
        state = load_json(state_path)
        errors += [f"project state: {item}" for item in validate_project_state(state)]
        if state.get("project_id") != report.get("project_id") or state.get("current_state") != "ready_for_delivery":
            errors.append("project state must be ready_for_delivery for this project")
        active = state.get("active_revisions", {})
        for key, value in accepted.items():
            if active.get(key) != value:
                errors.append(f"project state active revision mismatch: {key}")
    render_ref = report.get("render_qa_profile")
    render_profile: dict = {}
    if isinstance(render_ref, dict):
        relative = render_ref.get("path")
        profile_path = _inside_file(root, relative) if isinstance(relative, str) else None
        if profile_path is None:
            errors.append(f"render QA profile is missing or outside project root: {relative}")
        else:
            render_profile = load_json(profile_path)
            errors += [f"{relative}: {item}" for item in validate_render_qa_profile(render_profile)]
            if render_profile.get("revision") != render_ref.get("revision"):
                errors.append("render QA profile revision mismatch")
            if render_ref.get("sha256") != file_sha256(profile_path):
                errors.append("render QA profile SHA-256 mismatch")
            if report.get("artifact_hashes", {}).get(relative) != file_sha256(profile_path):
                errors.append("render QA profile is not covered by artifact_hashes")
    result_specs = (
        ("geometry_fidelity_result", validate_geometry_fidelity_result),
        ("render_qa_result", validate_render_qa_result),
    )
    for field, validator in result_specs:
        ref = report.get(field)
        if not isinstance(ref, dict):
            if report.get("delivery_contract_version") == "2.0":
                errors.append(f"{field} reference is required")
            continue
        relative = ref.get("path")
        result_path = _inside_file(root, relative) if isinstance(relative, str) else None
        if result_path is None:
            errors.append(f"{field} is missing or outside project root: {relative}")
            continue
        result = load_json(result_path)
        errors += [f"{relative}: {item}" for item in validator(result)]
        if result.get("project_id") != report.get("project_id") or result.get("revision") != ref.get("revision"):
            errors.append(f"{field} identity/revision mismatch")
        digest = file_sha256(result_path)
        if ref.get("sha256") != digest or report.get("artifact_hashes", {}).get(relative) != digest:
            errors.append(f"{field} SHA-256 binding mismatch")
        evidence_refs: list[dict] = []
        if field == "geometry_fidelity_result":
            topology = result.get("topology_evidence") if isinstance(result.get("topology_evidence"), dict) else {}
            evidence_refs += [value for value in (topology.get("cad_mesh"), topology.get("blender_mesh")) if isinstance(value, dict)]
            computed_mesh_metrics: dict[str, dict] = {}
            computed_mesh_object_ids: dict[str, set[str]] = {}
            for side, ref_name, metrics_name in (("cad", "cad_mesh", "cad_metrics"), ("blender", "blender_mesh", "blender_metrics")):
                mesh_ref = topology.get(ref_name)
                _, mesh = _load_bound_json(root, mesh_ref)
                if mesh is None:
                    errors.append(f"geometry fidelity {side} canonical mesh is missing, hash-mismatched, or invalid JSON")
                    continue
                mesh_errors, metrics = canonical_mesh_metrics(mesh)
                errors += [f"geometry fidelity {side} canonical mesh: {item}" for item in mesh_errors]
                if mesh.get("project_id") != report.get("project_id"):
                    errors.append(f"geometry fidelity {side} canonical mesh project mismatch")
                source_ref = mesh.get("source")
                if isinstance(source_ref, dict):
                    evidence_refs.append(source_ref)
                if metrics is not None:
                    computed_mesh_metrics[side] = metrics
                    computed_mesh_object_ids[side] = set(metrics.get("objects", {}))
                    if topology.get(metrics_name) != metrics:
                        errors.append(f"geometry fidelity {side} metrics do not equal the canonical mesh recomputation")
            recomputed_topology_match = computed_mesh_metrics.get("cad") == computed_mesh_metrics.get("blender") and len(computed_mesh_metrics) == 2
            if topology.get("topology_match") is not recomputed_topology_match or not recomputed_topology_match:
                errors.append("geometry fidelity topology_match disagrees with the two canonical mesh files")
            measurement_evidence = result.get("measurement_evidence") if isinstance(result.get("measurement_evidence"), dict) else {}
            snapshot_refs = {"cad": measurement_evidence.get("cad_snapshot"), "blender": measurement_evidence.get("blender_snapshot")}
            snapshots: dict[str, dict] = {}
            for side, snapshot_ref in snapshot_refs.items():
                if isinstance(snapshot_ref, dict): evidence_refs.append(snapshot_ref)
                _, snapshot = _load_bound_json(root, snapshot_ref)
                if snapshot is None:
                    errors.append(f"geometry fidelity {side} measurement snapshot is missing, hash-mismatched, or invalid JSON")
                    continue
                snapshot_errors = validate_snapshot(snapshot, f"{side}_snapshot")
                errors += [f"geometry fidelity {side} measurement snapshot: {item}" for item in snapshot_errors]
                if snapshot.get("project_id") != report.get("project_id") or snapshot.get("manifest_revision") != result.get("manifest_revision"):
                    errors.append(f"geometry fidelity {side} measurement snapshot identity/Manifest mismatch")
                snapshots[side] = snapshot
            if len(snapshots) == 2:
                for index, measurement in enumerate(result.get("measurement_results", []) if isinstance(result.get("measurement_results"), list) else []):
                    if not isinstance(measurement, dict): continue
                    object_id, property_name = measurement.get("object_id"), measurement.get("property")
                    if not isinstance(object_id, str) or not isinstance(property_name, str): continue
                    cad_value = _object_value(snapshots["cad"], object_id, property_name)
                    blender_value = _object_value(snapshots["blender"], object_id, property_name)
                    if cad_value is None and "." not in property_name:
                        cad_value = _object_value(snapshots["cad"], object_id, f"critical_dimensions.{property_name}")
                    if blender_value is None and "." not in property_name:
                        blender_value = _object_value(snapshots["blender"], object_id, f"critical_dimensions.{property_name}")
                    if measurement.get("cad_value") != cad_value or measurement.get("blender_value") != blender_value:
                        errors.append(f"geometry fidelity measurement_results[{index}] values do not match the bound CAD/Blender snapshots")
            if result.get("declared") != report.get("geometry_fidelity") or result.get("outcome") != "PASS":
                errors.append("geometry fidelity result does not PASS the Delivery Report declaration")
            consumed_revision = result.get("manifest_revision")
            if consumed_revision not in manifest_ancestors:
                errors.append("geometry fidelity result consumed Manifest is not an ancestor of the final Manifest")
            lineage_entry = next((entry for entry in report.get("manifest_lineage", []) if isinstance(entry, dict) and entry.get("revision") == consumed_revision), None)
            manifest_path = _inside_file(root, lineage_entry.get("path")) if isinstance(lineage_entry, dict) and isinstance(lineage_entry.get("path"), str) else None
            if manifest_path is not None:
                consumed_manifest = load_json(manifest_path)
                expected_ids = {item.get("object_id") for item in consumed_manifest.get("objects", []) if isinstance(item, dict) and isinstance(item.get("object_id"), str)}
                coverage = result.get("controlled_object_coverage") if isinstance(result.get("controlled_object_coverage"), dict) else {}
                declared_ids = set(coverage.get("expected_object_ids", [])) if isinstance(coverage.get("expected_object_ids"), list) else set()
                measured_ids = {item.get("object_id") for item in result.get("measurement_results", []) if isinstance(item, dict) and isinstance(item.get("object_id"), str)}
                if declared_ids != expected_ids:
                    errors.append("geometry fidelity controlled-object coverage does not match its consumed Manifest")
                for side, mesh_ids in computed_mesh_object_ids.items():
                    if mesh_ids != expected_ids:
                        errors.append(f"geometry fidelity {side} canonical mesh object IDs do not exactly match the consumed Manifest")
                if not expected_ids.issubset(measured_ids):
                    errors.append("geometry fidelity measurements do not cover every controlled Manifest object")
                measured_properties = {(item.get("object_id"), item.get("property")) for item in result.get("measurement_results", []) if isinstance(item, dict)}
                required_checks = " ".join(str(value).lower() for value in (result.get("declared", {}).get("required_checks", []) if isinstance(result.get("declared"), dict) else []))
                for obj in consumed_manifest.get("objects", []):
                    if not isinstance(obj, dict) or not isinstance(obj.get("object_id"), str): continue
                    object_id = obj["object_id"]
                    dimensions = obj.get("critical_dimensions", {})
                    for key in dimensions if isinstance(dimensions, dict) else []:
                        if (object_id, f"critical_dimensions.{key}") not in measured_properties and (object_id, key) not in measured_properties:
                            errors.append(f"geometry fidelity missing critical dimension {object_id}.{key}")
                    for token, prop in (("transform", "transform"), ("parent", "parent_id")):
                        if token in required_checks and (object_id, prop) not in measured_properties:
                            errors.append(f"geometry fidelity missing required {prop} check for {object_id}")
                if "topology" in required_checks and not isinstance(result.get("topology_evidence", {}).get("cad_metrics"), dict):
                    errors.append("geometry fidelity missing structured topology verification")
        else:
            evidence_refs += [value for value in result.get("images", []) if isinstance(value, dict)] if isinstance(result.get("images"), list) else []
            profile = result.get("profile") if isinstance(result.get("profile"), dict) else None
            if profile is not None:
                evidence_refs.append(profile)
                if profile != render_ref:
                    errors.append("render QA result profile does not match the Delivery Report render QA profile")
            minimum = render_profile.get("minimum_resolution") if isinstance(render_profile.get("minimum_resolution"), dict) else {}
            camera_records: list[dict] = []
            for image in result.get("images", []) if isinstance(result.get("images"), list) else []:
                if isinstance(image, dict) and isinstance(minimum.get("width_px"), int) and isinstance(minimum.get("height_px"), int):
                    if image.get("width_px", 0) < minimum["width_px"] or image.get("height_px", 0) < minimum["height_px"]:
                        errors.append(f"render QA image is below profile minimum resolution: {image.get('path')}")
                    image_path = _inside_file(root, image.get("path")) if isinstance(image.get("path"), str) else None
                    actual_dimensions = _png_dimensions(image_path) if image_path is not None else None
                    if actual_dimensions is None or actual_dimensions != (image.get("width_px"), image.get("height_px")):
                        errors.append(f"render QA PNG dimensions do not match actual file: {image.get('path')}")
                    metadata = image.get("camera_metadata")
                    if not isinstance(metadata, dict):
                        errors.append(f"render QA image lacks hash-bound camera metadata: {image.get('path')}")
                    else:
                        evidence_refs.append(metadata)
                        _, camera_data = _load_bound_json(root, metadata)
                        required_camera_fields = {"schema_version", "artifact_type", "project_id", "revision", "image", "camera_name", "projection", "view_matrix", "render_settings", "subject_frame_fraction", "excluded_collections", "output_role"}
                        if camera_data is None:
                            errors.append(f"render QA camera metadata is missing, hash-mismatched, or invalid JSON: {metadata.get('path')}")
                        elif not required_camera_fields.issubset(camera_data):
                            errors.append(f"render QA camera metadata lacks required fields: {metadata.get('path')}")
                        else:
                            image_binding = camera_data.get("image")
                            settings = camera_data.get("render_settings")
                            matrix = camera_data.get("view_matrix")
                            fraction = camera_data.get("subject_frame_fraction")
                            exclusions = camera_data.get("excluded_collections")
                            if camera_data.get("schema_version") != "1.0" or camera_data.get("artifact_type") != "render_camera_metadata" or camera_data.get("project_id") != report.get("project_id") or not isinstance(camera_data.get("revision"), int) or isinstance(camera_data.get("revision"), bool):
                                errors.append(f"render QA camera metadata identity is invalid: {metadata.get('path')}")
                            if not isinstance(image_binding, dict) or image_binding.get("path") != image.get("path") or image_binding.get("sha256") != image.get("sha256"):
                                errors.append(f"render QA camera metadata does not bind its image: {metadata.get('path')}")
                            if camera_data.get("camera_name") != image.get("camera") or camera_data.get("projection") not in {"perspective", "orthographic"}:
                                errors.append(f"render QA camera metadata camera/projection is invalid: {metadata.get('path')}")
                            if not isinstance(matrix, list) or len(matrix) != 16 or any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in matrix):
                                errors.append(f"render QA camera metadata view_matrix must contain 16 finite numbers: {metadata.get('path')}")
                            if not isinstance(settings, dict) or settings.get("width_px") != image.get("width_px") or settings.get("height_px") != image.get("height_px") or not isinstance(settings.get("engine"), str) or not settings.get("engine"):
                                errors.append(f"render QA camera metadata render settings do not match the image: {metadata.get('path')}")
                            if not isinstance(fraction, (int, float)) or isinstance(fraction, bool) or not math.isfinite(fraction) or not 0 <= fraction <= 1:
                                errors.append(f"render QA camera metadata subject_frame_fraction is invalid: {metadata.get('path')}")
                            if not isinstance(exclusions, list) or any(not isinstance(value, str) or not value for value in exclusions):
                                errors.append(f"render QA camera metadata excluded_collections is invalid: {metadata.get('path')}")
                            if camera_data.get("output_role") not in {"clearance_inspection", "presentation_render"}:
                                errors.append(f"render QA camera metadata output_role is invalid: {metadata.get('path')}")
                            camera_records.append(camera_data)
            deterministic_ids = {item.get("check_id") for item in result.get("deterministic_checks", []) if isinstance(item, dict)}
            deterministic = {item.get("check_id"): item for item in result.get("deterministic_checks", []) if isinstance(item, dict)}
            profile_deterministic = set(render_profile.get("deterministic_checks", [])) if isinstance(render_profile.get("deterministic_checks"), list) else set()
            deterministic_map = {"camera_projection": "plan_projection", "declared_output_separation": "output_separation"}
            required_deterministic = {deterministic_map.get(item, item) for item in profile_deterministic} | {"subject_frame_fraction", "clean_overview_exclusions", "camera_metadata"}
            if not required_deterministic.issubset(deterministic_ids):
                errors.append("render QA deterministic checks do not completely cover the Profile")
            subject = deterministic.get("subject_frame_fraction", {})
            required_fraction = render_profile.get("subject_min_frame_fraction")
            role_counts = {role: sum(item.get("output_role") == role for item in camera_records) for role in ("presentation_render", "clearance_inspection")}
            if len(camera_records) != 2 or role_counts != {"presentation_render": 1, "clearance_inspection": 1}:
                errors.append("render QA requires exactly one presentation_render and one clearance_inspection camera metadata record")
            presentation = next((item for item in camera_records if item.get("output_role") == "presentation_render"), {})
            clearance = next((item for item in camera_records if item.get("output_role") == "clearance_inspection"), {})
            measured_fraction = presentation.get("subject_frame_fraction")
            if not isinstance(required_fraction, (int, float)) or isinstance(required_fraction, bool) or subject.get("measured") != measured_fraction or not isinstance(measured_fraction, (int, float)) or isinstance(measured_fraction, bool) or measured_fraction < required_fraction or subject.get("required") != required_fraction:
                errors.append("render QA subject frame fraction does not satisfy the Profile")
            plan = deterministic.get("plan_projection", {})
            required_projection = render_profile.get("required_views", {}).get("plan_view_projection") if isinstance(render_profile.get("required_views"), dict) else None
            if plan.get("measured") != clearance.get("projection") or plan.get("measured") != required_projection or plan.get("required") != required_projection:
                errors.append("render QA plan projection does not match the Profile")
            clean = deterministic.get("clean_overview_exclusions", {}).get("measured")
            required_exclusions = render_profile.get("required_views", {}).get("clean_overview_excludes", []) if isinstance(render_profile.get("required_views"), dict) else []
            if not isinstance(clean, list) or clean != presentation.get("excluded_collections") or not set(required_exclusions).issubset(set(clean)):
                errors.append("render QA clean overview exclusions do not match the Profile")
            separated = deterministic.get("output_separation", {}).get("measured")
            derived_outputs = {item.get("output_role"): item.get("image", {}).get("path") for item in camera_records if isinstance(item.get("image"), dict)}
            if not isinstance(separated, dict) or separated != derived_outputs or set(separated) != {"clearance_inspection", "presentation_render"} or len(set(separated.values())) != 2:
                errors.append("render QA clearance and presentation outputs are not structurally separate")
            if deterministic.get("camera_metadata", {}).get("passed") is not True or len(camera_records) != len(result.get("images", [])):
                errors.append("render QA camera_metadata check is not backed by every image")
            manual_ids = {item.get("check_id") for item in result.get("manual_checks", []) if isinstance(item, dict)}
            required_manual = {"label_readability", "exposure", "brightness", "contrast", "legend_or_labels", "clean_overview"}
            if not required_manual.issubset(manual_ids):
                errors.append("render QA manual checks do not completely cover the Profile")
            for item in result.get("manual_checks", []) if isinstance(result.get("manual_checks"), list) else []:
                if isinstance(item, dict) and isinstance(item.get("evidence"), list): evidence_refs += [ref for ref in item["evidence"] if isinstance(ref, dict)]
            if result.get("outcome") != "PASS":
                errors.append("render QA result is not PASS")
        for evidence_ref in evidence_refs:
            evidence_relative = evidence_ref.get("path")
            evidence_path = _inside_file(root, evidence_relative) if isinstance(evidence_relative, str) else None
            if evidence_path is None or evidence_ref.get("sha256") != file_sha256(evidence_path):
                errors.append(f"{field} evidence hash mismatch: {evidence_relative}")
            elif report.get("artifact_hashes", {}).get(evidence_relative) != file_sha256(evidence_path):
                errors.append(f"{field} evidence is not covered by Delivery Report artifact_hashes: {evidence_relative}")
    ledger_ref = report.get("hash_ledger")
    if isinstance(ledger_ref, dict):
        relative = ledger_ref.get("path")
        ledger_path = _inside_file(project, relative) if isinstance(relative, str) else None
        if ledger_path is None:
            errors.append(f"outer hash ledger is missing or outside project root: {relative}")
        else:
            ledger = load_json(ledger_path)
            errors += [f"{relative}: {item}" for item in validate_hash_ledger(ledger)]
            if ledger.get("self_path") != relative:
                errors.append("hash ledger self_path does not match Delivery Report reference")
            if report_path is None:
                errors.append("report_path is required to verify the outer hash ledger")
            else:
                resolved_report = report_path.resolve()
                if project not in resolved_report.parents or not resolved_report.is_file():
                    errors.append("Delivery Report file is missing or outside project root")
                else:
                    report_relative = resolved_report.relative_to(project).as_posix()
                    if ledger.get("covered_report", {}).get("path") != report_relative or ledger.get("covered_report", {}).get("sha256") != file_sha256(resolved_report):
                        errors.append("outer hash ledger does not bind this Delivery Report")
                    if report_relative in report.get("artifact_hashes", {}):
                        errors.append("Delivery Report artifact_hashes must not contain itself")
            entries = ledger.get("entries", {})
            if accepted_prefix is not None:
                required_ledger_paths = {f"{accepted_prefix}/{path}" for path in report.get("artifact_hashes", {})}
            else:
                required_ledger_paths = set(report.get("artifact_hashes", {}))
            if report_path is not None and report_path.resolve().is_file() and project in report_path.resolve().parents:
                required_ledger_paths.add(report_path.resolve().relative_to(project).as_posix())
            if not required_ledger_paths.issubset(set(entries)):
                errors.append("outer hash ledger does not cover the Delivery Report and every declared artifact hash")
            for entry_path, expected in entries.items():
                if entry_path == relative:
                    errors.append("outer hash ledger illegally contains itself")
                    continue
                actual_path = _inside_file(project, entry_path)
                if actual_path is None or file_sha256(actual_path) != expected:
                    errors.append(f"outer hash ledger entry mismatch: {entry_path}")
            if report.get("delivery_contract_version") == "3.0" and report_path is not None and not errors:
                errors += _validate_release_package(report, project, report_path.resolve(), ledger_path, package_path_override)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Delivery Report and every hashed artifact under its project root.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = load_json(args.path)
        errors = validate_files(report, args.project_root, args.path)
        warnings = legacy_warnings(report)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
        warnings = []
    print(json.dumps({"valid": not errors, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
