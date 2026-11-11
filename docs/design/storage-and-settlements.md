# Storage, settlements, and map growth

The world does not have one implicit global warehouse. A stock is a normal
world entity whose configured location and access policy determine where it
can be used. In the Starter Village package, `village_food` is a `shared_stock`
resource located at `granary`; its `village_food` world-property entry is only
a compatibility projection for the configured food-crisis rule. The resource
entity is the quantity that actions reserve and change.

Use this shape for a local store:

```yaml
resources:
  - id: south_town_food
    type: shared_stock
    location_id: south_town_granary
    amount: 0
    storage_scope: settlement
    owner_id: south_town
    access_policy: town_ration
```

`location_id` is physical placement. `storage_scope`, `owner_id`, and
`access_policy` are optional world metadata; a world may use them in declared
conditions and permissions. Platform does not interpret names such as
`village_food`, `town_a_food`, or `granary` as built-in concepts. An action
selects the configured resource entity, verifies its location and conditions,
reserves its quantity, and records the resulting `resource.changed` event.

An additional settlement is therefore ordinary content, not a second runtime:

1. Add a settlement location and its connected warehouse/service locations.
2. Add local stocks, objects, agents, factions, and routes at those locations.
3. Give local actions and behavior trees the resource and location references
   they need; access remains a declarative precondition.
4. Add public facts and discovery entries so Agents learn the settlement by
   travel, exploration, records, or testimony instead of receiving the whole
   map.

If a settlement emerges during play, God may propose a structural `location`
entity with `state.routes`, `state.tags`, `state.public`, and optional map
metadata. Platform promotes those structural fields into the location
definition, so the new node is usable by the generic navigation graph. A
positive stock is never created as a God reward: create an empty stock or
storage structure first, then let a configured production, gathering, trade,
or delivery behavior put material there. A two-way route must be declared on
both endpoints (or be introduced by a future declarative route definition);
route direction is explicit.

The distinction is deliberate:

- **World property**: a compact scalar used for a global rule or trigger.
- **Entity resource**: a quantity with a physical location and optional owner
  or institution scope.
- **Agent inventory**: material currently carried by one participant.
- **Institution ledger**: contributions, quotas, or claims created only after
  an institution is proposed and activated.

This keeps a second town from accidentally sharing the first town's stock,
while still allowing a world-level trend to aggregate all configured resource
entities. New packages should prefer entity resources and use a world-property
projection only when a global trigger genuinely needs one.

## Module boundary

`src/sim2/platform/world_evolution.py` remains under `platform` on purpose. It
is the execution barrier for validated proposals: it checks definitions,
creates entities, applies declarative changes, and writes SQLite events in the
same transaction as state. The `src/sim2/evolution/` package owns wake
selection, Story eligibility, and proposal evaluation policy. Moving the
executor into `evolution/` would mix “which Agent should wake?” with “what
state mutation is legal?”, and would make the Platform boundary less clear.

If the executor grows further, split it into focused Platform mixins or
services (definition registration, structural entities, and proposal
evaluation) while keeping the import under `platform`; do not move it merely
because the filename contains `evolution`.
