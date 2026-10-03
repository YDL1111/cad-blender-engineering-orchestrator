from __future__ import annotations

import argparse
import json
from pathlib import Path

from artifact_validation import VALIDATORS, legacy_warnings, load_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate any authoritative orchestrator JSON artifact by artifact_type.")
    parser.add_argument("path", help="Path to JSON artifact")
    args = parser.parse_args()
    try:
        data = load_json(args.path)
        artifact_type = data.get("artifact_type")
        if artifact_type in {"project_manifest", "cad_work_packet", "blender_work_packet"}:
            raise ValueError("this artifact requires cross-file validation; use validate_project_manifest.py or validate_work_packet.py")
        if artifact_type not in VALIDATORS:
            raise ValueError(f"unsupported artifact_type: {artifact_type!r}")
        errors = VALIDATORS[artifact_type](data)
        warnings = legacy_warnings(data)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
        warnings = []
    if errors:
        print(json.dumps({"valid": False, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"valid": True, "artifact": str(Path(args.path).resolve()), "warnings": warnings}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
