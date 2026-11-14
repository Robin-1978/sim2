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

### Current position

The repository is between the first composition cut and the final removal of
migration façades:

1. The Platform facade and the world-evolution split are in place.
2. Unit of Work and typed repositories are in place for state-changing paths.
3. Agent tool registry, executor, session and runtime policy have been split.
4. The explicit cross-service owner conversion is now tested and committed;
   the Platform facade no longer relies on implicit service-method scanning for
   the migrated paths.
5. The remaining architectural debt is the Store read façade, the duplicate
   definition compiler/validation paths, and the still-large AgentEngine.
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
persistence domain. UnitOfWork, Platform services, AgentView, perception,
memory, simulation, Actions and API projections use those typed objects.
Repository-internal cross-domain calls also use named repositories. The
dynamic Store method façade has been removed.

**Completed:** the façade removal and test migration are ready to commit after
the full suite passed. Store remains responsible only for SQLite connection,
transaction, metadata, snapshots and lifecycle primitives.

**Gate:** no repository commits independently; event and projection writes are
atomic; claimed evolution tasks are recovered after restart; replay preserves
event IDs and revisions.

#### Package 4 — one definition compiler and one validation contract

**Files:** `src/sim2/world/schema.py`,
`src/sim2/platform/definition_runtime.py`,
`src/sim2/agent_view/compiler.py`, `src/sim2/agents/tool_registry.py`, and
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
`tool_registry.py`, `tool_executor.py`, `runtime_policy.py`, `audit.py`,
`src/sim2/agent_view/compiler.py`.

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
bash -ic 'source ~/.bashrc && python scripts/audit_dynamic_evolution_loop.py --provider beam'
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
| `platform/definition_runtime.py`, `entity_proposals.py`, `story_runtime.py`, `world_changes.py` | split modules | Definition validation, entity proposals, Story beats and world changes now have separate owners |
| `persistence/store.py` | 419 lines | Typed repositories exist, but read-heavy callers still use the migration façade |
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
| Behavior admission/execution | `platform/behavior_service.py` | explicit services |
| World minute | `platform/world_tick_service.py` | explicit service |
| Evolution tasks | `platform/evolution_service.py` | explicit service and UoW task repository |
| Definitions/entities/Story/world changes | `platform/definition_runtime.py`, `entity_proposals.py`, `story_runtime.py`, `world_changes.py` | separate owners |
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

**Status:** tool ownership is split into `agents/tool_registry.py`,
`agents/tool_executor.py`, and `agents/tool_protocol.py`; `agents/tools.py` is
only the public import façade. `AgentTurn` and role/evolution routing policy
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

### 4. Run the final model-backed acceptance gate

Use an interactive shell for every provider run:

```bash
bash -ic 'source ~/.bashrc && python scripts/audit_dynamic_evolution_loop.py \
  --provider remote --rounds 10 --runtime-dir /tmp/sim2-evolution-final'
bash -ic 'source ~/.bashrc && python scripts/audit_dynamic_evolution_loop.py \
  --provider beam --rounds 10 --runtime-dir /tmp/sim2-evolution-beam'
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
