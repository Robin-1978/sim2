# Large World Expansion Plan

This document plans the next world scale for sim2. It is design only; it does not add runtime code or change the Starter Village package.

## Goal and scale boundary

The large world is a network of regions rather than one giant scene. A region contains settlements, wilderness, routes, resources, factions, local calendars, and event sources. Starter Village becomes one region in that network. The world package supplies the initial geography and history; SQLite owns every later change.

The world is layered:

1. **World layer** — cosmology, global rules, calendar, climate bands, long arcs, and God Agent policy.
2. **Region layer** — routes, settlements, ecosystems, markets, factions, and regional events.
3. **Local layer** — locations, objects, relationships, active Actions, conversations, and perception.

Agents receive the local layer, relevant region summaries, known routes, and retrieved facts. They do not receive the whole map. God Agent receives cross-region indicators and event outcomes, then submits the same validated world intents as in a small world.

## Time, tick, and level of detail

`world_minute` remains the canonical clock and is stored in SQLite. A minute is a precision unit, not an LLM decision cadence. Every minute is still a logical tick: Platform applies rules, Action progress, resource locks, and event checks in order and commits that tick before starting the next one. An `advance(N)` request is only an API envelope around N sequential ticks; it never jumps over a minute. Full Agent sessions run only at semantic boundaries inside those ticks.

The scheduler uses three simulation levels:

- **Active local region**: exact Action progress, perception, relationships, and interaction ticks in the AgentView.
- **Known nearby regions**: the same per-minute rules, with fewer entities materialized in the current projection.
- **Distant regions**: the same per-minute rules and event order, with compact state projections until an observer or Agent obtains a reason to inspect them.

Each tick records its minute and generated events. Performance comes from compact projections, prepared queries, and one transaction per tick, never from skipping logical minutes.

## Day, night, season, and weather

The calendar is configuration data. Platform derives `day_phase`, season, weather, light, temperature, and visibility from `world_minute` and the current region. Rules then affect perception, route safety, available Actions, resource production, sleep recovery, market hours, and encounter probability. The AgentView exposes only the result needed for a decision:

```text
World time: Day 3, 21:40
Phase: night | Season: late autumn | Weather: light fog
Visibility: low | Safe travel: village routes only
```

Agents never infer hidden time rules from a raw tick number. Platform remains the authority for all time-dependent effects.

## Movement and information

Travel is a long Action with an ETA, route knowledge requirement, resource lock, exposure cost, and arrival event. A traveler cannot use a route or communicate as if already present; interaction checks use the current location or an explicitly configured transit policy. Exploration, testimony, maps, public records, and faction channels create source-scoped knowledge with confidence. Region summaries are not truth and never replace local evidence.

## Social and world evolution

Factions own norms, territories, resources, relations, and policy objectives. Markets and migration are configured event producers, while relationships and reputation remain per-Agent beliefs plus Platform evidence. Cross-region events propagate through routes and information channels, not through global context injection.

God Agent receives periodic global indicators, unresolved proposals, and measured outcomes. It may propose an entity, rule, faction, relationship, or event; Platform validates references, scope, capacity, causality, and resource effects before committing it. A world event is not considered successful until a later God review observes its actual effect.

## Future configuration contract

Future world packages should add data files beside the existing package contract:

- `regions.yaml` — region boundaries, summaries, visibility, and LOD policy.
- `routes.yaml` — directed routes, travel cost, hazards, and transit interaction policy.
- `calendar.yaml` — day phases, seasons, holidays, and weather schedules.
- `ecosystems.yaml` — regeneration, migration, climate, and resource production rules.
- `markets.yaml` — goods, local supply, demand signals, and trading hours.
- `world_events.yaml` — cross-region triggers, propagation, expiry, and observability.

The package remains declarative. Platform supplies generic schedule, route, condition, target, effect, event, and projection engines; no region-specific branches belong in the runtime.
