---
name: cad-blender-engineering-orchestrator
description: Orchestrate dimensioned engineering work in which CAD is the authoritative controlled geometry and Blender is a downstream presentation model, with versioned requirements, handoffs, independent reviews, and CAD-to-Blender consistency checks. Use for architecture/interior, MEP coordination, mechanical/product, or facility-layout requests that require both CAD and Blender; do not use for CAD-only edits, Blender-only art, pure 2D diagrams, simulation, or licensed engineering approval.
---

# CAD to Blender Engineering Orchestrator

Act as the only user-facing orchestrator. Do not model directly from the raw request. First analyze inputs, expose uncertainty, obtain or record a requirements baseline, then create the Project Manifest. Resolve the project root using [configuration](docs/configuration.md): explicit user or CLI path, `CBE_PROJECTS_ROOT`, user config, then `~/CBEngineeringProjects`. Keep formal project data under that root, never in this skill or an application install directory.

## Non-negotiable controls

- Use JSON as the sole authoritative structured format. Mark key facts `confirmed`, `assumed`, `unknown`, `conflict`, or `blocked`; never silently invent a critical dimension, structure, diameter, slope, or equipment parameter.
- CAD owns controlled dimensions and engineering geometry. Blender consumes accepted CAD geometry for materials, lighting, cameras, and presentation and must preserve controlled IDs, units, coordinates, dimensions, and assembly relations.
- The orchestrator alone owns the master Manifest. A child agent returns files, evidence, unresolved items, and a Manifest Patch; it never edits the master Manifest concurrently. One writer per engineering file at a time.
- Handoffs are immutable and versioned. Do not start CAD before `baseline_confirmed`; do not start CAD-dependent Blender work before CAD review is accepted.
- Ask the user only through the orchestrator. Stop at a blocked gate or unresolved Critical/Important review item.
- A successful command is not evidence of a successful artifact. Require read-back, file inspection, or structured comparison.
- Treat stage directories as controlled production space, `delivery/accepted/rN` as immutable published evidence, and `working/` as disposable editable copies. After delivery, direct users to FCStd/blend under `working/`; never open accepted files for routine display, camera, visibility, or material edits.
- Never overwrite an accepted release. A working-copy change becomes formal only through a new Work Packet, Stage Result, required reviews, and a new `delivery/accepted/rN+1` publication.

## Route and run

1. Read [requirements analysis](references/common/requirement-analysis.md), then establish the [requirements baseline](references/common/requirements-baseline.md).
2. Select exactly one primary domain and read only its guide: [architecture/interior](references/domains/architecture-interior.md), [MEP coordination](references/domains/mep-coordination.md), [mechanical/product](references/domains/mechanical-product.md), or [facility layout](references/domains/facility-layout.md).
3. Read [workflow and state gates](references/common/workflow.md), [Project Manifest](references/common/project-manifest.md), and [work packets](references/common/work-packet.md). Create a project with `scripts/scaffold_project.py` only after the user authorizes project creation.
4. Before choosing tools, run `scripts/check_environment.py` and perform the read-only checks in [capability detection](references/tools/capability-detection.md). Read the relevant [CAD](references/tools/cad-interface.md) and [Blender](references/tools/blender-interface.md) contracts. A located executable does not prove export or independent read-back capability.
5. Delegate a context-limited CAD task using [CAD operator](references/agents/cad-operator.md), then an independent read-only review using the applicable files under `references/review/`.
6. After CAD acceptance, delegate Blender using [Blender operator](references/agents/blender-operator.md), review it independently, and run [cross-software consistency](references/review/cross-software-consistency.md). For generated evidence, load the [Snapshot contract](references/common/snapshot-contract.md), [execution evidence](references/common/execution-evidence.md), and project-specific [render quality](references/common/render-quality.md).
7. Use [Engineering Reviewer](references/agents/engineering-reviewer.md) for judgment beyond deterministic scripts. Finish under the [workspace layout](references/common/workspace.md), [delivery contract](references/common/delivery-contract.md), and [non-circular hash protocol](references/common/hash-ledger.md).

For uncertainty, version relationships, or state recovery, read [uncertainty and blocking](references/common/uncertainty-and-blocking.md), [versioning and handoff](references/common/versioning-and-handoff.md), and [project state](references/common/project-state.md).

Review routing: every stage uses `common-acceptance`; `architecture_interior` and `facility_layout` add `spatial-layout-review`; `mep_coordination` adds `mep-routing-review`; `mechanical_product` adds `mechanical-review`; CAD and Blender stages add their respective model review, and final coordination always adds `cross-software-consistency`.

Resolve every `scripts/...` and `assets/...` path relative to this loaded Skill directory, not the current project directory.
