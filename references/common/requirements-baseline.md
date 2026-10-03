# Requirements Baseline

The baseline freezes interpreted intent before planning. Use `assets/templates/requirements-baseline.json`, keep source paths explicit, and increment integer `revision` for an approved change. Required content: goals, exclusions, inputs, deliverables, requirements, unresolved items, and confirmation evidence.

A baseline is `baseline_confirmed` only when every requirement that blocks CAD has status `confirmed` or an explicitly user-accepted `assumed` status. `unknown`, `conflict`, or `blocked` may remain only when `blocks` is empty for the next stage. Validate with:

From the loaded Skill directory: `python scripts/validate_requirements_baseline.py <file>`
