# Uncertainty and blocking

Statuses are `confirmed`, `assumed`, `unknown`, `conflict`, and `blocked`. Confidence is a number from 0 to 1. Every non-confirmed key value explains its source/reason and lists affected stages in `blocks`.

- `assumed`: reversible working value with explicit rationale; critical assumptions require user acceptance before dependent work.
- `unknown`: missing value; block only dependent work.
- `conflict`: sources disagree; cite both and request a decision.
- `blocked`: work cannot proceed safely or reproducibly; state the missing capability/fact and recovery evidence.

Child agents return blockers to the orchestrator. The orchestrator consolidates them into the smallest decision set for the user.

