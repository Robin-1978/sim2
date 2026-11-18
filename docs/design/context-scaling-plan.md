# Context Scaling for a Growing World

Status: **implemented and verified (G-08)**
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

### Implemented result

The contract-driven broad probe was rerun on the remote provider after the
projection change:

| Metric | Before | After |
| --- | --- | --- |
| Complete core | 1/1 | 1/1 |
| Legal / violation chain | 1/1 / 1/1 | 1/1 / 1/1 |
| Accepted / rejected proposals | 15 / 0 | 15 / 0 |
| Prompt characters | about 15,900 | 14,423 to 15,396 |
| Estimated prompt tokens | 3,983 | 3,606 to 3,849 |
| Omitted sections | action/behavior catalogs and four reference sections | none |
| `omitted_required` | not recorded | `[]` |

The rendered catalog tags are retained for prompt-order compatibility, but they
now contain a bounded inventory plus the target neighborhood, not the full
catalog. A test with 100 Actions, 100 behaviors and 100 legacy catalog entries
stays under 16,000 characters and does not render the distant entries.

Additional implementation details:

- `query_world_index` pages by cell, keyword and offset with a hard 20-item cap.
- `inspect_definition` returns one Action, behavior tree or rule and reports
  ambiguous shorthand instead of guessing.
- `structural_progress` in instance metadata stores the stage boundary and a
  bounded accepted-proposal summary, so a retry resumes at the right stage.
- Structural history keeps proposal names, not full definitions, and keeps
  only the last eight exchanges; the immediate native tool message still
  carries the exact result.
- `context_metrics` records `source_sections`, `required_sections` and
  `omitted_required` for audit.

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

## 8. Function schema, cache and iteration budgets

The prompt is not the only model input. Function schemas are sent on every
request, before the messages, and a large schema is part of the cacheable
prefix rather than the dynamic prompt text.

Measured God contract schema (remote dynamic probe, tools
`query_history, query_knowledge_gaps, propose_entity, propose_definition,
propose_world_change, wait`):

| Schema | Before | After |
| --- | --- | --- |
| Full contract | 14,510 chars (~3,627 tokens) | 10,755 chars (~2,684 tokens) |
| `propose_definition` | 7,264 chars | 5,207 chars |
| `propose_world_change` | 4,390 chars | 3,394 chars |

All model-facing schema descriptions are now at most 120 characters, with no
truncated `...` text. Field names, types, enums, required lists and nested
structure remain unchanged; only explanatory prose was shortened.

The staged-contract prompt projection was also reduced: in the 100-entry test
world the proposal-contract section fell from 4,997 to 641 chars and the whole
prompt from 9,838 to 5,443 chars. A real audited staged God prompt fell from
14,769 to 7,748 chars:

| Section | Before | After |
| --- | --- | --- |
| Proposal contract | 5,403 | 641 |
| World outcomes | 3,778 | 358 |
| Protocol | 2,120 | 297 |
| Rule condition fields | 1,091 | 139 |
| Tool-name list | 450 | 176 |
| World trends | 575 | 209 |

The same audit found that a raw contract used the author-facing `tools` key
while the engine only read `allowed_tools`; the session therefore exposed the
entire 19-tool catalog. Accepting both keys reduced the God suffix schema from
20,321 to 10,755 chars. The NPC prompt also fell from 10,020 to 8,216 chars:
behavior blocks 3,097→1,750, duplicate attribute rules removed and
unconditional world rules compacted. These are role-specific projections, not
global truncation.

Final remote contract-driven broad run after all projection changes:

| Metric | Old broad | Final broad |
| --- | --- | --- |
| God calls | 16 | 15 |
| Prompt chars | 14,423–15,396 | 7,615–8,577 |
| Prompt tokens | 7,737–9,666 | 4,731–7,161 |
| Accepted / rejected | 15 / 0 | 15 / 0 |
| Cached prompt tokens | 60.8% | 71.5% |
| Schema suffix | 20,321 chars | 10,755 chars |

The remaining prompt is mostly the exact stage instruction, current target
index, recent history and the compact platform protocol. The remaining suffix
is the static six-tool contract schema.

### Cache behavior

Provider cache data from the real remote runs:

| Role | Calls | Cached prompt tokens | Average cache share |
| --- | --- | --- | --- |
| God | 53 | 380,416 | 79.5% |
| `agent_decision` | 10 | 21,888 | 55.1% |
| Story | 8 | 12,544 | 47.7% |

`static_prefix_hash` was one value per role in these runs. The cached token
count is larger than the rendered static prefix because the provider caches
the function schemas and system message together with the stable prompt head.
This is why schema should be **stable per role/task class and optimized once**,
not rebuilt from dynamic context on every turn. Dynamic schema filtering is
useful only at a stable task-class boundary (for example the instant-interaction
tool set), because changing the schema invalidates the cacheable prefix for the
next call.

### Iteration and read budgets

The low decomposed-probe limits were a probe artifact, not the product
configuration: 3 iterations and 1 read caused repeated `read_budget` and
`tool_budget` rejections. The probe now uses the real God budget: 24 iterations,
40 tool calls and 4 focused reads. Product budgets remain:

| Role | Iterations | Reads | Reason |
| --- | --- | --- | --- |
| God structural | 24 | 4 | Several atomic stages, each with a bounded read before one proposal |
| `agent_decision` | 8 | 3 | One or two reads, then one choice |
| Story | 8 | 4 | Evidence plus one narrative continuation |
| Director | 1 call | 0 | Select one cell; it is not a ReAct session |

The LLM Director is a single `complete_json` call:

- system: `Director structural planning` (28 chars);
- user context: target list plus current cell counts (193 chars for the single
  `resource` target in the dynamic probe, 255 chars when all five cells are
  configured);
- tools: none;
- output: one `target_cell` and a short reason.

It does not receive entities, events, memories or tool schemas. The
`max_agent_iterations` / `max_read_tool_calls` values in `roles.director` are
therefore inert for this path; only the completion-token limit applies.

Raising the God iteration budget does not raise context size; the read budget
and the compact stage prompt bound the loop.

## 9. Non-goals

- Raising the global 16k cap as the primary solution.
- Semantic search over every entity on every turn.
- Letting the model write arbitrary SQL or read unvalidated state.
- Replacing the stage contract; the contract remains the decomposition
  authority.
