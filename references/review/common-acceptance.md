# Common acceptance

Run deterministic validators first, then independent judgment. Begin from a validated, immutable `review_work_packet` whose inputs are hash-bound and whose exact writer scope is limited to review output/evidence. Verify artifact type/schema, required outputs, unique IDs, units, coordinates, revision lineage, writer scope, attempt/receipt promotion, independent read-back evidence, unresolved blockers, and acceptance-criteria coverage.

Review outcome gates: `PASS` advances; `CONDITIONAL_PASS` advances only when all conditions are recorded and do not block the dependent stage; `FAIL` returns for revision; `NEEDS_USER_DECISION` stops for orchestrator-led clarification.
