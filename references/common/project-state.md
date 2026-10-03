# Project State

`project-state.json` is a journal pointer, not a replacement for artifact history. It records current state, active immutable revisions, last transition, next allowed states, blockers, and active writer locks.

`assets/contracts/state-machine.json` is the single definition of allowed states and exact transitions. Validators load it directly; documentation does not maintain a second transition table. Run `scripts/validate_state_machine.py --project-state <path>` to check both the definition and a state artifact.

Before granting a work packet, confirm no other writer owns its target files. Release the lock only after artifacts are closed, read back, and recorded. A crash or ambiguous lock becomes `blocked` until the orchestrator confirms ownership; never guess that a file is safe to overwrite.

Contract 3.0 transitions `ready_for_delivery -> publishing_delivery -> delivered`. A publishing failure returns to `ready_for_delivery` without claiming a release. A requested formal revision starts a new planning/work-packet cycle and publishes a new accepted revision; it never reopens or replaces a prior accepted directory. Pre-3.0 state journals that transition directly from ready to delivered remain readable for migration.

New state journals record `working_copy_root: working` and append one `{revision, path}` entry to `published_releases` for every immutable `delivery/accepted/rN`. Validators retain compatibility with older journals that omit these fields, but reject malformed, duplicate, traversing, or non-canonical release entries when the fields are present.
