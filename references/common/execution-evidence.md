# Execution attempts and completion receipts

Record each CAD or Blender execution under `<stage>/attempts/<packet-id>/<attempt-id>/`. Use `assets/templates/attempt-record.json` for command/interface, timestamps, exit code, log paths or summary, expected/actual outputs, hashes, failure classification, and promotion status. A small failed CLI attempt remains an attempt record; it does not become a Stage Result.

A successful attempt also emits `completion_receipt.json` from the corresponding template. The receipt binds packet, attempt, application/interface, completed status, output paths and SHA-256 values, writer process identity, and a distinct read-back process identity. Reopen/read-back must run in a second process or an equivalently independent process identity.

Only an attempt with exit code zero, no failure classification, all expected output hashes, a completed receipt, and independently reverified outputs may be referenced by a Stage Result with `evidence_contract_version: 1.0`. Validate the binding with `scripts/validate_stage_result_evidence.py <stage-result> --project-root <root>`. Legacy Stage Results remain readable but emit an explicit warning that attempt/receipt binding is absent.

`verified_by_log_hashes` requires writer and read-back logs to be hash-bound `process_log` JSON artifacts whose PID, host, process start, executable, and command digest exactly match process evidence. Plain text or mismatched logs may only support `declared` assurance, which cannot promote a Stage Result.
