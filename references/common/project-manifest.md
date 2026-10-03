# Project Manifest

The Manifest is created after the confirmed baseline and is the cross-software contract. Use `assets/templates/project-manifest.json`. Revision 1 may be checked with `python scripts/validate_project_manifest.py <manifest> --baseline <baseline>`. Revision 2 and later require authenticated files: `python scripts/validate_project_manifest.py <manifest> --baseline <baseline> --project-root <project-root> --manifest-lineage <lineage-json>`. `--legacy-unverified` is migration analysis only and never produces formal validity.

It carries `schema_version`, `project_id`, integer `revision`, `domain`, `target_level`, `units`, `coordinate_system`, `source_files`, `requirements_revision`, `objects`, `systems`, `constraints`, `assumptions`, `unresolved_items`, `stage_status`, `expected_outputs`, and `review_profiles`. Every controlled object has a stable unique `object_id`, type, parent/assembly relation, critical dimensions, and source/status metadata.

Only the orchestrator applies a reviewed Manifest Patch. Preserve prior manifests as immutable files such as `manifest-v1.json`; never edit a handed-off revision in place.
