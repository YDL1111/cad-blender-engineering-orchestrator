# Non-circular hash protocol

The Delivery Report hashes its declared artifacts but never itself and never the outer ledger. After the report is finalized, create `final-hash-ledger` from `assets/templates/hash-ledger.json`; it hashes the Delivery Report and all declared artifact hashes. The ledger never contains its own path and lists that path under `excluded_paths` with the reason.

For contract 3.0, ledger artifact entries use project-relative `delivery/accepted/rN/...` paths while the Delivery Report keeps release-relative artifact paths. The ledger covers the external `delivery/delivery-report-rN.json` plus every accepted file. It explicitly excludes itself, `working/`, and `package-rN.zip`. Working copies never enter an old ledger and changes there never invalidate a published release.

This hierarchy is intentional: accepted artifacts are covered by the report, and the immutable report is covered by the outer ledger. No file may claim its own digest, and no report/ledger pair may hash each other. Contract 3.0 cannot omit or rename its rN ledger. `validate_delivery_report.py` verifies report-to-ledger binding, actual accepted-file hashes, coverage, explicit exclusions, absence of self-reference, and exact ZIP membership/content against the accepted snapshot, report, and ledger. Legacy reports remain migration inputs, not new immutable releases.
