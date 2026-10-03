# Workflow and gates

The authoritative states and exact `next_allowed_states` mapping live only in `assets/contracts/state-machine.json`. Inspect or validate that JSON instead of copying a transition table into work products. It includes the normal path, clarification gate, CAD/Blender rework branches, delivery state, and terminal state.

Only the orchestrator changes project state after validating evidence. Revision branches return to the associated in-progress state with a new work packet. `clarification_required`, revision states, and `NEEDS_USER_DECISION` are gates, not failures to conceal.

After `ready_for_delivery`, enter `publishing_delivery`, create and validate a non-overwriting accepted release, then enter `delivered`. User display/view changes occur only in `working/`. Promoting them requires a new planning and controlled-stage cycle, not a file copy into an existing release.

At each transition record timestamp, actor, source revision, outputs, validator results, reviewer outcome, unresolved items, and next allowed states. Never skip the baseline or start CAD-dependent Blender work before `cad_accepted`.
