# Architecture Convergence Plan

## Purpose

The current Runtime has the right causal direction but too many overlapping
orchestration paths. The next phase is structural convergence: reduce the
number of lifecycle owners, remove compatibility branches, and make Platform
services depend on explicit protocols.

This plan applies to the current development branch. Compatibility with
retired schemas is not a goal. A definition or protocol is either migrated to
the new form or rejected during development.

## Authoritative execution plan (2026-11-14)

This section is the working plan for the current branch. It replaces an
implicit list of refactors with ordered deliverables and observable gates.
The order matters: a later stage must not hide a failure in an earlier stage
by adding another adapter, retry loop, or world-specific branch.

## Architecture review and concrete execution plan

### Latest review: 2026-11-14

The framework is not too large because it has many files. The core split is
reasonable: persistence, Platform mutation, activity execution, agent
protocols, prompt projection and world content have different reasons to
change. The current risk is duplicated ownership inside those boundaries.
The largest modules are still concentrated (`activity.py` 1,482 lines,
`engine.py` 937, `definition_runtime.py` 916 and `social.py` 840), and the
same contract is represented in several places: YAML, the definition compiler,
AgentView prose, tool schemas, quality checks and instance validation. That is
architectural debt rather than useful domain complexity.

The review found four kinds of patch-shaped behavior:

1. **Harness assumptions.** The real evolution probe selected
   `event_group:research_synthesis` after its first round. A configured
   `relay_stability_review` task became ready but was never consumed, so the
   NPC repeated `Inspect Archive` and the harness reported a false absorbing
   state. The probe now selects the oldest ready God task from the configured
   wake graph; it does not name an event family.
2. **Contract duplication.** Definition structure, model schemas, prompt
   examples and Platform validation are still partly hand-maintained in
   parallel. The canonical compiler must emit the normalized definition and
   the model-facing contract; Platform should add only instance checks such as
   reachability and conservation.
3. **Role and world leakage.** A few role branches and lexical categories
   remain in generic agent quality/projection code. They should become
   configured evidence types and consumer registrations. A new world must not
   require changing `quality.py`, `renderer.py` or Platform because it calls a
   resource something other than food.
4. **Large lifecycle owners.** `ActivityService` and `AgentEngine` still own
   several concerns. They should be reduced by moving only stable protocol
   boundaries (activity lifecycle/effects and session/protocol/audit), not by
   adding more façade layers. The goal is fewer owners, not a lower line count
   achieved through indirection.

The architecture is therefore **usable but not yet converged**. The causal
direction is correct; the remaining work is to remove duplicate contracts and
make every configured continuation observable and executable. The following
plan is the active implementation order. Each item has a test or audit gate;
no item is considered complete because a tool call returned successfully.

| Order | Concrete change | Acceptance evidence |
|---|---|---|
| 1 | Make the evolution probe consume any ready configured God/Story task, and add a regression fixture with two distinct wake families. | The probe cannot skip a ready task because of a task-type literal; the 10-round JSONL shows each consumed task and its source event. |
| 2 | Make `WorldEvent → EvolutionTask` the only wake path. Keep role names in configuration/registry; remove new `if role == ...` dispatch branches. | Duplicate delivery is idempotent, restart requeues claimed tasks, and every Story/God call has a committed source event. |
| 3 | Finish the canonical definition compiler. Generate normalized persistence, tool JSON Schema and AgentView catalog from one definition model. | An unrelated world with different attributes, resources and objects boots and a God-created Action plus bound generic BT is immediately executable. |
| 4 | Move quality and prompt semantics from world/role literals to evidence and configuration. Remove lexical food checks, acceptance waivers and duplicate research instructions. | The same groundedness tests pass for non-food names; provider errors and rejected intents remain explicit and are never rewritten to `wait`. |
| 5 | Finish the bounded agent boundary: Session owns ReAct history, Protocol owns provider normalization/call IDs, Registry owns schemas/permissions, Audit owns redacted evidence. | Prompt prefix/suffix sizes, cache usage, latency, tool arguments, result and event IDs are recorded without hidden reasoning or secrets. |
| 6 | Keep Activity as the sole process owner, but split only its stable lifecycle/effect concerns; do not create parallel Goal/BT/Action managers. | One integration test traces `Intent → Activity → Outcome → WorldEvent → EvolutionTask` through restart and rollback. |
| 7 | Run the corrected 10-round DeepSeek probe, then the deterministic week and benchmark. Fix only generic/configured liveness defects. | Three or more later rounds create distinct reachable content, NPC choices change, no unexplained absorbing state remains, and performance is measured. |

The stop rule is strict: if a failure is fixed by a new world-name branch,
legacy alias, role-specific quality exemption, token-limit increase or a
second wake mechanism, stop and move the fix back to the canonical contract
or world configuration.

### Verification after this review

- The corrected DeepSeek causal probe passed 10/10 rounds. Its redacted
  evidence is `/tmp/sim2-evolution-canonical-final2/`; the failure in
  `/tmp/sim2-evolution-canonical-final/` is retained as the harness-selection
  regression that led to the fix.
- The focused evolution suite passed 94 tests, the vertical slice passed 201
  tests, and the current full suite has **415 tests** after adding the
  canonical-tool and Story → God projection regressions. The full run passed
  the existing 413 tests; the two newly added tests also pass independently.
- `python -m compileall -q src scripts` and `git diff --check` pass.
- The 1,000-agent benchmark completed five runs with p95 tick time
  **142.976 ms**, no failed agents, and world revision 10 after the tick.
- The prompt projection regression test confirms that a real God world change
  keeps `<world_outcomes>` and its before/after evidence when compaction is
  necessary.
- Institution ledgers now accept only the canonical `selectors`,
  `event_types`, `behavior_ids` and `action_ids` fields; the old alias shape
  is rejected before persistence.
- Reflection quality matching no longer carries a food/patrol/craft keyword
  dictionary. It derives evidence from the configured outcome's result,
  summary, behavior and goal tags, with a bounded generic inflection match.
- Story activation now persists its public fact, goal and audience in the
  world state/event, and the God projection includes bounded active Story
  threads. This closes the previously missing Story → God causal input that
  caused a real ten-round run to wait while the NPC repeated inspection.
- Agent task wording now lives with role policies (`agent_view/policies.py`);
  the bounded session engine schedules and executes it without owning role
  semantics.
- The bounded model-facing tool-result projection now lives in `agents/session.py`;
  AgentEngine retains only scheduling, protocol calls and private provenance.
- Final Intent normalization, role permissions and Platform submission now live
  in `agents/intent_gateway.py`; AgentEngine no longer owns the mutation
  dispatch table.

### Review conclusion

The architecture is directionally sound, but the implementation is still
larger than it needs to be because several concerns have temporarily been
represented twice:

- `Intent`, BT state, activity state and evolution tasks can all look like the
  owner of one action unless the Activity record is treated as the single
  execution owner.
- Definition structure, model JSON Schema, AgentView text and Platform
  validation have had overlapping contracts.
- Event publication and Story/God wake creation have had more than one path.
- Agent session control, provider protocol handling, tool policy and audit
  persistence were concentrated in a large engine before being split.
- World-specific meaning has occasionally appeared as aliases, fallback
  resolvers or quality exceptions instead of configuration and generic
  primitives.

This is architectural debt, not evidence that the service split itself is
wrong. Adding another adapter or another special case would make the debt
permanent. The target is a smaller set of owners with one causal path, not a
larger framework.

### Function-size pass (2026-11-17)

Five functions of 440–740 lines each mixed independent concerns. They were
split behind their existing public entry points without changing behavior:

| Entry point | Before (HEAD) | Now |
|---|---:|---|
| `validate_world_template` | 491 lines in `bootstrap/service.py` | `bootstrap/template_validation.py`: `_TemplateChecks.run()` calls one check per template section; the longest check is 51 lines |
| `AgentIntentGateway.submit` | 516 lines | 29 lines; intent-type resolution is a chain of small steps and every intent type has a registered handler |
| `submit_story_beat` | 444 lines | 31 lines; ten gates that return a rejection or `None`, then `_commit_beat` |
| `AgentViewCompiler.build_decision_view` | 603 lines | 106 lines; `_decision_frame`, `_compile_behavior` (`_behavior_gates`, `_simulate_behavior`, `_target_guidance`, `_behavior_entry`) and one method per remaining view section; the longest new method is 87 lines |
| `render_context` | 735 lines | 35-line orchestrator over 24 section functions plus `_strip_world_scaffolding` and `_fit_to_limit`; the longest section is 58 lines |

Equivalence was checked before each old copy was deleted. The pre-change
function was kept as a temporary module and compared with the new one on
randomized and directed inputs, including inputs that raise: 27k template
variants, 180k intent responses, about 1,000 story-beat submissions and, on
the five shipped worlds, 3,780 decision views and 93k rendered prompts showed
no difference in results or exceptions. The temporary modules are deleted.
The permanent guards are focused tests beside each protocol:
`tests/test_story_beat_gates.py`, `tests/test_decision_view_gates.py` (every
reason a behavior is not offered) and `tests/test_prompt_render_sections.py`
(section order per role and the compaction contract). The last two also
pass on the pre-change implementation, so they describe existing behavior.

Verification after the pass: `python -m pytest -q` **566 passed**;
`python -m compileall -q src scripts` passed; the 1,000-agent benchmark
completed five runs with p95 tick time **180.551 ms** and no failed agents;
the deterministic week reached minute `10080` with 1,393 decisions, zero LLM
errors, zero rejected turns and no degeneration flag. With the configured
remote provider, two character decision sessions on `starter_village` ended
`accepted` with grade `comparative_grounded_choice`, and one God session ended
`wait` with grade `grounded`. The configured `local` provider refused the
connection (no local model server was running), so the local decision check
is not evidence either way.

Still above 200 lines and not part of this pass (the "Remaining function-size
pass" below splits them): `submit_definition_proposal`
(350), `AgentEngine.run` (317), `_validate_definition_proposal` (296),
`submit_behavior` (295), `api/cli.py:main` (291), `submit_world_change` (257),
`submit_entity_proposal` (230), `evaluate_turn` (228), `do_POST` (207) and
`_build_story_view` (203). `world_template_schema` (332) is a schema literal.

### Module split pass (2026-11-17)

The two largest projection modules were split by responsibility. Method and
function bodies moved verbatim; only names that now cross a module boundary
lost their underscore.

| Before | After |
|---|---|
| `prompting/renderer.py`, 1,113 lines | `renderer.py` (64 lines, `render_context` only) over `formatting.py` (144, one-line value renderers), `sections_common.py` (142), `sections_character.py` (278), `sections_story.py` (132), `sections_god.py` (246) and `compaction.py` (167, background removal and the size limit) |
| `agent_view/compiler.py`, 2,305 lines, one class | `compiler.py` (284): `AgentViewCompiler` keeps the constructor, shared lookups and `build_decision_view`, composed from the same `*Mixin` pattern the Platform services use: `behavior_view.py` (464), `action_availability.py` (168), `character_view.py` (196), `progress_view.py` (235), `choices.py` (152), `visible_text.py` (136), `story_view.py` (312) and `world_view.py` (284). `action_text.py` (145) holds the wording functions and `decision_frame.py` (64) the three frame dataclasses |

The public surface is unchanged: `render_context` and `AgentViewCompiler`
keep their import paths. The one test that imported a private helper now
imports `action_text.predicate_text`.

Equivalence evidence: the 38 renderer definitions and the 12 module-level
definitions and 54 methods of the compiler are AST-identical to the previous
code once the renamed names are mapped. On the five shipped worlds the
previous implementation and the split one produced the same output on 38,586
rendered prompts (including inputs that raise and prompts that were
compacted) and 2,268 decision, Story and God views; there was no difference
in results or exceptions. The previous copies were kept outside the package
for the comparison. `tests/test_agent_view_modules.py` stays as the guard for
the mixin composition: every `self.<name>` a view module reads is defined by
the composed class, no method is defined by two view classes, and no mixin
imports `compiler.py`.

Other modules the plan names as concentrated (`platform/activity.py`,
`definition_runtime.py`, `social.py`, `agents/engine.py`) are not part of
this pass.

### Remaining function-size pass (2026-11-17)

The ten functions that stayed above 200 lines in the first pass are split one
at a time, each in its own commit and each compared with the code it replaces.

| Entry point | Before | Now |
|---|---:|---|
| `AgentEngine.run` | 317 lines in `agents/engine.py` | 24 lines. `run` skips a busy or incapacitated character, compiles the task context (`_compile_task_context` over `_attach_evolution_trigger`, `_attach_triggering_evidence` and `_select_session_tools`), starts a `ReactSession` (`agents/session.py`, which holds the counters and messages that were loop locals) and calls `_react_step` once per iteration. `_react_step` asks the model (`_request_model`) and then submits the final Intent (`_submit_intent`) or runs the tool calls (`_run_tool_calls` over `_collect_calls`, `_execute_calls` and `_record_tool_result`). The guard chain for tool requests is `agents/tool_guards.py:screen_tool_call` (49 lines, fixed order). The longest new method is 44 lines |
| `submit_definition_proposal` and `_validate_definition_proposal` | 350 and 296 lines in `platform/definition_runtime.py` | 26 and 30 lines. Validation moved to `platform/definition_validation.py`: `DefinitionValidationMixin` is composed with `DefinitionRuntimeMixin` in `DefinitionService`. `_validate_definition_proposal` compiles the shape and dispatches to one `_validate_<kind>` method per definition kind. The behavior-tree walk is the `BehaviorNodeNormalizer` class, which owns the 32-node and 8-level limits that were closure state, and the resource-transfer and dynamic-inventory contracts are module functions. `submit_definition_proposal` validates through `_plan_definition` into a `DefinitionPlan` (the normalized definitions, the resolved characters and the errors), rejects when there are errors, and otherwise registers inside one transaction (`_register_definition`, `_publish_definition_events` returning `DefinitionEventIds`, `_assign_new_behavior`, `_definition_result`); the attribute-dependent services are rebuilt after commit. The longest new function is 43 lines |
| `BehaviorIntentService.submit_behavior` | 295 lines in `platform/behavior_service.py` | 13 lines. `BehaviorIntentService` moved to `platform/behavior_intent.py`; `behavior_service.py` keeps `BehaviorExecutionService`. `submit_behavior` returns the stored result of a known intent, rejects an unknown actor, opens a `BehaviorSubmission` (the intent, the actor record, the parameters as they change and the values read on the way) and calls `_admit`. `_admit` runs the admission steps in their fixed order: resolve the behavior, apply BT defaults, then one check method per rejection (`_check_available`, `_check_parameters`, `_check_actor_free`, `_resolve_targets`, `_check_entry_conditions`, `_check_cooldown`, `_check_catalog`, `_check_agent_revision`), each returning the rejection result or `None`. The first rejection is stored with the intent by one `reject_intent` call. `_start_behavior` commits the intent, the actor state, the BT instance and the `behavior.started` event in one transaction. The longest new function is 38 lines |
| `submit_world_change` | 257 lines in `platform/world_changes.py` | 6 lines. Validation moved to `platform/world_change_validation.py`: `WorldChangeValidationMixin` is composed with `WorldChangesRuntimeMixin` in `WorldChangesService`. `submit_world_change` opens a `WorldChangeSubmission` (the proposal as it is normalized on the way), runs `_validate_world_change` and commits through `_commit_world_change`. `_validate_world_change` runs the gates in their fixed order (required fields, change type, property key, property presence, payload object, rule, route, event effects, property value), each returning the rejection that `reject_intent` stored or `None`; the property key, the route endpoints and the property value are normalized into `submission.proposal`, so a rejection stores the proposal as it stood at that gate. `_commit_world_change` creates the intent, applies the change (`_apply_rule`, `_apply_route`, `_apply_relationship`, `_apply_faction` or the world property setter), publishes the `world.change.*` event, stores the pending proposal, remembers it for God and finishes the intent in one transaction. A relationship or faction change that is unusable while it is applied raises `_ChangeRejected` before anything is written; the intent and its rejection are committed together, as before. The longest new function is 32 lines |
| `submit_entity_proposal` | 230 lines in `platform/entity_proposals.py` | 10 lines. Validation moved to `platform/entity_proposal_validation.py`: `EntityProposalValidationMixin` is composed with `EntityProposalRuntimeMixin` in `EntityProposalService`. `submit_entity_proposal` opens an `EntitySubmission` (the intent and entity ids, the proposal as received, the proposal as it is normalized and the errors found so far), runs `_collect_entity_errors` and rejects once with every reason it found; `entity_failed_check` names the rejection after the most specific reason. `_collect_entity_errors` runs the steps in a fixed order (`_check_entity_label`, `_normalize_location_routes`, `_check_god_limits`, `_check_location_reference`, `_check_research_state`, `_check_research_assignees`, `_check_entity_id_free`), each appending to `submission.errors`; only `_normalize_location_routes` changes the proposal, by storing route neighbors and distances under their canonical ids. `_plan_entity` turns the validated proposal into an `EntityPlan` (the state to store, the structure promoted out of a location's state, and the character from `_normalize_god_agent_state`, which moved unchanged and rejects with `agent_schema`). `_commit_entity` creates the intent, writes the character (`_write_agent`) or the entity (`_write_structure`: `_join_neighbors`, `_record_research_gap` and the reachability check that raises `_UnreachableProposal`), publishes `entity.created`, remembers it for God and finishes the intent in one transaction; an unreachable research source rolls all of it back and is rejected with `entity_unreachable`. The longest new function is 29 lines, not counting `_normalize_god_agent_state` (118 lines, unchanged) |
| `evaluate_turn` | 228 lines in `agents/quality.py` | 26 lines. `evaluate_turn` collects the checks every role shares (`_protocol_checks`), returns the `provider_error` grade as soon as the provider gave no usable response, reads the final submission (`_final_submission`, unchanged) and runs one function per role: `_decision_checks`, `_reflection_checks`, `_god_checks` or `_story_checks`. Each appends its checks in the order the inline branches did. A decision turn returns a `_DecisionChoice` (the candidates and their refs, whether the turn waited, was guarded or interacted, the institution intent, the candidate matched by label and the selected ref) built by `_select_decision`; `_grounding_checks` adds the goal and drive alignment to it, and `_decision_grade` or `_role_grade` turns the checks into the grade. The branches that were inline are functions of their own: `_institution_checks`, `_experience_checks`, `_capability_update_checks`, `_definition_checks`, `_world_change_checks` (over `_narrative_event_checks` and `_rule_checks`, which calls `_declared_condition_checks`) and `_story_beat_checks`. Two locals that were assigned and never read (a `tokens` lambda and `effect_is_gate`) are gone. The longest new function is 37 lines |
| `_Handler.do_POST` | 207 lines in `api/http.py` | 14 lines. `do_POST` reads the JSON body, lets `_replay_idempotent_post` answer a repeated request from its stored response (or install the write that stores the first response of a new one) and hands the rest to `_route_post`; the mapping of errors to 400, 403 and 500 stayed in `do_POST`. `_route_post` serves `/session` (`_post_session`), then the operator calls, then the player routes. `_OPERATOR_POSTS` lists the 25 operator paths (the `/authoring/*` routes and the runtime, instance, snapshot, decide, simulate and event routes) with the server call behind each; one guard requires an operator session for them and for every other path under `/authoring/`, so an unknown authoring path is still refused before it is reported as not found. `_PLAYER_POSTS` maps the player paths to `_post_enter_player`, `_post_player_act`, `_post_player_wait`, `_post_dialogue` and the four `_post_trade_*` methods, whose bodies moved unchanged. The longest new function is 23 lines |
| `StoryViewMixin._build_story_view` | 203 lines in `agent_view/story_view.py` | 54 lines, most of it the returned view. `_build_story_view` reads the world, picks the source event (`_story_source_event`), builds the memory query (`_story_memory_query`) and the Story memories, describes the source (`_validated_story_source`, over `_describe_allocation` for an institution allocation and `_attach_source_fact` for the public fact the actor recorded), collects the audience candidates, the observed events and the facts, offers the beats (`_story_beat_choices`) and the facts the Story model knows (`_story_known_facts`), and returns the view; `_world_progress_candidates` builds the progress tracks that were a comprehension inside the returned literal. The calls are made in the order the inline code made them. The longest new helper is 33 lines |

`AgentEngine.run` equivalence: 887 scripted ReAct sessions on the five shipped
worlds produced the same `AgentTurn`, provider usage and database content as
the previous implementation, including sessions that raise. The scripts
covered every role, plain and native tool calls (several per response, with
and without call ids), malformed arguments, every guard in the chain, batched
intents, provider failures and non-object replies, busy and incapacitated
characters, trigger and event-group tasks, and an invalid AgentView. The new
code is 99% line-covered by that run; the three uncovered lines are early
returns in helpers this change did not touch. `tests/test_tool_guards.py` is
the permanent guard for the order and wording contract of the guard chain.

Definition proposal equivalence: the previous module was kept outside the
package and run side by side with the new one on fresh instances of the five
shipped worlds (160 scenarios, 56k submissions and validator calls). The
proposals were attributes, world properties, capabilities, recipes, Actions
with follow-up recipes and behaviors, and standalone behaviors, plus mutated
variants (junk values, deleted fields, renamed targets, wrong types, wrong
actors, targets that resolve to ids without an entity). Results, exceptions
and a dump of every table were identical after each submission, including the
submissions that raise inside the transaction and roll back. The new code is
100% line-covered in `definition_validation.py` and in `definition_runtime.py`
outside the five research helpers, which are AST-identical to the previous
code, as are the five validation helpers that moved unchanged.
`tests/test_definition_validation.py` is the permanent guard for the
resource-transfer and node-limit contracts, for the rollback of a registration
whose follow-up behavior is not executable, and for the assignment of a new
behavior.

`BehaviorIntentService.submit_behavior` equivalence: the previous service was
kept outside the package and run side by side with the new one on fresh
instances of the five shipped worlds (24,000 operations over 300 scenarios).
The operations were intents from every character and from unknown actors
(catalog behaviors, other registered behaviors, unknown and malformed
references, parameter and target aliases, visible, hidden and named targets,
stale and malformed revisions, repeated intent ids), world ticks of 1 to 200
minutes, and state changes that open the rare branches (busy or paused
characters, failure blocks, role changes, locked behaviors, cooldowns,
parameter bindings and defaults, institution allocation behaviors, changed
entry conditions). Results, exceptions and a dump of every table were
identical after each operation; two deliberate mutations (a copied instead of
shared parameter dict, and a swapped check order) were caught by the same run.
The new code is 99% line-covered; the three uncovered lines are in methods that
moved unchanged and are AST-identical to the previous ones, as are the sixteen
`BehaviorExecutionService` methods. `tests/test_behavior_intent.py` is the
permanent guard (it also passes on the previous implementation): the fixed
check order, the parameters recorded with a rejected intent at each step, alias
folding, target resolution, stale revisions, idempotent resubmission and the
rollback of a failure while the activity is opened.

`submit_world_change` equivalence: the previous module was kept outside the
package and run side by side with the new ones on fresh instances of four
shipped worlds (8,000 operations over 100 scenarios). The operations were
world property, event, rule, route, relationship and faction changes from God,
operators and other actors, with
readable property labels, numeric strings, wrong types, unchanged values,
malformed and invalid rules (schema, declared conditions and effects, self
wake, signal duplicates and cycles, repeated ids), existing, missing and
identical route endpoints, relationships with ids, names and participant
lists, missing and unknown faction ids, junk evaluation windows, world ticks,
and state changes that open the rare branches (boolean and text properties,
God property policies of several shapes, cleared rules). Results, exceptions
and a dump of every table were identical after each operation; ten deliberate
mutations (a value that is not stored, a distance that is not a float, a
rejection that is not finished, a faction that is not updated, an inverted
self-wake test, a property key that is not canonical, a skipped payload
check, a dropped mutation type, an unclamped window and a dropped God
memory) were each caught by the same run. `world_change_validation.py` is
100% line-covered; `world_changes.py` is covered except the three world
template methods, which are AST-identical to the previous ones, as are
`_world_proposal_expectation`, `_canonical_world_property` and
`_god_property_change_allowed`. All other messages of the previous function
appear unchanged in the new modules; the three "... object is required for a
... change" messages are now one template. `tests/test_world_change_validation.py`
is the permanent guard (it also passes on the previous implementation): the
gate order, the canonical key and coerced value stored with a rejection, the
property type rules, narrative events, normalized routes, the payload
objects, the intents rejected while a change is applied, rule registration
and its repeat guards, the evaluation window bounds and the rollback of a
failure while the change is recorded.

`submit_entity_proposal` equivalence: the previous module was kept outside the
package and run side by side with the new ones on fresh instances of four
shipped worlds (6,300 operations over 90 scenarios). The operations were
proposals for locations, characters, resources, materials, documents, research
sources, objects, factions and machines from God, operators and other actors:
route neighbors as ids, names, repeats, unknown places and non-locations;
distances that are valid, zero, negative, text or boolean; stocked and empty
resources; research sources with every shape of discoveries, assignees,
required counts and novelty keys at reachable and unreachable places;
characters with missing, clamped and unknown capabilities, needs, behaviors,
factions and knowledge seeds (including a seed that fails midway); names that
repeat another entity up to case and spacing or carry markup; existing and
unusable entity ids; world ticks; and state changes that open the rare
branches (seven shapes of `resource_entity_types`, an extra need, an unreadable
world minute). Results, exceptions and a dump of every table were identical
after each operation, including the operations that raise inside the
transaction and roll back. Twenty-eight deliberate mutations (a swapped step
order, an off-by-one stock limit, a name key that keeps its case, routes that
are not deduplicated, a lost failed check, a changed research threshold,
dropped assignee, location and id errors, a research location fallback, the
God location checks applied to every actor, the event minute, the seed
confidence default, the neighbor distance default, the gap actor, the stored
research focus, God memory, the retryable flag, a promoted region, a distance
that is not a float, the reachability check and its check name, an unrecorded
gap, a duplicated neighbor route, an unwritten seed, an unset location and a
rejection that stores the normalized proposal) were each caught: 26 by the
same run at once, the world minute read after planning once the run gained a
directed case with an unreadable minute, and the duplicated neighbor route by
a permanent test. `entity_proposals.py` is 100% line-covered;
`entity_proposal_validation.py` is covered except four lines of
`_normalize_god_agent_state`, which is AST-identical to the previous one. All
115 distinct strings of the previous function appear unchanged in the new
modules. `tests/test_entity_proposal_validation.py` is the permanent guard (it
also passes on the previous implementation): the step order and the whole
error list, the proposal stored as received, name rules and duplicates, the
most specific failed check, canonical and reciprocal routes with float
distances, the God location and stocked-resource limits and the configured
resource types, research discoveries, assignees and novelty at one place,
the order of agent errors, the defaults a character receives, knowledge
seeds and the rollback of a failing one, the intent, event and God memory of
an accepted proposal, and the knowledge gap and focus of a research source.

`evaluate_turn` equivalence: the previous module was kept outside the package
and run side by side with the new one on 150,000 generated turns and their
views (three runs of 50,000). The turns covered every role and an unknown one,
provider failures, every status, iteration counts around the bound, decisions
whose candidates are executable or only available, labels and refs (bare,
prefixed, unknown), interactions, waits, institution proposals, goal and drive
alignment, advisory and absent reasons, filled execution costs, lessons that
cite or ignore a recent outcome, capability, goal, belief and behavior
updates, God progress, institutions, entities, definitions and world changes
(events with and without a property, rules with every effect and enforcement
shape and declared conditions) and Story progress, waits and beats, submitted
as tool calls or as intent responses. About 1.7% of the turns were malformed
on purpose (missing or wrongly typed fields) and raise in both versions. The
grade, every check with its detail and its position, and the exception were
identical for every turn, and neither version changed its inputs. The run
reached all 11 grades and all 56 check names, and both modules are 100%
line-covered by it. Twenty-six deliberate mutations (the forced-choice
threshold, the order of the grades, a role routed to another role's checks, a
skipped role, a dropped condition check, the candidate lists swapped, a lost
alignment, a moved institution check, the iteration bound, a rule dispatched
for events, a view or payload not passed on, and others) were each caught by
the same run, and 25 of them by the permanent tests; the last one, a
candidate without a `ref`, can only be told apart on a malformed view because
`agent_view/behavior_view.py` always projects one. `tests/test_turn_quality.py`
is the permanent guard (it also passes on the previous implementation): the
shared session checks and the provider failure, each decision grade, the
reason grounding details, how a choice resolves to an offered ref, institution
decisions, every reflection submission, every God submission (progress,
institutions, entities, definitions, narrative events and rules with their
enforcement and declared conditions) and Story progress, waits and beats.

`do_POST` equivalence: the previous module was kept outside the package and
its handler was driven side by side with the new one, without sockets, over a
recording stand-in for the server that keeps the real authorization, session
and idempotency code. 84,000 POST requests in 2,100 sequences of 40 covered
every route and a few that do not exist, with and without a query string; no
credential, the operator token, an operator, observer and player session, a
player session of another instance and an unknown one, as a header or as a
bearer token; idempotency keys in the body and the header, repeated, too long
and shared between sessions; bodies with missing, mistyped or Chinese values,
text that is not JSON, arrays, numbers and empty bodies; a wrong content
length; and errors raised from inside the server call (`KeyError`, `TypeError`,
`ValueError`, `JSONDecodeError`, `PermissionError`, `RuntimeError`). The status,
every header, the body, the order and arguments of every call made on the
server, the stored idempotent responses, the issued sessions, the log lines
and whether the handler's write was replaced were identical for every request,
and the POST code of both modules is 100% line-covered by it. Twenty-seven deliberate
mutations (the replay status, a replay that goes on, the token or the session
exemption of the idempotency key, an unguarded authoring prefix or operator
table, the guard role, a swapped or renamed call, a changed default reason, a
call without the body, a player path mapped to another handler, the speaker
role of a dialogue, a changed status for bad requests and refusals, and a body
that is not read) were each caught, and also by the permanent tests. The
statements of the session branch and the eight player branches are
AST-identical to the new methods, the 25 operator calls are AST-identical to
the table entries once `self.server` is `server`, the exception handlers and
the nested capturing write are unchanged, and no other function of the module
differs. The only string of the previous `do_POST` that the new code has fewer
of is `"operator"` (sixteen guards became one).
`tests/test_http_post_routes.py` is the permanent guard (it also passes on the
previous implementation): the call behind each operator route and its refusal
for every other session, unknown routes, the sessions that may be created, the
authorization of player actions, waits, dialogues and trade calls, idempotent
replay and what it stores, and the mapping of errors.

`_build_story_view` equivalence: the previous mixin was kept outside the
package and the Story view of both versions was built side by side on the
same database, through `build_decision_view` for an id that is not a
character. 16,541 views on the five shipped worlds (16,218 with the audience
candidates replaced by one shared stub, 323 with the real ones) were compared.
The worlds were driven by decisions and ticks and then given events of every
information type and of Story, God, unknown and missing actors; unknown and
missing locations and research sources; outcomes of every shape; allocation
events with and without rows; public facts that match or miss by actor,
minute (also as text) and source type; fact rows that are not JSON objects;
configured and emergent beats in every state, up to fourteen at once; more
than twelve known facts; progress tracks of every shape; and role memories.
The source was the default, an event named by id, an unknown id, an id
given as text and an id that is not a number. The view (key order included),
the exception, the ordered log of every call the view makes on its
collaborators (the memory query, the beat check, the prompt sections, the
world time, history, gaps, sources, rule labels, tools and the text
humanizers) and the number of database writes were identical for every
view. Every line of `_build_story_view` and its helpers ran in both versions;
the lines the run does not reach are in `_story_audience_candidates`, which
the stub replaces and which ran unchanged in the 323 real views, and in
`_active_story_threads`, which the Story view does not call. Fifty-four
deliberate mutations (the event an id selects, the actors and event types
the default source skips or accepts, the fallback to the behavior wrapper,
the memory query parts and window, the name and location fallbacks, the
result and fact precedence, the allocation summary, the public fact match by
actor, minute, source type and the sixty-four row window, the first match,
the beat check inputs, the cap of twelve choices and of twelve facts, both
kinds of duplicate fact, the progress defaults and filter, the memory limit
and the eighty-event window) were each caught by the comparison (the last
four once cases for them were added to it), and 53 of them also by the
permanent tests. The statements of every helper are
AST-identical to the statements they replaced, and the only string the new
code has more of is `"actor_id"` (one more `source_event.get("actor_id")` in
`_build_story_view`). `tests/test_story_view.py` is the permanent guard (it
also passes on the previous implementation): which event is the source, the
memory query and the memories asked for, the names, location, result, fact
and research source of the validated source, the allocation summary, the
public fact and its matching rules, the beats on offer and how a choice is
worded, the known facts, the progress tracks, and the order of the sections
and of the source's fields.

Two behaviors of the Story view were kept as they were. This pass does not
fix them:

- The view reports the source event's actor as `self.id`
  (`agent_view/story_view.py:68` rebinds `actor_id`, which the fact lookup
  needs); the id the view was asked for survives only when there is no source
  event. A search of `src/sim2` finds no reader of it: Story prompts drop the
  `self` section (`prompting/compaction.py:71`) and
  `agent_view/validator.py:28` only checks that it is an object. The
  permanent tests do not pin it; the comparison with the previous code does.
- A source event at world minute 0 never gets its public fact:
  `agent_view/story_view.py:212` turns `0` into `-1` (`int(... or -1)`),
  which matches no `observed_at`.

Still above 200 lines: `api/cli.py:main` (291).

### Hardcode leftovers pass (2026-11-17)

This pass looked for world vocabulary that the engine, the prompts or the web
client still carried. The rule it applied: engine code may name what the
Platform itself publishes or defines (event types it records, role and intent
names, the condition and resolver vocabulary, lifecycle statuses); it must not
name what a world authors (tag names, titles, genres, seasons, phrases of one
language). Three changes followed, each with its own regression test:

| Commit | Change | Evidence |
| --- | --- | --- |
| `c4fff3e` | Location tag groups come from `rules.location_tag_groups` only. The health service and safe-location resolvers fell back to `{"health", "clinic"}` and `{"safe"}` while the `location.*` conditions had no default (`actions/conditions.py:346`, `:359`), so a world that tagged a clinic but declared no group was sent there and refused on arrival. | On the previous code, with the treatment behavior reduced to its travel step, the character reached the clinic from an undeclared group; now she stays. `tests/test_location_tag_groups.py` fails on the previous code and 7 of 8 mutations are caught (the eighth, an early return for an empty group, is equivalent). Every shipped world already declares the groups it uses. |
| `96a3a7a` | One `MEANINGFUL_REFLECTION_EVENTS` (`agents/reflection_policy.py`) replaces the two copies in `agents/intent_gateway.py` and `agents/tool_executor.py`. | `tests/test_reflection_evidence.py` passes on the previous code and on the new code, and catches 10 of 10 mutations, including a drift of either old copy. |
| `ccb2290` | The static web pages carry no world name: placeholders are neutral, the map dialog heading is filled from the world's title (it used to read "潮汐公社" for every world), and the empty-scene line is neutral. | `tests/test_web_assets_world_neutral.py` fails on the previous assets and catches 7 of 7 mutations. |

`rules.location_tag_groups` and `rules.failure_recovery_events` are now
documented in `docs/world-authoring.md`; the second one already existed but had
no entry.

Kept in code on purpose, because the Platform defines the vocabulary:

- Event types the Platform publishes (`platform/activity.py:644`, `:839`,
  `:897`, `:952`, `:972`, `:1004`) and the lists that name them:
  `agent_view/story_view.py:123`, `behavior/recovery.py:6` (the world can
  replace it with `rules.failure_recovery_events`), `platform/stagnation.py:32`
  and `evolution/config.py:20`.
- Role and intent names (`agents/runtime_policy.py:9`, `:61`), the condition
  vocabulary (`actions/conditions.py:172`), the lifecycle statuses
  (`world/lifecycle.py:28`) and the formula roots of the combat effect
  (`actions/effects.py:111`, `platform/activity.py:526`).

Left for a decision, each with its evidence:

- `random_seed` is declared by every world manifest, validated and stored
  (`bootstrap/service.py:326`, `:764`), but nothing in `src/` reads it and
  `src/` imports no `random` module.
- Predicates handled at `actions/conditions.py:356` default to true when the
  context holds no value (`:361`, `context.get(path, True)`).
- The `propose_world_change` condition example names starter-village
  vocabulary (`agent_view/tool_registry.py:381`); changing it changes a prompt
  every God sees, so it needs a real-provider check first.
- The reflection decline phrases cover English and Chinese only
  (`agents/intent_gateway.py:13`).
- The web client labels a fixed set of tags and drops the others
  (`web/app.js:483`, `:588`, two copies of one table).
- The Story view's default source events (`agent_view/story_view.py:123`) are
  a different list from the world's `story.emergent_source_event_types`
  (`platform/story_runtime.py:134`); the Platform also marks its own source
  events with `wake="story_source"` (`platform/activity.py:952`).

### Execution order for the remaining work

The following is the concrete plan for this development cycle. Each stage
must finish its gate before the next stage starts.

| Stage | Deliverable | Verification gate | Stop and fix when |
|---|---|---|---|
| A. Working-tree baseline | Fix focused regressions, keep the prompt projection bounded, and record the exact test baseline. | Focused tests, full tests, `compileall`, and `git diff --check` are green. | A test is made green by weakening grounding, increasing a token limit, or rewriting a failed intent. |
| B. Single definition contract | `definition_compiler.py` is the only structural source for Action, BT, Recipe, capability and attribute definitions. Platform performs only instance-dependent checks. | A fresh world with unrelated names boots; a God-created Action plus bound BT is executable by an NPC; invalid definitions fail consistently. | A legacy alias, food-specific resolver, duplicate schema, or role-specific validation waiver is added. |
| C. Single causal lifecycle | Every state-changing request is `Intent → Activity → Outcome → WorldEvent`; every consumer wake is `WorldEvent → EvolutionTask`. | One integration test follows an NPC action through all records, including causation and world revision, and restart does not duplicate the task. | A state write bypasses Unit of Work, or Story/God is called directly without a committed event. |
| D. Agent boundary and prompt projection | Session owns bounded ReAct; protocol owns provider normalization and call IDs; registry owns schemas/permissions; audit owns evidence. Context keeps a stable prefix and useful causal suffix. | Prompt source, response summary, tool arguments, accepted intent, latency, usage and event IDs are auditable without raw hidden reasoning or secrets. | A provider error is converted to `wait`, a rejected intent is marked passed, or context is enlarged to hide a bad projection. |
| E. Dedicated causal evolution probe | Use a small declarative world and the configured DeepSeek provider for 10 NPC → Platform → Story/God → NPC rounds. The probe must exercise a dynamic Action+BT and repeated research objects through the same generic research BT. | Every round records bounded context, model output summary, tool args, accepted/rejected result, event, follow-up task, next NPC choice and world delta. At least three later rounds create new, non-duplicate content and change executable NPC behavior. | The run only proves tool-call success, repeats a proposal without new evidence, reaches all-wait absorption, or hides provider failure. |
| F. Deterministic one-week and performance audit | After the causal probe passes, run the non-LLM week and the 1,000-agent benchmark. Investigate depletion, idle absorption, unfinished activities and p95 latency. | No unexplained starvation/deadlock; long activities have explicit end semantics; benchmark reports the configured p95 target. | A balance defect is hidden by increasing initial stock, or a performance problem is addressed by skipping state/event work. |
| G. Delete temporary scaffolding | Remove migration façades, duplicate dispatch paths and dead compatibility helpers exposed by stages A–F. Update the plan with evidence. | `rg` checks show one owner for each lifecycle, event wake and schema path; full suite and compile checks remain green. | Cleanup requires a new compatibility branch or changes historical facts instead of migrating definitions. |

### Files and artifacts for this cycle

- Code changes stay in the existing owners under `src/sim2/platform/`,
  `src/sim2/agents/`, `src/sim2/agent_view/`, `src/sim2/world/` and
  `src/sim2/persistence/`; no world-specific branch is allowed in Platform.
- Focused regression tests belong beside the affected protocol. The dedicated
  evolution probe remains `scripts/audit_dynamic_evolution_loop.py` rather
  than adding another long-run command.
- Real-provider SQLite files and JSONL traces stay under `/tmp`. The
  repository keeps only the redacted summary and the verification procedure
  in `docs/operations/real-model-evolution-check.md`.
- Every real-model run starts from an interactive shell with
  `source ~/.bashrc`; credentials, proxy values and hidden reasoning are never
  written to output.

### Verification record for the current working tree

The following checks were run on 2026-11-14 after writing this plan:

- `python -m pytest -q`: **408 passed**.
- `python -m compileall -q src scripts`: passed.
- `git diff --check`: passed.
- `python scripts/benchmark_platform.py --agents 1000 --runs 5`: passed with
  no failed agents and p95 tick time **144.194 ms** (warm-up excluded).
- The benchmark script was migrated to
  `store.repositories.entities`; it no longer depends on the removed Store
  read/write façade.
- Audit/export scripts now use typed repositories as well. The retired
  singular `research_discovery` shape, old research field aliases, default
  food/resource context fallbacks, and event-field aliases were removed; the
  three-lanterns packages now declare their resource tag groups explicitly.
- `tests/test_generic_research_chain.py` verifies that the retired singular
  research shape is rejected at the Platform boundary.

Stages E and F still require the final evidence pass after the current prompt
changes: a ten-round real-provider causal audit, followed by the deterministic
one-week balance check. Their artifacts must be reviewed for semantic output,
not only tool-call success, before this plan can be marked complete.

The latest ten-round DeepSeek audit after the research-status projection passed
the semantic gate: `10/10` rounds, no provider errors or rejected intents,
three distinct God-created research objects, changing NPC choices, and
continued Story/God handoffs. The audit is `/tmp/sim2-evolution-research-status/`.
The same run showed why the status projection matters: an old unresolved
sentence remained in history, but the current `<research_sources>` view marked
the completed source `resolved`, so later God proposals moved to new causal
questions instead of reopening it. The remaining deterministic one-week and
Beam/provider checks are separate gates.

### Explicit non-goals

This cycle will not create a quota-specific BT, a research-object-specific BT,
or a separate code path for the three-lanterns world. A quota, institution,
research source, resource or technology must be represented as configuration,
an entity/definition proposal, or a generic primitive. God may propose a new
definition; Platform validates and binds it; NPCs can execute it only when a
generic behavior is reachable. Story may organize committed facts into a
continuing thread, but it cannot invent a world fact or directly mutate state.

The plan is considered complete only when stages A–G have passed their gates
and the evidence is recorded. A successful tool call by itself is never an
acceptance result.

### Current position

The repository is between the first composition cut and the final removal of
migration façades:

1. The Platform facade and the world-evolution split are in place.
2. Unit of Work and typed repositories are in place for state-changing paths.
3. Agent tool registry, executor, session and runtime policy have been split.
4. The explicit cross-service owner conversion is now tested and committed;
   the Platform facade no longer relies on implicit service-method scanning for
   the migrated paths.
5. Metadata ownership is now explicit through `MetadataRepository`; the
   remaining architectural debt is the duplicate definition compiler/
   validation paths and the still-large AgentEngine.
6. A one-week deterministic run has already exposed a content-level liveness
   issue (`vital_depleted:fei_lan`). This is tracked after the ownership
   refactor; it must be fixed in world configuration or generic demand and
   regeneration rules, never by adding a Starter Village branch.

### Ordered work packages

#### Package 1 — close the explicit-service boundary

**Files:** `src/sim2/platform/*.py`,
`tests/test_platform_composition.py`, `tests/test_unit_of_work.py`,
`tests/test_action_binding.py`, `tests/test_god_world_content.py`,
`tests/test_institution_evolution.py`, `tests/test_evolution_liveness.py`.

- Finish the pending owner-call conversion in the working tree.
- Keep `Platform` as the public dispatch facade; service-to-service calls use
  declared context dependencies or typed callbacks only.
- Do not add another `__getattr__` lookup, service scan, or compatibility
  alias.
- Run `git diff --check`, the focused suite, and `compileall`.
- Commit this package separately with a short imperative subject.

**Gate:** no service reaches an undeclared service; one tick still commits
minute effects, activity progress, outcomes, events and wake tasks in one
transaction; the focused suite is green.

**Completed:** committed as `d995b06` after 94 focused tests and compile
checks. A follow-up regression also fixed the facade-to-runner host boundary.

#### Package 2 — make event and wake ownership singular

**Files:** `src/sim2/platform/events.py`,
`src/sim2/platform/wake_service.py`, `src/sim2/platform/activity.py`,
`src/sim2/platform/social.py`, `src/sim2/platform/entity_proposals.py`,
`tests/test_event_wake_ownership.py`.

- Add one event publisher that assigns `event_id`, `causation_id`,
  `correlation_id`, `world_revision` and actor metadata.
- Add one wake publisher that turns committed events into idempotent
  `EvolutionTask` records.
- Remove direct Story/God queue calls from activity and proposal code.
- Keep evidence grouping as a pure calculation; it must not write tasks.
- Test duplicate delivery, restart, cooldown, uncommitted-event rejection and
  one task per consumer/event group.

**Current state:** `platform/events.py` is now the single Platform service
entry for event creation. Platform services no longer call
`uow.events.append` directly; publication can atomically dispatch configured,
Story-source and declared follow-up wakes. Dynamic consumer queueing also
passes through the EventService boundary, and WakeService rejects unknown or
type-mismatched source events. Restart recovery coverage already exists in the
vertical suite.

**Completed:** event publication and dynamic wake ownership are committed as
`9857e7c` and `74f2c5f`; the focused event/evolution suite passes.

**Gate:** `rg "_queue_story_task|_queue_event_consumer_task" src/sim2`
returns only the WakeService implementation and its tests. Every Story/God
wake can be traced to a committed event.

#### Package 3 — remove the Store read façade

**Files:** `src/sim2/persistence/store.py`,
`src/sim2/persistence/repositories/`, `src/sim2/platform/`,
`src/sim2/agent_view/`, and repository ownership tests.

- Replace read-heavy `Store` method calls with typed repository calls through
  the existing Unit of Work or an explicit read context.
- Keep SQLite as the source of truth and keep all writes inside Unit of Work.
- Add restart and rollback tests while each read domain is migrated.
- Delete the façade only after `rg "\.store\.[A-Za-z_]" src/sim2` shows no
  unclassified access.

**Current state:** `WorldStore.repositories` exposes one named repository per
persistence domain, including `MetadataRepository`. UnitOfWork, Platform
services, AgentView, perception, memory, simulation, Actions and API
projections use those typed objects. Repository-internal cross-domain calls
also use named repositories. The dynamic Store metadata method façade has
been removed.

**Completed:** the façade removal and test migration pass the full suite and
are ready to commit. Store remains responsible only for SQLite connection,
transaction, repositories, snapshots and lifecycle primitives; metadata rows
are owned by `MetadataRepository`.

**Gate:** no repository commits independently; event and projection writes are
atomic; claimed evolution tasks are recovered after restart; replay preserves
event IDs and revisions.

#### Package 4 — one definition compiler and one validation contract

**Files:** `src/sim2/world/schema.py`,
`src/sim2/platform/definition_runtime.py`,
`src/sim2/agent_view/compiler.py`, `src/sim2/agent_view/tool_registry.py`, and
schema tests. If a dedicated compiler module is needed, create it beside the
existing schema owner rather than adding another parallel schema source.

- Define Action, BT, Recipe, capability, attribute and follow-up schemas once.
- Emit from that source: persisted normalized definition, model JSON Schema,
  AgentView description and Platform validation rules.
- Remove food aliases, duplicate research formats, event/event_type aliases and
  role-specific validation exceptions; migrate current development fixtures to
  the canonical form instead of preserving old input.
- Ensure a God-created Action plus BT is immediately reachable by the generic
  NPC behavior selector and is covered by an end-to-end test.

**Progress:** `world/definition_schema.py` is now the canonical model-facing
JSON Schema for `propose_definition`; the God tool registry imports it instead
of carrying a second hand-written copy. Fresh schema instances and the
God-created Action+BT path are covered by tests. Semantic normalization and
Platform validation still need to consume this same module before this
package is complete. The structural compiler is now
`world/definition_compiler.py`; it owns definition kinds, declarative field
checks, Action result/effect contracts, BT outcome-event shape, Recipe and
Capability shape, and model shorthand normalization. Platform retains only
instance-dependent reference, reachability, conservation and duplicate
checks. This compiler boundary is committed as `82d4954`.

**Gate:** a world with unrelated names, attributes, objects and entities boots
without Python changes; the same invalid proposal is rejected consistently by
the model-facing schema and Platform.

#### Package 5 — finish AgentEngine boundaries and prompt projections

**Files:** `src/sim2/agents/engine.py`, `session.py`, `protocol.py`,
`tool_executor.py`, `runtime_policy.py`, `audit.py`,
`src/sim2/agent_view/compiler.py`, `tool_registry.py`, `policies.py`.

- Keep the bounded ReAct loop in the session owner.
- Keep provider response normalization and tool-call IDs in the protocol
  owner; the platform, not the model, generates persisted call identities.
- Keep tool schemas and role permissions in the registry; keep mutation in the
  executor/gateway.
- Keep prompts compact: bounded world projection, relevant history and role
  memory only. Do not paste the full event log or raw hidden reasoning.
- Record input source, response summary, tool arguments, accepted/rejected
  intent, latency, token usage and resulting event IDs without secrets.

**Progress:** LLM audit persistence now lives in `agents/audit.py`; AgentEngine
keeps the bounded ReAct orchestration and calls the audit owner. Provider
normalization and protocol-specific response helpers now live in
`agents/protocol.py`; bounded native-message trimming lives in
`agents/session.py`. Engine retains thin proxies for the existing test/API
surface. Provider usage is extracted without mutating the wire response, and
the extraction is committed as `ce605a5`.

**Gate:** prompt history has a stable prefix, bounded suffix and explicit
  truncation reason; a provider failure resumes or waits according to policy;
  no reflection or Story/God output is silently rewritten into a different
  intent merely to pass quality checks.

#### Package 6 — causal evolution and liveness audit

**Files:** `scripts/audit_dynamic_evolution_loop.py`,
`docs/operations/real-model-evolution-check.md`, world configuration and
generic rule tests only where the audit identifies a real content defect.

- Run a ten-round NPC → committed event → Story/God → proposal → NPC loop
  with the configured DeepSeek provider for semantic quality.
- Run Beam as a provider/protocol check; `finish_reason=length` with no
  response is a provider failure, not a world-quality pass.
- Inspect every round's bounded context, tool arguments, response summary,
  accepted intent, event, follow-up task, next NPC choice and world delta.
- Run the deterministic one-week simulation and investigate every depleted
  vital, repeated proposal, idle absorption state and unfinished activity.
- Fix resource regeneration, demand, or progression in data/configuration or
  generic primitives; do not add `if world == ...` logic.

**Gate:** ten causal rounds show changed NPC behavior and committed world
  state, not only successful tool calls; repeated proposals are explained by
  missing evidence or rejected as duplicates; the one-week run has no
  unexplained starvation/deadlock and all long activities have explicit end
  semantics.

### Required verification commands

Run focused checks after each package and the full gate only at the merge
boundary:

```bash
python -m pytest -q tests/test_platform_composition.py \
  tests/test_unit_of_work.py tests/test_action_binding.py \
  tests/test_god_world_content.py tests/test_institution_evolution.py \
  tests/test_evolution_liveness.py
python -m compileall -q src scripts
bash -ic 'source ~/.bashrc && python scripts/audit_dynamic_evolution_loop.py --provider deepseek'
bash -ic 'source ~/.bashrc && python scripts/audit_dynamic_evolution_loop.py --provider remote_beam'
python scripts/run_long_simulation.py --minutes 10080
python -m pytest -q
python scripts/benchmark_platform.py --agents 1000 --runs 5
```

The model-backed command must write a run record under
`docs/operations/real-model-evolution-check.md` or its referenced artifact;
it must not print keys, proxy settings or hidden reasoning. A failed provider
call is recorded separately from a rejected world intent.

### Stop conditions

Stop the current package and fix its cause when any of the following occurs:

- a state change bypasses Unit of Work or lacks a WorldEvent;
- Story/God is woken without a committed causal event;
- an NPC receives a proposal but no executable generic behavior or assignment;
- a repeated action has no changed evidence, progress or explicit maintenance
  reason;
- a model output is accepted without grounded evidence;
- a test passes only because a legacy alias, quality waiver, token-limit bump,
  or world-specific fallback was added.

The plan is complete only after all package gates and the final acceptance
section pass. Until then, this document must retain the unresolved item and
the evidence for it instead of marking the architecture complete.

## Baseline before convergence

The current core contains approximately 21,000 lines of Python. The largest
modules are:

| Module | Approximate size | Current concern |
|---|---:|---|
| `platform/runtime.py` | 98 lines | Public facade still exposes the migration dispatch surface; lifecycle ownership now lives in services |
| `platform/activity.py` | 1,500 lines | Action/BT execution, research, Story completion, institutions and event wakeups are mixed |
| `platform/definition_validation.py`, `definition_runtime.py`, `entity_proposal_validation.py`, `entity_proposals.py`, `story_runtime.py`, `world_change_validation.py`, `world_changes.py` | split modules | Definition validation, definition registration, entity proposal validation, entity proposal commit, Story beats, world change validation and world change commit now have separate owners |
| `persistence/store.py` | 407 lines | SQLite connection, transaction and repository composition; metadata is owned by `MetadataRepository` |
| `agent_view/compiler.py` | 1,900 lines | Decision, Story and God views share availability, parameter and rendering logic |
| `agents/tools.py` | 1,200 lines | Schemas, dispatch, role policy, compatibility normalization and proposal rules are mixed |
| `agents/engine.py` | 1,067 lines | Session, provider protocol, intent normalization and audit still need a final boundary |

The most important duplicate lifecycle is:

```text
Intent
BT / Action instance
Evolution task
Story state
Progress state
Memory / knowledge result
```

The most important duplicate wake paths are the generic event scheduler and
direct calls such as `_queue_story_task` from the activity runtime.

## Current implementation snapshot

The first convergence stages have now landed:

| Area | Current owner | State |
|---|---|---|
| Platform facade | `platform/runtime.py` | 98-line facade; no Runtime Mixin inheritance |
| Runtime API | `platform/api_service.py` | explicit service |
| Behavior admission/execution | `platform/behavior_intent.py` (admission), `platform/behavior_service.py` (execution) | explicit services |
| World minute | `platform/world_tick_service.py` | explicit service |
| Evolution tasks | `platform/evolution_service.py` | explicit service and UoW task repository |
| Definitions/entities/Story/world changes | `platform/definition_validation.py`, `definition_runtime.py`, `entity_proposal_validation.py`, `entity_proposals.py`, `story_runtime.py`, `world_change_validation.py`, `world_changes.py` | separate owners |
| Persistence boundary | `persistence/uow.py`, `persistence/repositories/` | all SQL domains now have repository owners; the Store façade remains only as the migration surface |

The table above is the authoritative starting point for the remaining work;
the baseline table below records why the split was needed.

## Target architecture

The target runtime has one write coordinator and one causal lifecycle:

```text
Agent / Story / God
        │
        ▼
IntentGateway
        │ validates and creates Intent
        ▼
ActivityService
        │ owns Activity, BT cursor, Action instances and reservations
        ▼
OutcomeService
        │ compares expected and actual state
        ▼
EventService
        │ atomically persists WorldEvent and state projection
        ▼
WakeScheduler
        │ creates one EvolutionTask per configured consumer
        ▼
AgentView + AgentEngine
```

Platform remains the transaction boundary and public facade. Individual
services do not write SQLite outside the transaction supplied by Platform.

The canonical records are:

| Record | Single responsibility |
|---|---|
| `Intent` | What an Agent asked Platform to do |
| `Activity` | One long-lived execution owned by one actor |
| `Outcome` | Expected state, actual state, delta and result |
| `WorldEvent` | A committed fact with causation and correlation IDs |
| `EvolutionTask` | A scheduled consumer of one or more events |
| `Memory` / `KnowledgeFact` | An Agent's retained interpretation of committed facts |

Story and God never receive a private direct callback from Activity. They only
receive EvolutionTasks created by WakeScheduler from committed WorldEvents.

## Phase 0: Freeze the protocols

Before moving files, freeze these four contracts in
`docs/design/runtime-protocols.md`:

1. `Intent → Activity → Outcome → WorldEvent`
2. `WorldEvent → EvolutionTask`
3. Definition registration and versioning
4. AgentView and tool submission boundaries

Deliverables:

- Add a lifecycle diagram and field table for `Intent`, `Activity`, `Outcome`,
  `WorldEvent` and `EvolutionTask`.
- Define the required `causation_id`, `correlation_id`, `world_revision`,
  `definition_version` and actor fields.
- Mark direct Story/God queue calls as retired.
- Add one integration test that follows one NPC action through all five
  records.

Exit criteria:

- Every state-changing behavior has one Activity and one Outcome.
- Every Story/God wake is explainable by a committed event.
- No new feature may add a second lifecycle representation.

## Phase 1: Collapse event and wake ownership

### Changes

- Create `src/sim2/platform/events.py` for event creation and causal metadata.
- Create `src/sim2/platform/wake_service.py` as the only owner of consumer
  task creation.
- Move `_queue_story_task`, `_queue_event_consumer_task` and configured
  followup dispatch behind one public `WakeService.publish(event)` method.
- Remove direct Story queue calls from `activity.py`, `social.py` and the
  proposal services.
- Keep `evolution/wakes.py` as the pure evidence-group calculator; it must not
  write tasks directly.

### Required tests

- One event creates at most one task per consumer and event group.
- A manual-only event is delivered only through its declared producer.
- A Story task cannot be created from an uncommitted or synthetic event.
- Event group count and cooldown behavior are deterministic after restart.

### Exit criteria

`rg "_queue_story_task|_queue_event_consumer_task" src/sim2` returns only the
WakeService implementation and its tests.

## Phase 2: Replace Platform Mixins with services

Keep `Platform` as the public facade, but replace the Mixin inheritance with
explicit services in this order:

1. `ActivityService`: `_start_action`, progress, interruption, completion and
   reservation lifecycle.
2. `DefinitionService`: Action, BT, Recipe, Capability and Attribute
   registration.
3. `EvolutionService`: entity proposals, world changes and evolution proposal
   validation.
4. `SocialService`: dialogue, trade and institution operations.
5. `WorldTickService`: minute effects, regeneration, safety signals and time.
6. `SnapshotService`: snapshot, restore, replay and audit export.

Each service receives a narrow `PlatformContext` containing the store,
transaction helper, resolver and event publisher. Services may not call
another service's private method.

### File moves

- Keep the completed proposal split in `definition_runtime.py`,
  `entity_proposals.py`, `story_runtime.py` and `world_changes.py`.
- Move activity-specific research and information progress into
  `ActivityService`; keep research catalog reads in `perception`/Repository.
- Move timeline rendering out of `runtime.py` into `api/timeline.py`.
- Move snapshot and audit methods out of `runtime.py` into `SnapshotService`.

### Exit criteria

- `Platform` no longer inherits from runtime Mixins.
- `runtime.py` is a coordinator of fewer than 500 lines.
- No service imports the concrete `Platform` class to call private methods.
- Existing targeted tests pass without changing behavior.

## Phase 3: Split persistence and projections

**Status:** repository ownership is now split into entities, memory, activity,
social, events, evolution and LLM modules. `UnitOfWork` owns the SQLite
transaction, all Platform event writes and evolution task operations use its
typed repositories, and rollback coverage is present. The remaining work is
to replace the Store method-name façade with typed repository calls in the
read-heavy projections, then remove the façade. This is Package 3 of the
authoritative execution plan above.

Replace the broad `WorldStore` API with repositories behind one Unit of Work:

- `EventRepository`
- `EntityRepository`
- `AgentRepository`
- `ActivityRepository`
- `EvolutionRepository`
- `MemoryRepository`
- `SocialRepository`
- `SnapshotRepository`

The Unit of Work owns the SQLite transaction. Repositories do not commit on
their own. The current SQLite schema remains the source of truth during the
refactor; only the Python ownership boundary changes.

Exit criteria:

- A repository cannot update a table outside a Unit of Work.
- Event and projection writes still commit atomically.
- Crash recovery and deterministic replay tests pass.

## Phase 4: Unify AgentView, tools and validation

**Status:** tool ownership is split into `agent_view/tool_registry.py`,
`agents/tool_executor.py`, and `agents/tool_protocol.py`; `agents/tools.py` is
only the public import façade. The registry and `agent_view/policies.py` sit
below the runtime: the projection imports them, and `agents` (which imports
`platform`) is never imported by `agent_view`, so `platform` → `agent_view` →
`agents` → `platform` no longer closes a package cycle
(`tests/test_architecture_layers.py` guards it). `AgentTurn` and role/evolution routing policy
now live in `agents/session.py` and `agents/runtime_policy.py`. The remaining
work is to make the definition compiler the sole source for model schemas and
Platform validation, then separate the bounded session/audit implementation
from the orchestration engine. This is Packages 4 and 5 of the authoritative
execution plan above.

Create one definition compiler that emits:

1. Model-facing JSON Schema.
2. Platform validation rules.
3. AgentView descriptions.
4. Persisted normalized definition.

Remove duplicate hand-written definitions for Action parameters,
`success/failure`, `outcome_events`, resource reservations and followup BTs.

Split the Agent layer into:

- `session.py`: bounded ReAct loop and message history.
- `protocol.py`: provider response normalization and tool-call envelopes.
- `agent_view/tool_registry.py`: schemas and role permissions.
- `audit.py`: prompt, response and usage persistence.
- `agent_view/policies.py`: role instructions only.

`AgentViewCompiler` becomes a projection service. It must not contain
Platform mutation rules or hidden world-specific fallback behavior.

Exit criteria:

- One canonical Action/BT schema is used by model validation and Platform.
- No legacy aliases remain in `agents/tools.py`, `agent_view/compiler.py` or
  `runtime.py`.
- A new world with different attributes and entities needs only configuration
  changes.

## Phase 5: Remove patch-shaped contracts

After the unified schema is in place, remove these special cases or express
them as ordinary configuration:

- food-specific aliases and fallback resolvers;
- direct `god`/`story` branches outside role configuration;
- non-canonical research source shapes; all sources use `research_discoveries`;
- separate `event` and `event_type` spellings;
- Action-specific resource transfer validation outside the generic resource
  reservation contract;
- Story completion based on unrelated behavior when a beat declares a
  followup behavior;
- quality checks that waive grounding because a proposal was accepted.

The only allowed runtime primitives are generic operations such as resource
reservation, transfer, entity state update, relationship delta, capability
evidence, progress and event publication. World meaning belongs in content
configuration.

## Phase 6: Reduce operational surface

Keep one supported real-model audit command and one deterministic smoke
command:

- `scripts/audit_dynamic_evolution_loop.py` for bounded real-provider quality
  checks.
- `scripts/run_long_simulation.py` for non-LLM performance and persistence.

The other audit scripts may remain as historical fixtures during the refactor,
but new behavior must not require another one-off script. Each retained script
must state whether it is deterministic, model-backed, or a replay.

## Non-goals during convergence

- No new Agent role.
- No new world-specific behavior branch in Platform.
- No new compatibility format.
- No new Story/God wake mechanism.
- No new UI or provider adapter until the lifecycle and schema phases pass.

## Concrete execution plan

This is the implementation checklist for the current development branch. Work
is intentionally staged so each structural change can be tested before the
next owner is moved. A stage is complete only when its exit checks pass; a
green test that depends on a compatibility branch does not count.

### Stage A — finish definition ownership

**Current state:** complete in commit `f20598e`. Definition validation and
registration live in `platform/definition_runtime.py` and are owned by the
composed `DefinitionService`.

1. Run `git diff --check` and compile the two modules.
2. Run the focused definition, action binding, institution and liveness tests.
3. Confirm that `Platform` resolves each extracted method through the
   `DefinitionService` without a second implementation.
4. Keep later persistence or activity changes in separate commits.

**Acceptance:** dynamic Action + follow-up BT registration, research-source
reachability validation, recipe normalization, and proposal rollback retain
their existing behavior; no proposal service contains a duplicate definition
registry or definition-effect validator.

### Stage B — split world-evolution responsibilities

**Status:** complete in commit `f20598e`. The old
`world_evolution.py` owner has been removed; Platform now imports the four
focused modules `definition_runtime.py`, `entity_proposals.py`,
`story_runtime.py`, and `world_changes.py`.

Split the remaining `WorldEvolutionRuntimeMixin` in this order:

1. `platform/entity_proposals.py`: agent, location, faction, object, resource
   and institution proposals plus reference validation.
2. `platform/story_runtime.py`: Story beat eligibility, source-event binding,
   chapter progress and narrative follow-up registration.
3. `platform/world_changes.py`: generic world-property, relationship, rule,
   capability-evidence and progress changes.
4. Keep the four modules as the direct owners; there is no old facade.

For every move, use this loop: move code without behavior changes, run the
focused tests, search for the old method owner, then remove the old copy. Do
not add a new branch for a world, entity type, or visible name during the
split.

**Acceptance:** each responsibility has one owner, proposal errors still roll
back the complete transaction, and no service imports `Platform` to reach a
private method.

### Stage C — make Platform a composed coordinator

**Status:** the first composition cut is complete. `Platform` now has no
Runtime Mixin base classes and is a small facade. Explicit services own the
API, intent admission, action execution, minute ticks, evolution tasks,
definitions, entities, Story, social work and liveness signals. The current
branch has a constructed method-owner registry as a migration boundary; the
pending Package 1 change makes every cross-service owner explicit and removes
the remaining implicit lookup behavior. The registry is not a new public
extension point.

Harden the explicit service composition and remove its migration proxy. The
constructor creates services in dependency order:

```text
Store/UnitOfWork
  → EventService → WakeService
  → DefinitionService / ActivityService / SocialService / WorldTickService
  → EvolutionService
  → SnapshotService / TimelineService
```

`Platform` keeps the public API and delegates to these services. Each service
gets a narrow context (`store`, transaction helper, target resolver, event
publisher, and configuration projections); it cannot call another service's
private methods. During this stage, keep one transaction boundary per public
operation and preserve active-process resume semantics.

**Acceptance:** `runtime.py` is below 500 lines, `Platform` has no runtime
Mixin base classes, service dependencies are declared, and a single tick
still commits minute effects, activities, outcomes, events, wake tasks, and
clock advancement atomically.

### Stage D — unify persistence ownership

Introduce repository interfaces behind one Unit of Work in this order:

1. `EventRepository` and `ActivityRepository`;
2. `EvolutionRepository` and `MemoryRepository`;
3. `EntityRepository`, `AgentRepository`, and `SocialRepository`;
4. `SnapshotRepository`.

Repositories receive an active transaction and never call `commit()`.
Existing SQLite tables remain the source of truth during this development
phase. Add rollback, restart recovery, and event/projection atomicity tests at
each step.

**Acceptance:** no state-changing repository method can commit independently;
crash recovery requeues claimed evolution tasks; event IDs, revisions and
causation IDs remain stable across replay.

### Stage E — converge AgentView and tools

Create one definition compiler that emits the persisted normalized definition,
the model JSON Schema, and the AgentView projection. Then split Agent code
into `session.py`, `protocol.py`, `audit.py`, and the projection-owned
`agent_view/tool_registry.py` and `agent_view/policies.py`.

Remove legacy aliases and role-specific mutation checks after all callers use
the canonical schema. Keep world history and role memory bounded projections;
do not paste the full event log into prompts. Verify with a model-backed audit
that records context, tool calls, accepted/rejected intents, and resulting
world events without storing secrets or raw hidden reasoning.

**Acceptance:** a world with unrelated attributes, entities and terminology
boots from configuration alone; Story and God can only consume committed
events and submit validated proposals.

### Stage F — real-model and long-run gate

Before declaring convergence complete, run these in order:

```bash
python -m pytest -q <focused suites>
python -m compileall -q src scripts
bash -ic 'source ~/.bashrc && python scripts/audit_dynamic_evolution_loop.py --provider remote_beam'
python scripts/run_long_simulation.py --minutes 10080
python -m pytest -q
python scripts/benchmark_platform.py --agents 1000 --runs 5
```

The model-backed audit must inspect every round's bounded context, tool
arguments, response summary, accepted intent, world event, and follow-up
behavior. It must demonstrate at least ten causal rounds in which an NPC
action wakes Story or God, the proposal is accepted, another NPC receives the
result, and the next behavior changes. “Tool call succeeded” alone is not an
acceptance criterion.

Record the run ID, database path, provider role, latency, prompt/completion
tokens, cache-hit information, and the reason for every wait or retry in
`docs/operations/real-model-evolution-check.md`. Stop and fix the first
causal break; do not mask it by increasing a token limit or adding a
world-specific fallback.

### Change discipline

- One stage and one architectural owner per commit.
- Every moved method has a focused regression test before its old owner is
  deleted.
- No new compatibility format, direct Story/God callback, or per-world
  `if/else` is allowed.
- A real-model failure is recorded with its context and causal chain, then
  fixed at the protocol or configuration boundary that caused it.
- Full slow tests run only at the merge boundary; focused tests run after each
  stage.

## Final acceptance

The refactor is complete when all of the following hold:

- A new world pack with unrelated names, attributes and entities boots without
  Python changes.
- One NPC action can be traced from Intent to Activity, Outcome, Event and
  EvolutionTask in SQLite.
- Story and God are only event consumers and cannot bypass Platform.
- Dynamic God-created Action+BT and research-source evolution still pass the
  independent long probe.
- The targeted evolution suite passes without running the full slow suite on
  every edit.
- The full suite, compile check and benchmark pass once at the merge boundary.

## Immediate execution plan after the 10-round causal probe

The 10-round DeepSeek probe has passed the causal loop gate, but it does not
close the architecture work. The next work is deliberately limited to the
four items below. They are ordered by dependency; do not start a later item
by adding a compatibility shim for an earlier failure.

### 1. Finish the canonical definition compiler — complete

**Files:** `src/sim2/world/definition_schema.py`,
`src/sim2/platform/definition_runtime.py`,
`src/sim2/agent_view/compiler.py`,
`tests/test_definition_schema.py`, `tests/test_action_binding.py`.

Move normalization and semantic validation into the same canonical compiler
that already produces the model JSON Schema. The compiler must emit one
normalized Action/BT/recipe/capability/attribute definition for persistence,
Platform validation and AgentView. Remove the remaining duplicate aliases and
special-case food/research forms while migrating the development fixtures.

**Required checks:** an unrelated world pack boots without Python changes;
an invalid definition is rejected identically at schema and Platform
boundaries; a God-created Action is immediately selectable through its bound
generic BT; no standalone Action remains executable.

**Status:** structural compiler and regression tests passed. The remaining
semantic checks are intentionally instance-owned and are covered by the
existing action-binding and vertical-slice suites.

**Stop condition:** any fix that adds a world-name, visible-name, or legacy
schema branch stops this item and is replaced by a compiler or configuration
fix.

### 2. Finish AgentEngine protocol boundaries — complete

**Files:** `src/sim2/agents/engine.py`, `src/sim2/agents/session.py`,
`src/sim2/agents/protocol.py`, `src/sim2/agents/tool_executor.py`,
`src/sim2/agents/audit.py`, `tests/test_agent_engine.py`.

Extract provider calls, response normalization, tool-call identity,
conversation trimming and ReAct iteration ownership. The Platform generates
the persisted call identity; the model may only supply function name and
arguments. Keep a bounded stable prefix and a bounded suffix, and record the
truncation reason, prompt size, completion size, cache usage, latency and
accepted event IDs.

**Status:** provider protocol extraction and focused regression tests passed;
the remaining work in this stage is operational observation during the final
real-provider gate.

**Required checks:** provider errors become explicit failed turns; a tool
schema error is not rewritten into `wait`; reflection, Story and God outputs
retain their original reason; audits never contain keys, proxy values or raw
hidden reasoning.

### 3. Close generic liveness and balance defects

**Files:** world configuration under `worlds/`, generic demand/regeneration
primitives under `src/sim2/platform/` or `src/sim2/simulation/`,
`tests/test_evolution_liveness.py`, and the deterministic-run report.

Investigate every depleted vital, repeated proposal and unfinished activity
from the one-week run. Express regeneration, consumption, recovery and
maintenance as configuration or generic primitives. Do not increase a single
starter value as a patch and do not add a Starter Village branch.

**Required checks:** a depleted vital has a configured recovery path or an
explicit terminal consequence; repeated work changes evidence, progress or
has a declared maintenance reason; a long action has explicit completion,
interruption and simulation-end semantics.

**Current evidence:** the one-week deterministic run first exposed the smoke
provider's actual defect: it cycled through every offered behavior and bought
food at high satiety, exhausting one merchant's coins while market stock still
existed. The deterministic provider now follows the configured ordered
`drive_tags` and current drive urgency, and the food behaviors declare
`satiety` directly as their primary drive. The final 10080-minute rerun reached
the end with zero starvation damage, zero incapacitations, zero rejected turns,
zero provider errors and no degeneration flags. This is a generic test-harness
policy over the rendered configuration, not a Starter Village branch; resource
and need effects remain Platform/configuration driven.

**Performance evidence:** caching the revision-stable visible-entity map and
definition projections reduced the same 120-minute cProfile sample from
21.6s to 10.3s and cut JSON decodes from about 696k to 171k. The cache is
invalidated by committed world revisions, so dynamic God definitions remain
visible without a world-specific branch.

**Research liveness evidence:** one real DeepSeek run reached a genuine
absorbing state after God rejected a near-duplicate question and the NPC had
only the same archive inspection left. The generic research-trigger prompt
now tells God to shift to a distinct causal dimension when a source is
exhausted, while retaining the platform duplicate guard. The next 10-round
run created provenance, calibration and sign-off sources and passed with no
absorbing-state flag.

**Real Starter Village evidence:** a one-day DeepSeek audit reached minute
1440 with 194 sessions, zero provider errors, all nine NPCs at health 100,
and no starvation damage. Two quality failures were traced to a protocol
sentence splitter treating decimal points as sentence boundaries; the parser
fix passed its regression tests and a real 1440→1450 resume session passed.

The final 1000-agent benchmark completed five measured runs with p95 tick time
**136.188 ms**, no failed agents, and world revision 10.

The static concurrency review identified a real tick race: `step()` used to
read `world_minute` before acquiring `BEGIN IMMEDIATE`, so two threaded control
requests could both schedule the same next minute. `WorldTickService.step()` now
reads the clock inside the transaction, and `tests/test_tick_concurrency.py`
forces the old interleaving and verifies that committed ticks are exactly
minutes 1 and 2. The HTTP read projections now use the same Store read lock,
and `world_snapshot()`/`get_meta()` are serialized with the writer on the
shared SQLite connection.

The Beam review also exposed a provider-boundary issue rather than a world
causal defect. Beam's native tool calls sometimes returned whitespace-padded
keys, dotted nested paths and malformed Action parameter shapes. The Beam
adapter now repairs only that wire representation, while Platform still
rejects invalid canonical definitions; malformed definitions no longer crash
the Runtime. A real Beam run remains recorded as a provider-quality failure
when it exhausts its output budget without a valid final Intent.

After these changes the full suite is **422 passed**; compileall and diff
checks pass. The supported DeepSeek causal audit, deterministic week and
benchmark remain the authoritative world-quality evidence. Beam remains an
external provider compatibility failure until it can emit a bounded valid
Intent consistently.

### 4. Run the final model-backed acceptance gate

Use an interactive shell for every provider run:

```bash
bash -ic 'source ~/.bashrc && python scripts/audit_dynamic_evolution_loop.py \
  --provider remote --rounds 10 --runtime-dir /tmp/sim2-evolution-final'
bash -ic 'source ~/.bashrc && python scripts/audit_dynamic_evolution_loop.py \
  --provider remote_beam --rounds 10 --runtime-dir /tmp/sim2-evolution-beam'
python scripts/run_long_simulation.py --minutes 10080
```

For each round, review the bounded context, tool arguments, output reason,
accepted intent, committed event, wake task, next NPC choice and world delta.
The remote run must show at least ten causal rounds with changed NPC behavior
and changed world state. A Beam transport failure is recorded as a provider
failure and cannot be counted as a world-quality pass. The deterministic run
must report every starvation, deadlock, duplicate proposal and unfinished
activity; unexplained cases block completion.

### Evidence and commit order

1. Commit the typed-repository probe migration and its focused test/docs
   evidence.
2. Commit the canonical compiler and its regression tests.
3. Commit AgentEngine protocol extraction and audit tests.
4. Commit generic liveness/configuration fixes and the updated one-week
   report.
5. Run the full suite, compile check and benchmark once, then record the
   acceptance evidence in `docs/operations/real-model-evolution-check.md`.

The current probe evidence is `/tmp/sim2-evolution-loop-post-architecture/`.
It completed ten rounds with `status=passed`, no rejected turns or LLM errors,
one conserved supply transfer, repeated generic research on newly created
objects, and grounded Story/God continuation. This evidence is a progress
record; it does not waive the remaining compiler, AgentEngine or one-week
liveness gates.

### Post-core web surfaces

Configuration authoring is a product layer after the runtime convergence gate,
not another mutation path. The planned World Authoring Web workbench is
specified in `docs/design/world-authoring-web.md`. It must generate forms and
BT graphs from the same World Template Schema, Definition IR and expression
AST used by bootstrap and God proposals. Its compile, reference, semantic and
temporary SQLite smoke checks must call the existing authoring/Platform
services; the browser must not duplicate validation or write a running
instance. The existing TUI/HTTP observer surface remains separate from this
draft-package editor. The Runtime Web Client is specified separately in
`docs/design/web-client.md`: it consumes visibility-safe map, observer and
timeline projections with incremental event cursors, while player commands
still go through `ControlService` and Platform. Neither browser surface gets
direct SQLite or LLM/provider access.

The post-AST 10-round causal audit initially exposed a real liveness defect in
the declarative probe: `inspect_archive` had no configured terminal condition
after the stability-review threshold, so the same valid Action could repeat
without a new consequence. The probe now gates that Action with
`world.relay_stability < 0.55`, and the audit treats a ready final-round
Story/God task as pending continuation rather than absorbing state. The
corrected DeepSeek run at `/tmp/sim2-evolution-after-stability-gate/` passed
10/10 with a God-created source, changed NPC choices and grounded Story
continuations.

The latest convergence evidence after removing the metadata façade and adding
the Runtime Web/Authoring MVP is recorded in
`docs/operations/real-model-evolution-check.md`: **433 tests passed**, the
deterministic week reached minute `10080` with no degeneration flags, the
1000-agent benchmark p95 was **165.524 ms**, and the final remote 10-round
causal probe at `/tmp/sim2-web-final-causal/` passed with four distinct
God-created research objects plus one conserved Action/BT. The browser test
also covers opening an existing package, guided editing, compile, smoke, export,
path confinement and the explicit WorldBuilder route. The remaining items in
this plan are historical cleanup and provider-specific operational checks, not
an unverified web design claim.
