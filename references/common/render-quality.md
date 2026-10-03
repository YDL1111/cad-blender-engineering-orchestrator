# Render QA profile

Use a project-specific `render_qa_profile` from `assets/templates/render-qa-profile.json`; do not copy threshold values blindly between projects. It records minimum resolution, minimum subject frame fraction, label pixel height or a required manual readability gate, exposure/brightness/contrast checks, a clean overview, orthographic plan views, legend/label requirements, and separate clearance-inspection versus presentation outputs.

Deterministic checks cover measurable metadata such as resolution, declared camera projection, and output separation. Independent visual review covers framing, real label readability, exposure, contrast, occlusion, and whether at least one clean overview excludes maintenance envelopes and obstructing routing. Passing deterministic checks never substitutes for the manual visual gate.

Results use stable `check_id` values and cover every Profile requirement. Read PNG dimensions from the actual file. Bind camera/projection metadata and every manual-review evidence item with `{path, sha256}`; delivery validation verifies existence, digest, and hash-ledger coverage.

Results use stable `check_id` values and cover every Profile requirement. Read PNG dimensions from the actual file. Bind camera/projection export metadata and every manual-review evidence item with `{path, sha256}`; delivery validation verifies existence, digest, and hash-ledger coverage.
