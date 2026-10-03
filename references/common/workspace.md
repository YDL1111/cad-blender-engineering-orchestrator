# Workspace and published copies

Stage work remains under `cad/`, `blender/`, `work-packets/`, `reviews/`, `manifests/`, and the other controlled project directories. These are production inputs to gates, not the post-delivery files users should casually edit.

Published storage is append-only:

```text
delivery/
  accepted/r1/                 immutable snapshot with preserved project-relative layout
  accepted/r2/                 later formal release; never replaces r1
  delivery-report-rN.json
  final-hash-ledger-rN.json
  package-rN.zip
working/
  cad/                         editable FCStd copies
  blender/                     editable blend copies
```

The Delivery Report resolves every artifact path inside its declared `accepted/rN`. The outer ledger uses project-relative accepted paths. Neither mechanism authenticates `working/`. Default opening instructions point to `working/cad` and `working/blender`; changes to display state, view, camera, material, or visibility there do not alter old evidence.

Use `scripts/publish_delivery.py` for a new release. It fails if any rN report, ledger, ZIP, or accepted directory already exists. An rN+1 source must carry a newer Delivery Report and at least one newer formal CAD/Blender Stage Result than rN. A read-only filesystem attribute may be applied to accepted files as a warning, but only hashes establish integrity.

For a historical project, first run `scripts/migrate_delivery_layout.py --source-zip <historical-zip> --project-root <project> --release 1` without `--apply`. This verifies the ZIP and reports the intended paths without writing. After review, add `--apply`: the historical ZIP supplies accepted/r1, while current FCStd/blend files supply working copies. The command never deletes or overwrites historical evidence. Validate the new report, package, accepted snapshot, and the explicit exclusion of working files before using the migrated layout.
