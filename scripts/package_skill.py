"""Build and inspect a deterministic, text-only local Skill candidate."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path


SKILL_NAME = "cad-blender-engineering-orchestrator"
ROOT_FILES = {"SKILL.md", "README.md", "LICENSE", "CHANGELOG.md", "VERSION", ".gitignore", "release-files.txt"}
DIRECTORY_SUFFIXES = {
    "agents": {".yaml"},
    "assets": {".json"},
    "references": {".md"},
    "scripts": {".py"},
    "docs": {".md"},
    "examples": {".md"},
}
LOCAL_REGRESSION_SCRIPTS = {"real_project_regression.py", "real_migration_regression.py"}
PRIVATE_PATH = re.compile(r"[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\s'\"]+", re.IGNORECASE)
UNC_PATH = re.compile(r"\\\\[^\\/\s]+[\\/][^\\/\s]+[\\/][^\s'\"]+")
SECRET = re.compile(r"(?:github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect_files(source: Path) -> dict[str, bytes]:
    source = source.resolve()
    allowlist = source / "release-files.txt"
    if not allowlist.is_file():
        raise ValueError("source must contain release-files.txt")
    listed = allowlist.read_text(encoding="utf-8").splitlines()
    if len(listed) != len(set(listed)) or any(not name or name.strip() != name for name in listed):
        raise ValueError("release-files.txt contains duplicate or malformed paths")
    if not ROOT_FILES.issubset(listed):
        raise ValueError("release-files.txt omits a required root file")
    files: dict[str, bytes] = {}
    for name in sorted(listed):
        relative = Path(name)
        parts = relative.parts
        if relative.is_absolute() or ".." in parts or "\\" in name or relative.as_posix() != name:
            raise ValueError(f"invalid release path: {name}")
        path = source / relative
        allowed = (len(parts) == 1 and relative.name in ROOT_FILES) or (
            len(parts) > 1 and parts[0] in DIRECTORY_SUFFIXES and path.suffix.lower() in DIRECTORY_SUFFIXES[parts[0]]
        )
        if parts[0] == "assets" and (len(parts) != 3 or parts[1] not in {"contracts", "templates"}):
            allowed = False
        if parts[0] == "scripts" and relative.name in LOCAL_REGRESSION_SCRIPTS:
            allowed = False
        if not allowed or not path.is_file():
            raise ValueError(f"missing or prohibited release file: {name}")
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != source and source in parent.parents):
            raise ValueError(f"symlink is not allowed in release: {relative.as_posix()}")
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError(f"release text file exceeds 2 MiB: {relative.as_posix()}")
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"release file is not UTF-8 text: {relative.as_posix()}") from exc
        legacy_root = "D:" + "/CBEngineeringProjects"
        normalized_text = text.replace("\\\\", "\\").replace("\\/", "/")
        personal_name = Path.home().name
        named_person = bool(personal_name and re.search(rf"(?<![A-Za-z0-9]){re.escape(personal_name)}(?![A-Za-z0-9])", normalized_text))
        if PRIVATE_PATH.search(normalized_text) or UNC_PATH.search(text) or UNC_PATH.search(normalized_text) or SECRET.search(normalized_text) or named_person or legacy_root in normalized_text or legacy_root.replace("/", "\\") in normalized_text:
            raise ValueError(f"release file contains a personal path or credential pattern: {relative.as_posix()}")
        files[relative.as_posix()] = data
    return files


def build_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, data in sorted(files.items()):
            info = zipfile.ZipInfo(f"{SKILL_NAME}/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def inspect_zip(payload: bytes, files: dict[str, bytes]) -> list[str]:
    errors: list[str] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        expected = [f"{SKILL_NAME}/{relative}" for relative in sorted(files)]
        if names != expected:
            errors.append("ZIP names, order, or top-level Skill directory differ from the manifest")
        for relative, data in files.items():
            name = f"{SKILL_NAME}/{relative}"
            if name not in names or archive.read(name) != data:
                errors.append(f"ZIP member does not match source: {relative}")
    return errors


def package(source: Path, dist: Path) -> dict:
    files = collect_files(source)
    version = files["VERSION"].decode("utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.]+)?", version):
        raise ValueError("VERSION must be a valid release candidate version")
    payload = build_zip(files)
    errors = inspect_zip(payload, files)
    if errors:
        raise ValueError("package validation failed: " + "; ".join(errors))
    dist.mkdir(parents=True, exist_ok=True)
    zip_path = dist / f"{SKILL_NAME}-{version}.zip"
    if zip_path.exists() and zip_path.read_bytes() != payload:
        raise FileExistsError(f"different candidate already exists at {zip_path}; change VERSION before repackaging")
    if not zip_path.exists():
        zip_path.write_bytes(payload)
    manifest = {
        "version": version,
        "skill_directory": SKILL_NAME,
        "package": zip_path.name,
        "package_sha256": sha256(payload),
        "files": [{"path": path, "sha256": sha256(data), "size": len(data)} for path, data in sorted(files.items())],
    }
    manifest_path = dist / f"file-manifest-{version}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "version": version,
        "valid": True,
        "zip_file": zip_path.name,
        "sha256": sha256(payload),
        "file_count": len(files),
        "top_level_skill_md": f"{SKILL_NAME}/SKILL.md",
        "excluded_categories": ["cache/pyc", "engineering artifacts", "renders", "delivery ZIPs", "logs", "unknown files"],
        "privacy_scan": "pattern_scan_passed; explicit allowlist requires human release review",
        "license_status": "MIT" if files["LICENSE"].decode("utf-8").startswith("MIT License\n") else "owner_decision_pending",
    }
    report_path = dist / f"package-acceptance-{version}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an offline deterministic Skill release candidate.")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    source = args.source_root.resolve()
    try:
        report = package(source, (args.dist or source / "dist").resolve())
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
