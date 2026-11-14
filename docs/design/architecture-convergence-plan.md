# Architecture Convergence Plan

## Purpose

The current Runtime has the right causal direction but too many overlapping
orchestration paths. The next phase is structural convergence: reduce the
number of lifecycle owners, remove compatibility branches, and make Platform
services depend on explicit protocols.

This plan applies to the current development branch. Compatibility with
retired schemas is not a goal. A definition or protocol is either migrated to
the new form or rejected during development.

## Baseline before convergence

The current core contains approximately 21,000 lines of Python. The largest
modules are:

| Module | Approximate size | Current concern |
|---|---:|---|
| `platform/runtime.py` | 1,700 lines | Tick loop, state mutation, evolution scheduling, timeline, snapshots and public API are mixed |
| `platform/activity.py` | 1,500 lines | Action/BT execution, research, Story completion, institutions and event wakeups are mixed |
| `platform/world_evolution.py` | 2,000 lines | Definition validation, entity proposals, Story beats and world changes share one module |
| `persistence/store.py` | 1,750 lines | All repositories and metadata are exposed through one store |
| `agent_view/compiler.py` | 1,900 lines | Decision, Story and God views share availability, parameter and rendering logic |
| `agents/tools.py` | 1,200 lines | Schemas, dispatch, role policy, compatibility normalization and proposal rules are mixed |
| `agents/engine.py` | 1,100 lines | ReAct protocol, provider messages, task policy, intent normalization and audit are mixed |

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
| Platform facade | `platform/runtime.py` | 81-line facade; no Runtime Mixin inheritance |
| Runtime API | `platform/api_service.py` | explicit service |
| Behavior admission/execution | `platform/behavior_service.py` | explicit services |
| World minute | `platform/world_tick_service.py` | explicit service |
| Evolution tasks | `platform/evolution_service.py` | explicit service and UoW task repository |
| Definitions/entities/Story/world changes | `platform/definition_runtime.py`, `entity_proposals.py`, `story_runtime.py`, `world_changes.py` | separate owners |
| Persistence boundary | `persistence/uow.py` | event and evolution repositories migrated; Store façade remains for the next phase |

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

**Status:** the first persistence boundary is in place: `UnitOfWork` owns the
SQLite transaction and exposes typed `EventRepository` and
`EvolutionRepository` entry points. All Platform event writes and evolution
task operations now use these repositories, and rollback coverage is present.
The remaining repositories must be migrated one domain at a time before the
broad Store facade is removed.

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
- `tool_registry.py`: schemas and role permissions.
- `audit.py`: prompt, response and usage persistence.
- `policies.py`: role instructions only.

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
- separate `research_discovery` and `research_discoveries` formats;
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
Runtime Mixin base classes and is a 63-line facade. Explicit services own the
API, intent admission, action execution, minute ticks, evolution tasks,
definitions, entities, Story, social work and liveness signals. The shared
`PlatformContext` proxy is a temporary migration seam; the remaining Stage C
work is to replace its cross-service name lookup with narrow typed callbacks
as each service is cleaned up.

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
Mixin base classes, and a single tick still commits minute effects,
activities, outcomes, events, wake tasks, and clock advancement atomically.

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
into `session.py`, `protocol.py`, `tool_registry.py`, `audit.py`, and
`policies.py`.

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
bash -ic 'source ~/.bashrc && python scripts/audit_dynamic_evolution_loop.py --provider beam'
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
