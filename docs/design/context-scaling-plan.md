# Context Scaling for a Growing World

Status: **design accepted, implementation pending (G-08)**
Related ledger: [social-authority-loop-goal.md](social-authority-loop-goal.md)

## 1. Observed problem

The God prompt is already close to its compaction limit before the world is
large:

| Metric | Broad staged God run |
| --- | --- |
| Prompt limit | 16,000 chars |
| Observed prompt | about 15,900 chars (compacted) |
| Estimated tokens | 3,540 to 3,989 |
| Static prefix | about 2,887 chars |
| Dynamic section | about 13,000 chars |
| Omitted | `world_action_catalog`, `world_behavior_catalog`, `world_outcomes`, `world_trends`, and other configured sections |

Compaction keeps latency bounded, but as the world grows it hides more of the
catalog God needs to avoid duplicate or contradictory proposals. Increasing
the character limit is not a scalable answer: it raises cost and latency and
eventually exceeds any model window.

## 2. Design goals

1. Keep the structural prompt bounded as the number of entities, Actions,
   behaviors and rules grows.
2. Show the model everything relevant to the current structural target.
3. Keep full history and full catalogs in SQLite, not in every prompt.
4. Make omissions observable and testable.
5. Preserve content neutrality: no world names or social primitives in
   Platform code.

## 3. Proposed projection

Replace the full catalog projection with a relevance-scoped
`StructuralContext`:

| Layer | Contents | Typical size |
| --- | --- | --- |
| World summary | cell counts, active season plan, dead/empty content, recent structural changes | tens of lines |
| Cell index | IDs and names only for the current target cell, plus counts for every other cell | bounded by target cell |
| Target detail | current resource/entity, steward, related behaviors, authorization rules, recent events | one target's neighborhood |
| Stage progress | current stage, completed stages, missing semantic requirements | one checklist |
| Recent changes | last N structural events only | bounded |
| On-demand reads | paged `query_world_index` and `inspect_definition` calls | read budget |

The full `world_action_catalog` and `world_behavior_catalog` should no longer
be rendered. They become queryable indexes.

## 4. Storage and services

1. **World index query**: a read tool that filters by cell, entity type, name
   prefix, or ID and returns ID/name/label/effect summary with a hard page
   limit.
2. **Structural progress ledger**: persist accepted proposal IDs, current
   stage, completed stages and missing requirements. Render only the compact
   checklist.
3. **Relevance resolver**: Platform computes the target neighborhood once per
   structural task:
   - target entity and location;
   - steward and delegation relationships;
   - behaviors with effects on the target;
   - authorization rules for the target or behavior IDs;
   - recent events for the target.
4. **Omission telemetry**: record which sections were compacted. If a section
   contains the current target and was omitted, fail the context build or force
   a bounded retrieval instead of silently hiding it.

## 5. Role budgets

Keep per-role budgets, but make them relevance-aware rather than fixed
full-catalog budgets:

| Role | Prompt budget | Context policy |
| --- | --- | --- |
| `god` structural | 4k tokens | world summary + target neighborhood + stage checklist |
| `story` | 2k tokens | current evidence + audience + active plan |
| `agent_decision` | 1k tokens | location, needs, executable/blocked choices, relevant memories |
| `director` | 1k tokens | cell counts, active plan, recent structural changes |

The existing `max_agent_iterations` and `max_read_tool_calls` limits remain.

## 6. Acceptance tests

1. A world with 100 Actions and 100 behaviors keeps the God structural prompt
   under its token budget.
2. The prompt contains the current target's related behaviors and rules, but
   not the full catalog.
3. `query_world_index` returns bounded, paged results.
4. Omission telemetry reports no omitted section that contains the current
   target.
5. A staged broad run still completes 15/15 proposals and both chains after
   the projection change.
6. The architecture content-neutral guard still passes.

## 7. Migration order

1. Add `query_world_index` and `inspect_definition` read tools.
2. Add the persisted structural progress ledger.
3. Add the relevance resolver and `StructuralContext` projection behind a
   feature flag.
4. Switch `god_structural` to the new projection; keep the old projection for
   one regression run.
5. Remove the full catalog sections from the structural prompt.
6. Run the 100-Action/100-behavior budget test and the staged broad probe.

## 8. Non-goals

- Raising the global 16k cap as the primary solution.
- Semantic search over every entity on every turn.
- Letting the model write arbitrary SQL or read unvalidated state.
- Replacing the stage contract; the contract remains the decomposition
  authority.
