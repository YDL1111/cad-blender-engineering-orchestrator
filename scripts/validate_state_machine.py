from __future__ import annotations

import argparse
import json
from pathlib import Path

from artifact_validation import STATE_MACHINE_PATH, STATES, TRANSITIONS, load_json, validate_project_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the single-source state machine and optionally a Project State artifact.")
    parser.add_argument("--project-state", type=Path)
    args = parser.parse_args()
    errors: list[str] = []
    try:
        definition = load_json(STATE_MACHINE_PATH)
        declared = definition.get("states", {})
        if set(declared) != STATES:
            errors.append("loaded state set differs from state-machine.json")
        for state, targets in declared.items():
            if set(targets) != TRANSITIONS.get(state):
                errors.append(f"loaded transitions differ for {state}")
        if args.project_state:
            errors += validate_project_state(load_json(args.project_state))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors, "definition": str(STATE_MACHINE_PATH)}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
