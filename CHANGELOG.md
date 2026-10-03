# Changelog

## 0.1.0 - 2026-10-03

- First public release, authored by YDL1111 and licensed under MIT.
- Promote the rc.9 runtime validated with FreeCAD 1.1.3 and Blender 5.2.1 LTS; independently reopen saved CAD and Blender files and compare three controlled objects (268 vertices, 524 triangles) with zero cross-software differences.
- Preserve rc.9 geometry, evidence, review, and immutable-delivery gates; finalize installation links and release metadata.
- Report the license selected for a packaged release instead of always reporting a pending decision.
- Working files are not overwritten on later publication; existing same-name copies may remain from an earlier release. Create a separate working copy from the desired accepted release when needed.

## 0.1.0-rc.9 - local candidate

- Derive routine working copies only from the accepted release snapshot, not unaccepted stage models. Preserve explicit historical migration overrides.

## 0.1.0-rc.8 - local candidate

- Add a symmetric readback-process event-window regression test to the real-software provenance gate.

## 0.1.0-rc.7 - local candidate

- Keep malformed timezone-free process-log timestamps as structured validation errors rather than raising during the new event-window comparison.

## 0.1.0-rc.6 - local candidate

- Reject process-log events that postdate the corresponding writer attempt or independent readback receipt. This gate was exposed by the real-software smoke test.

## 0.1.0-rc.5 - local candidate

- Resolve project roots from explicit input, environment, user config, or home.
- Detect FreeCAD and Blender without installing software or changing projects.
- Replace machine-specific template paths with substitution markers.
- Add documentation, deterministic packaging, privacy checks, and portable tests.
- Reject JSON-escaped personal paths and UNC paths in release content; omit historical project-specific regression runners from the ZIP.
- Keep package acceptance metadata machine-independent and exercise complete configuration precedence.
- Use an explicit release file allowlist and block binaries whose version probe fails.
- Make fresh-profile manual and future GitHub installs create the skills parent directory.
- Preserve existing evidence, review, geometry, and accepted/working contracts.
