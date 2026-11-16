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
  A scrollable command log keeps multi-line `look` and `actions` output visible.

The status bar uses the instance's configured calendar, starting clock, day
phase and season. Character rows show public location and behavior names; raw
SQLite identifiers remain available through the audit API rather than being
used as the primary interface text.

Keys: `p` pauses or resumes, `s` advances exactly one minute while paused,
`r` refreshes and `q` exits. Buttons expose the same controls. The command bar
accepts `map`, `look`, `actions`, `do 1`, `go <place>`, `say`, trade commands and
the text-client command set. The TUI runtime worker uses its own SQLite
connection; the display never shares an LLM request transaction or mutates
state directly.

## Text client

`sim2 mud` is the plain-text counterpart. It renders the same `player_view`
projection used by the web client, so a player sees the world clock, current
location and safety tags, goal, need meters, inventory, known routes, numbered
actions, nearby entities and recent outcomes.

```text
== World ==
World minute 0 · Day 1, 07:00, day, late_autumn · world=starter_village

== You ==
Ari · player · idle · 河桥 (unsafe)
Goal: 找到失踪车队的下落
Needs: Satiety [########--] 80 | Safety [#######---] 70 | ...
Routes: 村广场 (5 min, safe)

== Actions ==
 1. 前往 村广场 · 预计 5 分钟 · 沿已知路线前往 村广场。  [do 1]
    -> 抵达目标地点
```

The common commands are:

- `look` or `status` shows the scene or a compact status.
- `actions` lists the current numbered actions. `do 1` submits the selected
  action through Platform; optional `key=value` arguments override that
  action's parameters.
- `go 1`, `go village_square` or `go 村广场` starts travel to a connected known
  location.
- `wait [minutes]` advances a manual session one or more minutes.
- `say <target> <message>` records a message to a nearby character and asks the
  recipient's Agent to answer immediately. The target may be a name or ID and
  the topic defaults to `general`.
- `reply` retries any remaining pending interaction with the configured Agent
  provider without advancing world time. `messages [limit]` and the
  `Conversation` section of `look` show the resulting transcript.
- `offer`, `offers`, `accept`, `counter` and `reject` cover trade.
- `map`, `timeline [limit]` and `help [command]` inspect the known world.
  `quit` leaves the session.

A manual `sim2 mud` session asks the recipient to answer as part of `say`. It
uses the provider selected by `--provider`, or the configured default
`agent_decision` provider when the flag is omitted:

```bash
sim2 mud --db "$DB" --player-id player_one --provider remote_beam
```

An autonomous TUI runtime handles these interaction wakes in the background.
Observer mode adds `world`, `events [limit]`, `pause`, `resume`, `step`,
`evolution` and `metrics`. The output remains plain text and the client only
submits intents through Platform; it never writes SQLite or exposes hidden
locations, private memory or God-only context.

## Map data

`Platform.map_view` and `GET /map` return transport-neutral data. Route edges
include configured distance and the baseline minutes conversion:

```json
{
  "nodes": [
    {"id": "reactor_bay", "name": "Reactor Bay", "routes": ["docking_ring"], "occupants": []}
  ],
  "edges": [{"from": "docking_ring", "to": "reactor_bay", "distance": 2, "minutes": 10}]
}
```

The terminal renders a spanning-tree topology and lists additional cross
routes, so cyclic maps remain accurate in narrow windows. A world may add
`map_position: {x: ..., y: ...}` to a location for a future canvas or Web
renderer. Players see public and personally known locations; hidden locations
are not exposed. The observer view also excludes hidden world truth.

The MUD-style `sim2 mud` client now renders a playable text scene with the same
`player_view` projection used by the web client. `sim2 serve` exposes the same
projections and controls to the web client. Both go through Platform and share
the same visibility, validation and transaction rules as the TUI.

The repository now ships a dependency-free browser bundle for the local Runtime
Web Client and Authoring Workbench. The local HTTP service is still a development
server, not a finished public web server:

```bash
sim2 serve --host 127.0.0.1 --port 8080
# or select one explicitly
sim2 serve --instance-id my-world-001 --host 127.0.0.1 --port 8080
curl 'http://127.0.0.1:8080/status'
curl 'http://127.0.0.1:8080/timeline?limit=50&after=0'
```

Without `--db`, the server uses `current_instance.json` under `--runtime-root`
(default `~/.sim2`) and falls back to the most recently modified
`instances/*/world.sqlite3`. Startup prints the selected database and a local
Operator token.

Open `http://127.0.0.1:8080/` in a browser. **运行世界** shows the map, public
timeline, characters and evolution queue. **设计世界** edits a JSON world draft,
loads the canonical Schema, runs compile checks and a disposable SQLite smoke test,
opens the current source package, lists packages under `--authoring-root`, can
create a new independent SQLite game from a selected reviewed template, can
invoke the configured WorldBuilder Agent on an explicit button click, and
downloads the generated YAML files as a package manifest. **Create and open**
first creates the SQLite instance and then activates it through the same server;
the source package is never edited as a side effect.

`sim2 serve` now creates a local in-memory session boundary. `GET /session`
returns a read-only observer token; entering a player returns a player token that
is required for that player's view, actions, waiting and dialogue. Operator
commands (`runtime/start`, `runtime/pause`, `runtime/resume`, `runtime/stop`,
`runtime/step`, instance creation/switching, `events`, `audit`) require the startup
`operator_token` printed by the server or `SIM2_OPERATOR_TOKEN`. This is a local
development boundary, not a replacement for production login and deployment
authentication.

One server process owns at most one autonomous runtime thread. Start accepts a
provider name plus world-minute and LLM-call budgets. Pause finishes the current
complete minute, including already-started Agent decisions, and then stops the
worker. Continue preserves the same `run_id`, starting minute, target minute and
LLM budget. Stop leaves committed history intact and pauses the persisted world.
Manual wait returns `runtime_running` while the autonomous worker owns the clock.
Switching an instance first stops and closes the old runtime, opens the replacement
SQLite database, and scopes every player session to that one instance.

The **命令** tab covers every `sim2` CLI instruction without leaving the browser:
`init` (`POST /instances/init`), `validate-world`/`describe-world`
(`POST /authoring/validate|describe`), `world-schema` (`GET /authoring/schema`),
`build-world --template` (`POST /authoring/build-world` from the current draft),
`decide` (`POST /decide`), `simulate` (`POST /simulate`, rejected while the
autonomous runner owns the clock), `snapshot`/`restore` (`POST /snapshots`,
`GET /snapshots`, `POST /restore`), full trade (`GET /trade/offers`,
`POST /trade/offers|counter|accept|reject`), `inject` (`POST /events`) and the
read-only `status`/`health`/`metrics`/`audit` probes. `mud`/`tui` are client
modes: the play view is the web `mud` and the runtime view is the web `tui`.
All management writes require the Operator session; all player writes require
the matching Player session. Every `POST` (except `/session`) accepts an
`Idempotency-Key` header (or `idempotency_key` field): the same session token
plus the same key executes once and replays the same response, so browser
retries never duplicate a behavior or trade. The web client sends a fresh key
per user action, retries network failures once with the same key, and disables
the clicked button while the request is in flight.

The observer keeps a 4-second polling baseline plus an SSE live layer
(`GET /events/stream`): timeline deltas and runtime changes arrive within a
second and merge by `event_id`; three consecutive stream errors fall back to
polling. The stream is read-only and never triggers a model call.

网页默认先打开 **开始游戏**：选择玩家出身后，页面显示角色目标、需求条、
当前位置、可见路线和行动卡。每张行动卡通过 `/player/act` 提交到 Platform；
行动结果回到玩家事件流。行内还有交易抽屉：待处理报价列表、发起/接受/
反报价/拒绝都走同一幂等通道。**观察世界**多了 NPC 对话与决策动态两个
面板（公开交谈全文、谁决定做什么及原因/结果），决策行可展开因果链
（`GET /events/related` 只含公开字段，不含 prompt 与私有记忆）；运行
控制台有分段历史与 token/预算预警，命令页快照列表可直接下载 SQLite。
**设计世界** 仍用于配置编辑。

To open other reviewed packages, constrain the server to their parent directory:

```bash
sim2 serve --db "$DB" --authoring-root /home/robin/ws/sim2/worlds --port 8080
```

The editor defaults to a guided form view with per-section entries; **原始 JSON**
is an advanced view for nested Action, BT and rule structures. This keeps the
authoring value in Schema-driven validation, executable smoke checks and package
generation rather than making the browser a second YAML parser.

The browser design, projection boundaries, cursor/reconnect rules, map behavior,
player/operator permissions, and the separate World Authoring Workbench are
specified in [`docs/design/web-client.md`](design/web-client.md). The workbench
uses the same Schema and Definition Compiler as `world-schema` and
`validate-world`; it does not edit a running SQLite instance. Do not expose the
development HTTP service outside localhost without a reviewed reverse-proxy
authentication layer and CORS policy.

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
