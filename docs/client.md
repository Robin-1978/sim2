# TUI, text and HTTP clients

The primary local client is the Textual TUI:

```bash
# Observe an already running world.
sim2 tui --db ~/.sim2/instances/mist-village-001/world.sqlite3 --observer

# Observe and start the autonomous Agent loop in the same command.
sim2 tui --db ~/.sim2/instances/mist-village-001/world.sqlite3 \
  --observer --run --provider remote_beam

# Enter as a configured player. The origin is needed only on first entry.
sim2 tui --db "$DB" --player-id player_one --origin courier --name Ari --run \
  --provider remote_beam
```

The screen has four live areas:

- **World map**: visible locations, topology, safety and current occupants.
- **Story timeline**: public events in world-time order.
- **Characters**: location, execution state, active BT and minutes remaining.
- **Details and command bar**: public attributes plus player or observer commands.

The status bar uses the instance's configured calendar, starting clock, day
phase and season. Character rows show public location and behavior names; raw
SQLite identifiers remain available through the audit API rather than being
used as the primary interface text.

Keys: `p` pauses or resumes, `s` advances exactly one minute while paused,
`r` refreshes and `q` exits. Buttons expose the same controls. The command bar
accepts `map`, `look`, `move`, `say`, trade commands and the text-client command
set. The TUI runtime worker uses its own SQLite connection; the display never
shares an LLM request transaction or mutates state directly.

## Map data

`Platform.map_view` and `GET /map` return transport-neutral data:

```json
{
  "nodes": [
    {"id": "reactor_bay", "name": "Reactor Bay", "routes": ["docking_ring"], "occupants": []}
  ],
  "edges": [{"from": "docking_ring", "to": "reactor_bay"}]
}
```

The terminal renders a spanning-tree topology and lists additional cross
routes, so cyclic maps remain accurate in narrow windows. A world may add
`map_position: {x: ..., y: ...}` to a location for a future canvas or Web
renderer. Players see public and personally known locations; hidden locations
are not exposed. The observer view also excludes hidden world truth.

The MUD-style `sim2 mud` interface remains useful for scripts and recovery.
`sim2 serve` exposes the same projections and controls over HTTP for a later
Web or desktop client. Both go through Platform and share the same visibility,
validation and transaction rules as the TUI.

The runtime observer UI and the World Authoring Web 工作台 are separate
surfaces. The observer UI reads an existing SQLite instance; the authoring
workbench edits a draft package and calls compile/smoke validation before a
reviewed package is initialized. The authoring design and planned endpoints
are documented in [`docs/design/world-authoring-web.md`](design/world-authoring-web.md).

The Runtime Web Client design is documented in
[`docs/design/web-client.md`](design/web-client.md). It uses the existing
visibility-safe map, observer and timeline projections, then adds incremental
event cursors and typed interaction responses. The two web surfaces share the
transport layer but never share direct SQLite or configuration editing access.
