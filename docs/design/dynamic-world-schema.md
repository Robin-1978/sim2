# Dynamic World Schema

The Platform is a generic executor. A world package is the source of truth
for attributes, needs, capabilities, entities, actions, behavior trees,
conditions, effects, schedules, and Agent-facing wording. A new world may use
different names and different dimensions without changing Platform code.

## Engine envelope versus world data

The runtime keeps a small execution envelope for every person: current
location, execution status, active behavior/action, reservations, conditions,
inventory, and capabilities. These fields describe how the engine runs work.
Health, energy, hunger, morale, magic, reputation, or any other world concept
belong in the imported attribute and need definitions. Their bounds, defaults,
labels, prompts, cost direction, and recovery rules come from configuration.

Actions use generic effect forms such as `attribute_delta`, `need_delta`,
`inventory_add`, `entity_state_set`, `world_property_delta`, and
`relationship_delta`. Conditions resolve configured paths and aliases. The
engine must not branch on a starter-world name, item, location, or need.

Stocks are entities, not an implicit global variable. A resource declares its
physical `location_id` and may declare `storage_scope`, `owner_id`, and
`access_policy`; a reservation resolves that configured entity and changes its
quantity transactionally. This permits a story to add another settlement with
its own warehouse and stock without changing Platform code. A God structural
proposal may add a location with declarative routes/tags, but it cannot grant a
positive stock; production, gathering, trade, or delivery behavior must create
that quantity. See [Storage, settlements, and map growth](storage-and-settlements.md).

## Emergent social institutions

An institution is absent until a person proposes it and the world accepts it.
Before activation there is no metric, ledger, or policy state. A proposal is an
auditable event and cannot change resources. Acceptance creates declarative
definitions in SQLite: institution, policy, ledger fields, evidence selectors,
period, and safety constraints. Future validated events may then append ledger
entries. Historical events are excluded unless the accepted policy explicitly
requests evidence-backed backfill.

This lets a world invent a public-work ledger, ration policy, reputation code,
or an entirely different institution without adding a new Python branch.

## Verification status

- Character attributes and needs come from the imported schema; the orbital
  relay fixture runs with integrity, charge and signal clarity and contains no
  health, energy or satiety state.
- Institutions, policies and ledgers are generic SQLite definitions activated
  through validated proposal intents.
- Research mitigation, artifact installation and boundary investigation are
  configured follow-up behaviors with persisted outcomes.
- The different-ontology world fixture, runtime smoke test and cross-reference
  validator run without changing Platform code. A fresh real-provider long run
  remains the final semantic release gate for the Starter Village package.

## Runtime definition evolution

YAML is an authoring and bootstrap format. After a SQLite instance is created,
its metadata and definition tables are authoritative: `attributes` and
`world_properties` live in instance metadata, while Actions, behavior trees,
recipes and capabilities live in `*_def` tables. The original YAML is not read
during minute advancement.

God may submit one `propose_definition` intent for a declarative attribute,
world property, capability, Action, behavior tree or recipe. Platform validates
field types, identifiers, references, node depth, duration, effect primitives,
capacity and duplicate names in one transaction. A valid proposal writes the
normalized definition, a `definition.registered` event and the intent result to
SQLite. A behavior can optionally be assigned to named characters; otherwise it
is registered but appears in no executable catalog. A new attribute also writes
its configured default into every existing character projection.

The proposal never executes the new definition. Characters can use a newly
registered behavior only in a later decision view, and its Actions still pass
normal preconditions, reservations, time, capacity, postconditions and effects.
God cannot use this channel to grant inventory, resource quantities, needs,
relationships or production outcomes. Those values still require an observed
behavior result.

Story and God share the same Agent Engine but have different tool scopes. Story
may activate evidence-backed beats, progress and registered BT unlocks. God
may create mechanisms and structural definitions. Story can surface a need for
new mechanics, but Platform does not let narrative text register an Action or
attribute directly.
