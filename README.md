# sim2

sim2 is a configuration-driven, text-first adaptive world simulation framework. A world package defines its setting, entities, actions, behavior trees, rules, memory seeds, evolution schedule, and human entry. Agents make decisions through the LLM boundary, while Platform validates intents and is the only authority that changes world state. Bootstrap creates one independent SQLite database per instance, so each run can evolve and be replayed independently.

The first world is **Starter Village (雾溪村)**: a settlement at the edge of Greywood, built around a weakening pact and a hidden root gate. Food, water, health, patrols, trade, relationships, exploration, player choices, and world-level interventions can change the story. Its complete setting and extension map are in [the world guide](docs/worlds/starter-village.md); the importable package is [`worlds/starter_village/`](worlds/starter_village/), and the concise configuration catalog is [here](docs/worlds/starter-village-catalog.md).

The repository also includes **Three Lanterns Town (三灯镇)**, a compact three-character validation world. Its water keeper, boundary watcher, and reed trader/researcher exercise the same generic runtime through inspection, exploration, research, gathering, testimony, and evidence-gated Story progression. The package is [`worlds/three_lanterns_town/`](worlds/three_lanterns_town/); the unified authoring source is [`examples/three_lanterns_town.yaml`](examples/three_lanterns_town.yaml).

Each individual Agent has two phases: a short decision phase that selects a Platform-validated behavior or interaction, and a scheduled reflection/evolution phase that turns useful outcomes into memory, beliefs, goals, or new behavior proposals. Each world also has Story and God Agents: Story turns validated discoveries into configured or evidence-grounded narrative beats, while God observes structured world outcomes and proposes validated world changes. Long actions continue in Platform without an LLM call every minute.

## Quick start

```bash
python -m pip install -e .
sim2 init --instance-id mist-village-001
DB=~/.sim2/instances/mist-village-001/world.sqlite3
sim2 status --db "$DB"
sim2 health --db "$DB"
```

The installed package includes Starter Village. Use `--world` to select another
bundled world or `--config worlds/my_world` to initialize a world package from
the current checkout.

Open the live TUI with a map, timeline, character table and pause/step controls:

```bash
sim2 tui --db "$DB" --observer --run --provider remote_beam
sim2 tui --db "$DB" --player-id player_one --origin courier --name Ari --run --provider remote_beam
```

The complete client guide is [here](docs/client.md). The smaller text interface
remains available for scripts and diagnostics:

```bash
sim2 mud --db "$DB" --player-id player_one --origin courier --name Ari
sim2 mud --db "$DB" --observer
```

In observer mode, use `timeline 50` to read the latest public events as a chronological third-person account, or `events 50` for the compact technical event list. The HTTP equivalent is `GET /timeline?limit=50&after=0`; pass the returned `next_after_event_id` as `after` to continue without duplicates. A player enters through the same text interface without `--observer` and sees only their permitted local view.

Start the local HTTP control API when another client needs status, stepping, events, dialogue, or trade operations:

```bash
sim2 serve --db "$DB" --host 127.0.0.1 --port 8080
```

Run the autonomous Platform runtime after configuring providers in `~/.sim2/llm.json`:

```bash
sim2 simulate --db "$DB" --minutes 1440
```

For a real-provider run using one configured provider for every Agent role, use `--provider remote` (the value is a provider name, never an API key). The runtime still advances one Platform tick at a time:

```bash
sim2 simulate --db "$DB" --minutes 1440 --provider remote
```

Memory retrieval uses SQLite FTS plus task-conditioned ranking. To enable the optional local BGE ONNX reranker with the cached model, install its small runtime in the PyTorch environment and point `SIM2_BGE_MODEL` at the model directory; the world package already enables the semantic-similarity switch and falls back safely when the variable is absent:

```bash
/disk/miniconda3/envs/pytorch/bin/pip install onnxruntime tokenizers
export SIM2_BGE_MODEL=/path/to/bge-small-zh-v1.5
```

The text interface supports movement, messages, offers, counteroffers, acceptance/rejection, waiting, and public world telemetry. HTTP exposes the same Platform operations for another client.

## Worlds and extension

The first validation world is [Starter Village](docs/worlds/starter-village.md), with its importable package in [`worlds/starter_village`](worlds/starter_village/). It is a small settlement around a failing pact, with food, water, health, patrol, exploration, trade, relationships, hidden locations, scheduled reflection, and God-led world change.

To add a world, provide a package with `manifest.yaml`, setting and world state, locations, objects, factions, agents, actions, behavior trees, rules, evolution schedule, player template, and optional prompts. `manifest.config_version` is recorded in the new SQLite instance. Reusing an instance ID is rejected; initialize a new ID for every world instance.

Use `sim2 world-schema`, `sim2 build-world`, `sim2 validate-world` and
`sim2 describe-world` for the complete authoring workflow. WorldBuilder can
draft a compact template from a brief through a configured provider, but it
must validate and submit the exact same template before YAML is written. See
[World authoring and WorldBuilder](docs/world-authoring.md).

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
# Full one-week context/output/world causality audit (runs every role through one provider)
bash -ic 'source ~/.bashrc >/dev/null 2>&1; python scripts/audit_real_world.py \
  --minutes 10080 --provider remote --model deepseek-v4.1-flash \
  --output artifacts/real-agent-audit-week.json --auto-resume'
# Review every recorded session and the persisted SQLite execution closure
python scripts/review_audit.py artifacts/real-agent-audit-week.json
# Export every recorded world event, including one-minute time advances and
# chat text, without exporting Agent prompts or call details
python scripts/export_timeline.py --db "$DB" --output artifacts/world-timeline.md
# Export character-centred storylines from the same public event history
python scripts/export_storylines.py --db "$DB" --output artifacts/character-storylines.md
```

`audit_real_world.py` writes a reviewable JSON artifact under `artifacts/` with
each session's initial AgentView, rendered ReAct prompts, tool results, final
Intent, quality checks, and the resulting SQLite world projection. Use
`SIM2_BGE_MODEL=disabled` for a fast LLM/world-loop isolation run; omit it to
exercise the configured local BGE reranker.

Licensed under [Apache License 2.0](LICENSE).
