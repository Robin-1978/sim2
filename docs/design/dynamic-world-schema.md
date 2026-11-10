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

## Implementation order

1. Replace remaining fixed attribute fallbacks with the imported schema.
2. Add generic institution, policy, and ledger tables and proposal/activation
   intents.
3. Add configured follow-up behaviors for research mitigation, artifact
   installation, and boundary investigation.
4. Run a custom-world fixture with different attributes and a 240-minute
   semantic audit before repeating the full 1,440-minute run.
