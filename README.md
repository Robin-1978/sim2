# sim2

sim2 is a configuration-driven, text-first adaptive world simulation framework. A world package defines its setting, entities, actions, behavior trees, rules, memory seeds, evolution schedule, and human entry. Agents make decisions through the LLM boundary, while Platform validates intents and is the only authority that changes world state. Bootstrap creates one independent SQLite database per instance, so each run can evolve and be replayed independently.

The first world is **Starter Village (雾溪村)**: a settlement at the edge of Greywood, built around a weakening pact and a hidden root gate. Food, water, health, patrols, trade, relationships, exploration, player choices, and world-level interventions can change the story. Its complete setting and extension map are in [the world guide](docs/worlds/starter-village.md); the importable package is [`worlds/starter_village/`](worlds/starter_village/), and the concise configuration catalog is [here](docs/worlds/starter-village-catalog.md).

The repository also includes **Three Lanterns Town (三灯镇)**, a compact three-character validation world. Its water keeper, boundary watcher, and reed trader/researcher exercise the same generic runtime through inspection, exploration, research, gathering, testimony, and evidence-gated Story progression. The package is [`worlds/three_lanterns_town/`](worlds/three_lanterns_town/); the unified authoring source is [`examples/three_lanterns_town.yaml`](examples/three_lanterns_town.yaml).

Each individual Agent has two phases: a short decision phase that selects a Platform-validated behavior or interaction, and a scheduled reflection/evolution phase that turns useful outcomes into memory, beliefs, goals, or new behavior proposals. Each world also has Story and God Agents: Story turns validated discoveries into configured or evidence-grounded narrative beats, while God observes structured world outcomes and proposes validated world changes. Long actions continue in Platform without an LLM call every minute.

## Quick start

The fastest path is the browser client. It opens a world launcher, creates
the first SQLite instance from a bundled template, and lets you keep playing
the same character later:

```bash
python -m pip install -e .
sim2 play
```

`sim2 play` starts the local server and opens `http://127.0.0.1:8080/`.
On a first run there is no instance yet, so the page shows the world templates;
creating one from the page is enough. Use `sim2 play --no-browser` on a
headless machine. The launcher prints the URL and a local operator token.

The same things are available from the CLI:

```bash
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

The complete client guide is [here](docs/client.md). `sim2 mud` provides the
script-friendly plain-text client with the same playable projection as the web
client:

```bash
sim2 mud --db "$DB" --player-id player_one --origin courier --name Ari
sim2 mud --db "$DB" --observer
```

The player view starts with the world clock, location, goal, need meters,
routes and numbered actions. Use `do 1` to submit an action, `go 1` to travel,
and `wait [minutes]` to advance a manual session. `say <target> <message>` asks
the configured Agent provider to answer immediately; `messages` shows the
transcript. Pass `--provider <name>` to choose that provider explicitly.
Observer mode adds `world`, `events`, `pause`, `resume`, `step`, `evolution`
and `metrics`.

In observer mode, use `timeline 50` to read the latest public events as a chronological third-person account, or `events 50` for the compact technical event list. The HTTP equivalent is `GET /timeline?limit=50&after=0`; pass the returned `next_after_event_id` as `after` to continue without duplicates. A player enters through the same text interface without `--observer` and sees only their permitted local view.

Start the local HTTP control API when another client needs status, stepping, events, dialogue, or trade operations:

```bash
sim2 serve --host 127.0.0.1 --port 8080
```

With no `--db`, `sim2 serve` opens the most recently activated instance under
`~/.sim2`; use `--instance-id <id>` to choose one explicitly, or `--db "$DB"`
for an instance outside that root. The terminal prints the URL, active database
and a local Operator token. Add `--log-level info` to log every HTTP request;
4xx/5xx responses are logged even at the default `warning` level.

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

The text interface renders a playable scene with numbered actions as well as movement, messages, offers, counteroffers, acceptance/rejection, waiting, and public world telemetry. HTTP exposes the same Platform operations for another client.

The browser surfaces are separate from the TUI. `sim2 play` opens `/` as a
game-oriented client: a world hub that can create or continue an instance, then
a scene with a world-specific procedural backdrop, character sheet, action
cards, goals, a compact loadout summary, a persistent route mini-map, world
pulse, nearby people and trade. Clicking a person opens a
character sheet with the dialogue thread and composer; “角色详情” opens the full
inventory and capability sheet. Existing players resume with one click, and
manual player actions
advance the same shared world tick as NPC decisions, interactions and
Story/God evolution. The HUD and world hall open the **World Studio** editor in
a full-screen overlay with the current operator session, so designing or
creating a new world no longer requires switching to a separate console or
copying a token. With no `~/.sim2/llm.json`, the page still advances time
and long actions; LLM-driven NPC turns wait until a provider is configured.
`/admin.html` keeps the full operator and world-authoring workbench: runtime
budgets, instance switching, snapshots, CLI-equivalent commands, package
compilation and smoke tests. Its 设计世界 page uses the same visual language as
the game and a beginner-first flow: copy a validated template, edit named
fields, watch the readiness checks, then publish and open a new instance. See
[Web Client Design](docs/design/web-client.md),
[Context Management](docs/design/context-management.md) and
[World Authoring Web](docs/design/world-authoring-web.md). The development
server binds to localhost and must not be exposed publicly without additional
deployment authentication and CORS controls.

## Worlds and extension

The first validation world is [Starter Village](docs/worlds/starter-village.md), with its importable package in [`worlds/starter_village`](worlds/starter_village/). It is a small settlement around a failing pact, with food, water, health, patrol, exploration, trade, relationships, hidden locations, scheduled reflection, and God-led world change.

[`worlds/tidebound_commons`](worlds/tidebound_commons/) is the Chinese coastal
survival world used by the game client. It ships a survival loop (water, food,
reeds, driftwood, rations, rope and repair kits), a public repair/route goal
chain, four origins with distinct opening events and first tasks, a daily
public decision that trades water against repairs or rest, a visible storm
countdown that damages an unprepared tide gate and rewards a real repair, and
no pre-written story beats: Story and God evolve the commons from validated
events, periodic cadence and the configured stagnation signal. Storm outcomes
also close and reopen configured places and deplete resource nodes, so their
consequences are spatial and material rather than only numeric. Opening-only
situation anchors and onboarding hints expire after two world days; later
content comes from Story and God. `stagnation.growth: structural` counts new
places, verbs, factions and rules rather than new research parameters, and
`god_structural` carries an explicit contract for one new playable unit.
Creating a place or resource emits a typed follow-up event, so dynamic content
gets an activity instead of becoming a dead room. All of these mechanics live
in YAML.

[`worlds/riverbend_market`](worlds/riverbend_market/) is the five-shop gold
economy world. The player arrives with a small purse seven days before a
caravan returns, and must move coin between a smithy, tavern, clinic, inn and
market: gather raw goods, sell them into shop tills, buy tools, meals, beds,
treatment and spice, craft ingots, food and medicine, then assemble a fair
crate from the smithy's ingot, the clinic's tonic, the tavern's food and ale,
the market's spice, and a ready inn. Shops restock and pay wages, a relief fund
and market short-work prevent hard locks, and the deterministic seven-day clock
owns the fair acts. Story turns verified trades and shortages into grounded
beats, the Director chooses one structural growth cell per plan, and God adds
one playable economic unit without minting coin. The full loop, prices and
agent contracts are in
[the Riverbend design](docs/design/riverbend-market-loop.md).

[`worlds/windward_skyfall`](worlds/windward_skyfall/) is the sky-archipelago
survival sandbox. The player captains the windship *Windward* above a collapsing
chain of islands: explore, gather, craft tools, build camp modules, manage five
crew members and three factions, and decide whether to escape through the High
Ring, anchor the islands, or become the storm. A `sky` scene theme, explicit
gated travel, storm pressure, day/night exposure, five endings and a focused
vertical-slice test ship with the package. The LLM Director reads the save's recent events and two residents' current perspectives.
God proposes playable changes to places, social ties, behaviors or rules. Story creates
chapters from committed events and reachable follow-ups. Each save persists its own history. See
[the Windward design](docs/design/windward-skyfall.md).

[`worlds/gate_cities`](worlds/gate_cities/) is a Chinese city logistics
sandbox. Choose who gets the first freight slot, spend the delivered cargo on
a public project, then hand the route to a behavior-tree crew. Key residents
and world-level Agents use the configured text provider for decisions and
evolution. Manual play works offline. See [the Gate of Cities design](docs/design/gate-cities.md).

Choose **诸城之门 · 建造经营版** to play the construction sandbox
([`gate_cities_sandbox`](worlds/gate_cities_sandbox/)). Survey a site, build a
pair of terminals and sponsor a charter or a rival route. Commerce, housing,
jobs and public appointments react to the routes residents can actually use.
Start a paving workshop with a small cash stake and commission residents to
fill paid orders. Reinvest the output in a road residents can use. It opens
as a separate save from the freight prototype.

In automatic mode, Director selects a gap from the outcome of the current
save. God proposes world changes, key residents decide whether to use them,
and Story follows the results that Platform commits. Routine residents use
behavior trees. The player chronicle shows public milestones while the full
timeline is available to observers. The world package defines the rules and
can be edited and run locally. See [the economy design](docs/design/gate-cities.md#经济审查与下一阶段设计).

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
