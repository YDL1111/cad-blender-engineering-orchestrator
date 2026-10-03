"""Publish a validated delivery into an immutable accepted/rN release and editable working copies."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from artifact_validation import file_sha256, load_json
from validate_delivery_report import validate_files


def _safe_relative(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts) or ":" in normalized:
        raise ValueError(f"unsafe project-relative path: {value}")
    return path.as_posix()


def _source_paths(report: dict[str, Any], source_root: Path, report_path: Path) -> set[str]:
    paths = {_safe_relative(path) for path in report.get("artifact_hashes", {}) if isinstance(path, str)}
    ledger_ref = report.get("hash_ledger") if isinstance(report.get("hash_ledger"), dict) else {}
    ledger_relative = ledger_ref.get("path")
    if isinstance(ledger_relative, str):
        ledger_path = (source_root / _safe_relative(ledger_relative)).resolve()
        if ledger_path.is_file() and source_root in ledger_path.parents:
            ledger = load_json(ledger_path)
            paths.update(_safe_relative(path) for path in ledger.get("entries", {}) if isinstance(path, str))
    report_relative = report_path.resolve().relative_to(source_root).as_posix()
    excluded = {report_relative, _safe_relative(ledger_relative)} if isinstance(ledger_relative, str) else {report_relative}
    selected: set[str] = set()
    for relative in paths - excluded:
        folded = relative.casefold()
        if folded.startswith("working/") or folded.startswith("delivery/accepted/") or folded.endswith(".zip"):
            continue
        source = (source_root / relative).resolve()
        if source_root not in source.parents or not source.is_file():
            raise FileNotFoundError(f"accepted source artifact is missing or outside source root: {relative}")
        selected.add(relative)
    if not selected:
        raise ValueError("source Delivery Report does not identify any accepted artifacts")
    return selected


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_release(source_root: Path, source_report_path: Path, target_root: Path, release_revision: int) -> tuple[dict[str, Any], Path, Path, Path]:
    report = load_json(source_report_path)
    source_errors = validate_files(report, source_root, source_report_path)
    if source_errors:
        raise ValueError("source delivery is not valid: " + "; ".join(source_errors))
    accepted_prefix = f"delivery/accepted/r{release_revision}"
    accepted_root = target_root / accepted_prefix
    report_relative = f"delivery/delivery-report-r{release_revision}.json"
    ledger_relative = f"delivery/final-hash-ledger-r{release_revision}.json"
    package_relative = f"delivery/package-r{release_revision}.zip"
    for path in (accepted_root, target_root / report_relative, target_root / ledger_relative, target_root / package_relative):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite published release path: {path}")
    accepted_root.mkdir(parents=True, exist_ok=False)
    selected = _source_paths(report, source_root, source_report_path)
    for relative in sorted(selected):
        destination = accepted_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)
    published = dict(report)
    published.update({
        "delivery_contract_version": "3.0",
        "accepted_release": {"revision": release_revision, "path": accepted_prefix, "immutable": True},
        "working_copy_policy": {"path": "working", "excluded_from_delivery_hashes": True, "default_open_location": True},
        "hash_ledger": {"path": ledger_relative, "coverage": "delivery_report_and_declared_artifacts"},
        "package": {"path": package_relative, "release_revision": release_revision},
        "artifact_hashes": {relative: file_sha256(accepted_root / relative) for relative in sorted(selected)},
    })
    report_path = target_root / report_relative
    _write_json(report_path, published)
    ledger = {
        "schema_version": "1.0", "artifact_type": "hash_ledger", "project_id": published.get("project_id"), "revision": release_revision,
        "self_path": ledger_relative, "coverage": "delivery_report_and_declared_artifacts",
        "accepted_release": {"revision": release_revision, "path": accepted_prefix},
        "covered_report": {"path": report_relative, "sha256": file_sha256(report_path)},
        "entries": {report_relative: file_sha256(report_path), **{f"{accepted_prefix}/{path}": digest for path, digest in published["artifact_hashes"].items()}},
        "excluded_paths": [
            {"path": ledger_relative, "reason": "A hash ledger never contains its own digest."},
            {"path": "working", "reason": "Editable working copies are outside immutable delivery evidence."},
            {"path": package_relative, "reason": "The package contains this ledger and cannot hash itself without a cycle."},
        ],
    }
    ledger_path = target_root / ledger_relative
    _write_json(ledger_path, ledger)
    return published, accepted_root, report_path, ledger_path


def _create_package(target_root: Path, release_revision: int, accepted_root: Path, report_path: Path, ledger_path: Path) -> Path:
    package = target_root / "delivery" / f"package-r{release_revision}.zip"
    with zipfile.ZipFile(package, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(accepted_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(target_root).as_posix())
        archive.write(report_path, report_path.relative_to(target_root).as_posix())
        archive.write(ledger_path, ledger_path.relative_to(target_root).as_posix())
    return package


def _create_working_copies(working_source_root: Path, target_root: Path) -> list[str]:
    created: list[str] = []
    for stage, extension in (("cad", ".fcstd"), ("blender", ".blend")):
        candidates = sorted((working_source_root / stage).rglob(f"*{extension}")) if (working_source_root / stage).is_dir() else []
        for source in candidates:
            destination = target_root / "working" / stage / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                continue
            shutil.copy2(source, destination)
            created.append(destination.relative_to(target_root).as_posix())
    return created


def _validate_release_progression(source_report: dict[str, Any], target_root: Path, release_revision: int) -> None:
    if release_revision == 1:
        return
    previous_path = target_root / "delivery" / f"delivery-report-r{release_revision - 1}.json"
    if not previous_path.is_file():
        raise ValueError(f"release r{release_revision} requires existing delivery-report-r{release_revision - 1}.json")
    previous = load_json(previous_path)
    previous_errors = validate_files(previous, target_root, previous_path)
    if previous_errors:
        raise ValueError("previous immutable release failed authentication: " + "; ".join(previous_errors))
    if previous.get("delivery_contract_version") != "3.0" or previous.get("accepted_release", {}).get("revision") != release_revision - 1:
        raise ValueError("previous release is not a valid adjacent immutable delivery")
    if source_report.get("project_id") != previous.get("project_id"):
        raise ValueError("new release project_id does not match the previous release")
    if not isinstance(source_report.get("revision"), int) or isinstance(source_report.get("revision"), bool) or source_report["revision"] <= previous.get("revision", 0):
        raise ValueError("new release requires a newer Delivery Report revision")
    current_revisions = source_report.get("accepted_revisions") if isinstance(source_report.get("accepted_revisions"), dict) else {}
    previous_revisions = previous.get("accepted_revisions") if isinstance(previous.get("accepted_revisions"), dict) else {}
    for key in ("requirements", "manifest", "cad_result", "blender_result"):
        if not isinstance(current_revisions.get(key), int) or current_revisions.get(key, 0) < previous_revisions.get(key, 0):
            raise ValueError(f"new release cannot regress accepted_revisions.{key}")
    if not any(current_revisions.get(key, 0) > previous_revisions.get(key, 0) for key in ("cad_result", "blender_result")):
        raise ValueError("new release requires a newer formal CAD or Blender Stage Result")


def _validate_built_release(published: dict[str, Any], target_root: Path, report_path: Path) -> None:
    errors = validate_files(published, target_root, report_path)
    if errors:
        raise ValueError("published release failed validation: " + "; ".join(errors))


def _remove_owned_tree(path: Path) -> None:
    def onerror(function, value, _exc):
        os.chmod(value, stat.S_IWRITE)
        function(value)
    shutil.rmtree(path, onerror=onerror)


def publish_release(source_root: Path, source_report_path: Path, target_root: Path, release_revision: int,
                    working_source_root: Path | None = None, read_only_hint: bool = True) -> dict[str, Any]:
    if release_revision < 1:
        raise ValueError("release revision must be >= 1")
    source_root, target_root = source_root.resolve(), target_root.resolve()
    source_report_path = source_report_path.resolve()
    source_report = load_json(source_report_path)
    _validate_release_progression(source_report, target_root, release_revision)
    with tempfile.TemporaryDirectory(prefix="cad-blender-publish-") as temp:
        preview_root = Path(temp) / "project"
        preview_root.mkdir()
        preview, preview_accepted, preview_report, preview_ledger = _build_release(source_root, source_report_path, preview_root, release_revision)
        _create_package(preview_root, release_revision, preview_accepted, preview_report, preview_ledger)
        _validate_built_release(preview, preview_root, preview_report)
    published: dict[str, Any] | None = None
    accepted_root = target_root / "delivery" / "accepted" / f"r{release_revision}"
    report_path = target_root / "delivery" / f"delivery-report-r{release_revision}.json"
    ledger_path = target_root / "delivery" / f"final-hash-ledger-r{release_revision}.json"
    initially_absent = {path: not path.exists() for path in (accepted_root, report_path, ledger_path, target_root / "delivery" / f"package-r{release_revision}.zip")}
    try:
        published, accepted_root, report_path, ledger_path = _build_release(source_root, source_report_path, target_root, release_revision)
        package = _create_package(target_root, release_revision, accepted_root, report_path, ledger_path)
        _validate_built_release(published, target_root, report_path)
        working = _create_working_copies((working_source_root or accepted_root).resolve(), target_root)
        if read_only_hint:
            for path in accepted_root.rglob("*"):
                if path.is_file():
                    os.chmod(path, stat.S_IREAD)
        return {"published": True, "release_revision": release_revision, "accepted_path": accepted_root.relative_to(target_root).as_posix(),
                "report": report_path.relative_to(target_root).as_posix(), "ledger": ledger_path.relative_to(target_root).as_posix(),
                "package": package.relative_to(target_root).as_posix(), "working_copies_created": working,
                "working_excluded_from_hashes": True, "read_only_hint_applied": read_only_hint}
    except Exception:
        for path in (report_path, ledger_path, target_root / "delivery" / f"package-r{release_revision}.zip"):
            if initially_absent[path] and path.exists():
                os.chmod(path, stat.S_IWRITE)
                path.unlink()
        if initially_absent[accepted_root] and accepted_root.exists():
            _remove_owned_tree(accepted_root)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a non-overwriting immutable delivery release and editable working copies.")
    parser.add_argument("report", type=Path, help="Validated source Delivery Report")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--release", type=int, required=True)
    parser.add_argument("--no-readonly-hint", action="store_true")
    args = parser.parse_args()
    try:
        result = publish_release(args.project_root, args.report, args.project_root, args.release, read_only_hint=not args.no_readonly_hint)
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(json.dumps({"published": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
