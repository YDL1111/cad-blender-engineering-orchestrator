"""Non-destructively restore an immutable accepted release from a verified historical ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from artifact_validation import load_json
from publish_delivery import publish_release
from validate_delivery_report import validate_files


MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_MEMBER_BYTES = 8 * 1024**3
MAX_ZIP_TOTAL_BYTES = 20 * 1024**3


def _validated_members(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ValueError(f"ZIP contains more than {MAX_ZIP_ENTRIES} entries")
    if sum(info.file_size for info in infos) > MAX_ZIP_TOTAL_BYTES:
        raise ValueError("ZIP declared uncompressed size exceeds the migration limit")
    seen: set[str] = set()
    members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for info in infos:
        member = PurePosixPath(info.filename.replace("\\", "/"))
        unsafe_segment = any(part in {"", ".", ".."} or part.rstrip(" .") != part or part.split(".", 1)[0].upper() in reserved for part in member.parts)
        if member.is_absolute() or unsafe_segment or ":" in info.filename:
            raise ValueError(f"ZIP contains unsafe path: {info.filename}")
        canonical = "/".join(part.casefold() for part in member.parts)
        if canonical in seen:
            raise ValueError(f"ZIP contains duplicate or Windows-colliding path: {info.filename}")
        seen.add(canonical)
        if info.file_size > MAX_ZIP_MEMBER_BYTES:
            raise ValueError(f"ZIP member exceeds migration size limit: {info.filename}")
        if info.flag_bits & 0x1:
            raise ValueError(f"encrypted ZIP members are unsupported: {info.filename}")
        file_type = (info.external_attr >> 16) & 0o170000
        if file_type not in {0, 0o040000, 0o100000}:
            raise ValueError(f"ZIP contains unsupported special file: {info.filename}")
        members.append((info, member))
    return members


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    for info, member in _validated_members(archive):
        target = (destination / member.as_posix()).resolve()
        if destination.resolve() not in target.parents:
            raise ValueError(f"ZIP path escapes extraction root: {info.filename}")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def _find_report(root: Path) -> Path:
    candidates = sorted((root / "delivery").glob("delivery-report-*.json"))
    if len(candidates) != 1:
        raise ValueError("historical ZIP must contain exactly one delivery/delivery-report-*.json")
    return candidates[0]


def inspect_historical_zip(zip_path: Path) -> dict:
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="cad-blender-zip-check-") as temp:
        extracted = Path(temp) / "extracted"
        extracted.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            _safe_extract(archive, extracted)
        report_path = _find_report(extracted)
        report = load_json(report_path)
        errors = validate_files(report, extracted, report_path, zip_path)
        return {"valid": not errors, "sha256": digest, "report": report_path.relative_to(extracted).as_posix(),
                "entry_count": sum(1 for path in extracted.rglob("*") if path.is_file()), "errors": errors}


def migrate(zip_path: Path, project_root: Path, release: int, apply: bool, target_root: Path | None = None) -> dict:
    project_root = project_root.resolve()
    target = (target_root or project_root).resolve()
    inspection = inspect_historical_zip(zip_path)
    working_candidates = {
        "cad": [path.relative_to(project_root).as_posix() for path in sorted((project_root / "cad").rglob("*.FCStd"))],
        "blender": [path.relative_to(project_root).as_posix() for path in sorted((project_root / "blender").rglob("*.blend"))],
    }
    plan = {"zip": inspection, "release": release, "accepted_target": f"delivery/accepted/r{release}",
            "working_target": "working", "working_sources": working_candidates, "destructive_actions": [], "applied": False}
    if not inspection["valid"]:
        return plan
    if not apply:
        return plan
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cad-blender-migrate-") as temp:
        extracted = Path(temp) / "extracted"
        extracted.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            _safe_extract(archive, extracted)
        report_path = _find_report(extracted)
        result = publish_release(extracted, report_path, target, release, working_source_root=project_root, read_only_hint=True)
    published_report = target / result["report"]
    post_errors = validate_files(load_json(published_report), target, published_report)
    hashed_paths = set(load_json(published_report).get("artifact_hashes", {}))
    working_in_hashes = any(path.replace("\\", "/").casefold().startswith("working/") for path in hashed_paths)
    plan.update({"applied": True, "publish_result": result, "accepted_validation_errors": post_errors,
                 "working_excluded_from_hashes": not working_in_hashes,
                 "boundary_valid": not post_errors and not working_in_hashes and (target / f"delivery/accepted/r{release}").is_dir() and (target / "working").is_dir()})
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or apply a non-destructive historical delivery migration.")
    parser.add_argument("--source-zip", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True, help="Existing project; current FCStd/blend are copied only to working/")
    parser.add_argument("--release", type=int, default=1)
    parser.add_argument("--target-root", type=Path, help="Alternate destination, primarily for isolated regression tests")
    parser.add_argument("--apply", action="store_true", help="Create new accepted/working paths; never overwrites an existing release")
    args = parser.parse_args()
    try:
        result = migrate(args.source_zip.resolve(), args.project_root, args.release, args.apply, args.target_root)
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(json.dumps({"valid": False, "applied": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["zip"]["valid"] and (not args.apply or result.get("boundary_valid") is True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
