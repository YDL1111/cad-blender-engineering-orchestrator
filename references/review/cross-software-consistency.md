# Cross-software consistency

Export structured CAD and Blender snapshots from the shared Snapshot contract. Each snapshot records the Manifest revision it actually consumed; CAD and Blender may cite different revisions when both are verified ancestors of the accepted final Manifest. Run:

From the loaded Skill directory: `python scripts/compare_cad_blender.py <cad-snapshot.json> <blender-snapshot.json> --manifest <accepted-final-manifest.json> --manifest-lineage <delivery-report-or-lineage.json> --baseline <confirmed-baseline.json> --project-root <project-root> --report <new-report.json>`. The explicit final lineage is required when neither historical snapshot contains the complete chain to the supplied final Manifest.

Both snapshots follow [the shared Snapshot contract](../common/snapshot-contract.md). Obtain tolerance from the accepted Manifest; never rely on an implicit default. The comparator verifies immutable Manifest lineage, IDs and parent relations, units, coordinate system, typed critical dimensions, and transforms. Strings such as `DN100` and `+X`, booleans, arrays, objects, and null retain their JSON types; tolerance applies only to finite numbers. The independent reviewer then determines impact and disposition; a textual import-success message is insufficient.

