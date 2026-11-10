# Behavior Duration and Closure Matrix

This matrix is the review baseline for the Starter Village package. Platform
advances exactly one world minute per `step()`. A behavior's displayed time is
computed from its registered Actions, route graph, recipe duration, and
capability modifier; `estimated_minutes` is only a catalog fallback. No
story-specific duration branch belongs in runtime code.

## Configured duration baseline

| Behavior family | Configured work | Expected elapsed time | Review |
|---|---|---:|---|
| Travel | `travel_to` | 5 minutes per route hop | Dynamic; 0 when already there |
| Recovery | `rest` | 60 minutes | Rest restores energy at 2/minute and reduces exposure/attention |
| Food | eat / withdraw / buy | 1 / 15 / 10 minutes, plus travel | Three distinct supply paths |
| Gathering | `gather_resource` | 180 minutes for two units | Finite-node cost: energy 0.25/minute |
| Patrol / investigation | patrol / classify marks | 120 / 90 minutes, plus travel | Patrol cooldown is 720 minutes; classification is separate |
| Communication / negotiation | communicate / negotiate | 15 / 60 minutes | Interaction must use visible participants |
| Research | one sample | 240 minutes, plus travel | Research progress is cumulative; two samples unlock the result |
| Exploration | travel + survey | 300 minutes, plus travel | Exposure is 0.3/minute and one ration is reserved |
| Crafting | meal / ration / medicine / bell | 90 / 120 / 180 / 180 minutes | Recipe definitions own time, inputs, outputs and skill evidence |
| Public measures | water / pact | 120 / 180 minutes, plus travel where configured | Each is hidden until its exact research fact exists, then changes a configured world property |
| Deployment / emergency | install / alarm / boundary defense / attack | 20 / 3 / 120 / 6 minutes, plus travel | Alarm wakes the next response decision; defense lowers verified threat; attack requires a target |

Composite examples use the same calculation: `explore_greywood` is 305
minutes from the north gate (5-minute route plus 300-minute survey),
`install_dawn_bell` is 35 minutes from the smithy, and `research_water` is
245 minutes from the clinic. If the origin changes, the estimate changes with
the configured topology.

## Necessity and closure review

Keep movement, recovery, food, gathering, crafting, communication, research,
exploration, deployment, and threat-response behaviors. They form the loops
that move resources, knowledge, capability evidence, relationships, and world
progress. Keep governance, ration review, patrol, and investigation as
separate behaviors: their results have different consumers and cooldowns.
Conflict mediation, treatment, defense, fleeing, alarm, market purchase, and
ledger recovery remain conditional and should be absent until their configured
conditions are true.

Every completed behavior must produce at least one persisted consequence:
state/inventory/location change, relationship change, knowledge or research
progress, a public event, a world-property delta, or a configured outcome
fact. A report that only appends an event without changing a usable fact is
not a closed loop. The timeline and nightly review consume the persisted
result; Story and God may propose follow-ups but cannot invent the result.

## God Action access decision

God does **not** receive `request_behavior` or an Action executor. Direct
execution would bypass actor location, inventory, capacity, capability,
relationships, and time, making world changes causally ungrounded. God can
observe verified outcomes and propose a rule, relationship, faction strategy,
structural entity, narrative beat, or world progress. A proposed Action/BT
must be declarative, validated and registered by Platform, then selected and
executed by a real actor through the normal duration and cost pipeline.

## Runtime guard

Action `success.condition` may contain a machine-checkable predicate or a
symbolic result contract. Runtime evaluates supported predicates against the
post-action projection inside one savepoint. A false predicate rolls back the
effect and information event together. Symbolic labels remain contracts backed
by the configured effect/information pipeline; no package ID is embedded in
Platform.
