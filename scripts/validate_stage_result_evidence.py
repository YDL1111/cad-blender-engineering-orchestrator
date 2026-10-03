from __future__ import annotations

import argparse
import json
from pathlib import Path

from artifact_validation import load_json, validate_stage_result
from validate_delivery_report import validate_stage_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Stage Result and its promoted attempt/completion receipt evidence.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--legacy-unverified", action="store_true", help="Migration analysis only; never returns formal validity")
    args = parser.parse_args()
    legacy = False
    try:
        result = load_json(args.path)
        legacy = result.get("evidence_contract_version") != "1.0"
        errors = validate_stage_result(result) + validate_stage_evidence(result, args.project_root.resolve(), allow_legacy_unverified=args.legacy_unverified)
        warnings = ["legacy Stage Result: migration analysis only; no Work Packet/attempt/receipt authentication"] if legacy else []
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        errors, warnings = [str(exc)], []
    formal_valid = not errors and not legacy
    print(json.dumps({"valid": formal_valid, "migration_analysis_valid": not errors, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    return 0 if formal_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
