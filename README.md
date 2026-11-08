# sim2

sim2 is a configuration-driven, text-first sandbox game and world simulation framework. A world package defines its setting, entities, actions, behavior trees, rules, memory seeds, evolution schedule, and player entry. Agents make decisions through the LLM boundary, while Platform validates intents and is the only authority that changes world state. Bootstrap creates one independent SQLite database per instance, so each run can evolve and be replayed independently.

The first world is **Starter Village (雾溪村)**: a settlement at the edge of Greywood, built around a weakening pact and a hidden root gate. Food, water, health, patrols, trade, relationships, exploration, player choices, and world-level interventions can change the story. Its complete setting and extension map are in [the world guide](docs/worlds/starter-village.md); the importable package is [`worlds/starter_village/`](worlds/starter_village/), and the concise configuration catalog is [here](docs/worlds/starter-village-catalog.md).

Each individual Agent has two phases: a short decision phase that selects a Platform-validated behavior or interaction, and a scheduled reflection/evolution phase that turns useful outcomes into memory, beliefs, goals, or new behavior proposals. Each world also has a God Agent that observes structured world outcomes and proposes validated world changes. Long actions continue in Platform without an LLM call every minute.

## Quick start

```bash
python -m pip install -e .
sim2 init --config worlds/starter_village --instance-id mist-village-001
DB=~/.sim2/instances/mist-village-001/world.sqlite3
sim2 status --db "$DB"
```

Open the text interface as a player or observer:

```bash
sim2 mud --db "$DB" --player-id player_one --origin courier --name Ari
sim2 mud --db "$DB" --observer
```

Run the automatic Agent/Platform loop after configuring providers in `~/.sim2/llm.json`:

```bash
sim2 simulate --db "$DB" --minutes 1440
```

For a real-provider run using one configured provider for every Agent role, use `--provider remote` (the value is a provider name, never an API key):

```bash
sim2 simulate --db "$DB" --minutes 1440 --provider remote
```

The text interface supports movement, messages, offers, counteroffers, acceptance/rejection, waiting, and public world telemetry. HTTP exposes the same Platform operations for another client.

## Worlds and extension

The first validation world is [Starter Village](docs/worlds/starter-village.md), with its importable package in [`worlds/starter_village`](worlds/starter_village/). It is a small settlement around a failing pact, with food, water, health, patrol, exploration, trade, relationships, hidden locations, scheduled reflection, and God-led world change.

To add a world, provide a package with `manifest.yaml`, setting and world state, locations, objects, factions, agents, actions, behavior trees, rules, evolution schedule, player template, and optional prompts. `manifest.config_version` is recorded in the new SQLite instance. Reusing an instance ID is rejected; initialize a new ID for every world instance.

The planned multi-region expansion is described in [Large World Expansion Plan](docs/worlds/large-world-plan.md). It keeps Starter Village as a region and adds declarative region, route, calendar, ecosystem, market, and cross-region event data without adding world-specific runtime branches.

## Development

```bash
python -m pytest -q
python -m compileall -q src scripts
python scripts/benchmark_platform.py --agents 1000 --runs 5
python scripts/run_long_simulation.py --minutes 10080
```

Real-provider checks use the configured local or remote OpenAI-compatible provider without printing credentials:

```bash
python scripts/verify_agent_models.py
python scripts/verify_core_interaction.py --provider remote
```

Licensed under [Apache License 2.0](LICENSE).
