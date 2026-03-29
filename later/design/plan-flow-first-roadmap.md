# Flow-First Layering Roadmap

Date: 2026-03-28
Status: Draft

## Summary

The target architecture is simple:

- `run_flow(...)` is the primary interface.
- Flows execute where storage lives.
- `get`, `put`, `find`, `tag`, and related operations are thin wrappers over flow execution.
- Rendering, filtering, context assembly, and similar behavior belong to flow projections.
- User-editable system docs define semantics by role.
- Local concerns like daemon lifecycle, scanning, watching, and queue management are outside the semantic core and call flows instead of reimplementing them.

This roadmap is split into four grouped detail plans:

1. [plan-flow-host-primary-interface.md](/Users/hugh/play/keep/later/design/plan-flow-host-primary-interface.md)
2. [plan-canonical-flow-surface.md](/Users/hugh/play/keep/later/design/plan-canonical-flow-surface.md)
3. [plan-projections-and-system-doc-roles.md](/Users/hugh/play/keep/later/design/plan-projections-and-system-doc-roles.md)
4. [plan-local-integrations-and-runtime-stability.md](/Users/hugh/play/keep/later/design/plan-local-integrations-and-runtime-stability.md)

## Ordering

The units should land in order.

- Unit 1 establishes the primary interface and removes the local/remote split above `run_flow`.
- Unit 2 makes canonical flows the source of truth for essential operations and server transport.
- Unit 3 moves semantic shaping into projections and role-driven system-doc resolution.
- Unit 4 isolates local integrations, then hardens the daemon/runtime around the simplified interface and locks it down with contract tests.

## Conditions Of Satisfaction

- Hosted and local backends expose one stable semantic interface: `run_flow`.
- Essential memory behavior is implemented once, in the flow system.
- System-doc-controlled behavior does not depend on fixed IDs.
- The client layer does not own semantics.
- Local runtime and integration code does not bypass hosted-compatible flow execution.
