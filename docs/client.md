# TUI, text and HTTP clients

The primary local client is the web game client:

```bash
sim2 play                      # open the browser world hub
sim2 play --no-browser         # print the URL on a headless machine
```

`sim2 play` starts the local HTTP service, opens `http://127.0.0.1:8080/`, and
creates the first world from the page when no instance exists. The rest of this
document covers the Textual TUI, the plain-text client and the HTTP projections
they share.

The Textual TUI is the terminal client:

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

The repository ships a dependency-free browser bundle for the local Runtime Web
Client and Authoring Workbench. The local HTTP service is still a development
server, not a finished public web server. The friendly entry point is:

```bash
sim2 play
```

It starts the same local server on `127.0.0.1:8080`, opens the browser, and
passes the operator capability in a URL fragment so the page can unlock local
management without copy-pasting a token. Use `sim2 play --no-browser` on a
headless machine, or start the server without opening a browser:

```bash
sim2 serve --host 127.0.0.1 --port 8080
# or select one explicitly
sim2 serve --instance-id my-world-001 --host 127.0.0.1 --port 8080
curl 'http://127.0.0.1:8080/status'
curl 'http://127.0.0.1:8080/timeline?limit=50&after=0'
```

Without `--db`, the server uses `current_instance.json` under `--runtime-root`
(default `~/.sim2`) and falls back to the most recently modified
`instances/*/world.sqlite3`. When no instance exists at all, it starts in
**launcher mode**: `/` renders the world catalog instead of failing, `/world`
and `/player/entry` answer `status: "no_world"`, and the page can create the
first instance from a bundled template. The first creation is allowed before an
operator session exists; every later instance creation or switch keeps the
normal operator-token boundary. Startup prints the selected database (or
`null`), a `launcher` flag, and a local Operator token.

Open `http://127.0.0.1:8080/` in a browser. The main client is a play-first
world hub: it lists saved instances, offers a one-click **继续** card for the
instance's existing player, and exposes **创建新世界** with the bundled world
templates. Creating a world from the page compiles and initializes it through
the same bootstrap code as the CLI, then activates it. `/worlds` returns the
same catalog for other clients.

**运营台 → 设计世界** is now a beginner-first authoring page with the same
visual language as the game: a five-item writing checklist, a guided form with
plain-language fields, a readiness panel, and a publish step. Authors can copy
an existing package as a starting point, edit world basics, locations, people,
objects and actions, then run compile checks, a disposable SQLite smoke test,
and **生成并打开游戏** to write the package and activate a new instance. The
full JSON remains available behind the form; the source package is never
edited as a side effect.

`sim2 serve` exposes one versioned boundary under `/api/v1`. `POST /api/v1/session`
returns a read-only observer token; entering a player returns a player token that
is required for that player's view, actions, manual advance and dialogue. Operator
commands (`runtime/*`, instance creation/switching, `operator/events`, `operator/audit`,
snapshots and authoring) require the startup `operator_token` printed by the server
or `SIM2_OPERATOR_TOKEN`. This is a local development boundary, not a replacement
for production login and deployment authentication.

When `~/.sim2/llm.json` is absent, `/api/v1/runtime` reports
`llm_configured: false`. The Platform clock, needs, long actions and authored
background BT actors still advance, so the page remains playable; only LLM-driven
NPC decisions, Story/God turns and automatic dialogue replies stay idle until a
provider is configured.

One server process owns at most one autonomous runtime thread. Start accepts a
provider name plus world-minute and LLM-call budgets. Pause finishes the current
complete minute, including already-started Agent decisions, and then stops the
worker. Continue preserves the same `run_id`, starting minute, target minute and
LLM budget. Stop leaves committed history intact and pauses the persisted world.
Manual advance is a background task: `POST /api/v1/player/{id}/wait` returns
`202` with a task id, and the task advances one world minute per chunk without
holding the control lock for the whole request. A second task for the same player
returns `409 task_running`; a running autonomous worker returns `409 runtime_running`.
Switching an instance closes the old runtime, opens the replacement SQLite database,
and scopes every player session to that one instance.

The **命令** tab covers every `sim2` CLI instruction without leaving the browser:
`init` (`POST /api/v1/instances/init`), `validate-world`/`describe-world`
(`POST /api/v1/authoring/validate|describe`), `world-schema`
(`GET /api/v1/authoring/schema`), `build-world --template`
(`POST /api/v1/authoring/build-world` from the current draft), `decide`
(`POST /api/v1/operator/decide`), `simulate` (`POST /api/v1/operator/simulate`,
rejected while the autonomous runner owns the clock), `snapshot`/`restore`
(`POST /api/v1/operator/snapshots`, `GET /api/v1/operator/snapshots`,
`POST /api/v1/operator/snapshots/{id}/restore`), full trade
(`GET|POST /api/v1/player/{id}/trades` and
`POST /api/v1/player/{id}/trades/{offer_id}/{counter,accept,reject}`), `inject`
(`POST /api/v1/operator/events`) and the read-only
`status`/`health`/`metrics`/`audit` probes. `mud`/`tui` are client modes: the
play view is the web `mud` and the runtime view is the web `tui`.
All management writes require the Operator session; all player writes require
the matching Player session. Every command route accepts an `Idempotency-Key`
header (or `idempotency_key` field): the same session token plus the same key
executes once and replays the same response, so browser retries never duplicate a
behavior or trade. The web client sends a fresh key per user action, retries
network failures once with the same key, and disables the clicked button while the
request is in flight.

The game client opens one SSE connection to `GET /api/v1/stream?compact=1` after
entering the world. Events carry `cursor`, `event_id`, `revision`, `type` and
`data`. The browser reconnects with
`Last-Event-ID` and the hub replays the missing window. `player.view.changed`
triggers a coalesced read-model refresh only when `revision` advances.
Worlds with `ui.chronicle_events` send those public events and updates to the
player's own active behavior. They sample other world changes at most once
per 15-second polling interval while the clock moves. Observer timelines keep
the simulation events. `task.progress` and `task.completed` drive the manual-advance UI, and
`runtime`, `feed.append`, `trade.changed`, `dialogue.append` and `chapter.changed`
update their panels directly. Three consecutive stream errors fall back to the
15-second polling loop. The stream is read-only and never triggers a model call.

网页默认先打开 **世界大厅**：已有角色时一键继续，否则选择出身进入；出身卡
同时显示个人目标和“第一件事”。进入后
页面显示角色目标、需求条、当前位置、常驻路线小地图、当前篇章和行动卡；
小地图节点与行动卡走同一个 `/api/v1/player/{id}/actions` 校验路径。篇章标题
和摘要位于世界目标上方，目标区在行动区上方，默认显示前三项，包含状态、进度、
可读的缺失条件和可执行按钮；多选目标会并列显示最多三个选项（例如每日公共
决策）。推荐行动卡会标注它推进的篇章目标。行动区默认突出一个推荐行动和最多
五个备选行动，完成后“上一拍”显示结果与资源/需求变化；移动端底部固定一个
推荐行动或“推进当前行动”按钮。物品和能力收进“角色详情”侧栏，点击附近人物会
从右侧滑出角色侧栏，查看该角色的对话记录并直接发送消息，而不是切到单独 tab。
每张行动卡通过 `/api/v1/player/{id}/actions` 提交到 Platform；行动结果回到玩家
事件流。行内还有交易抽屉：待处理报价列表、发起/接受/反报价/拒绝都走同一幂等
通道。

主游戏页的 HUD、设置抽屉和世界大厅都有 **世界编辑器** 入口：编辑器以全屏
overlay 内嵌运营台的“设计世界”页，直接用当前 Operator session 打开，不需要
再输入令牌。编辑器与主游戏共用深青/琥珀配色和衬线标题；左侧是写作清单和
推荐下一步，中间是中文表单，右侧是就绪检查。新手可以先复制一个已验证模板，
再改名字、地点和人物；“生成并打开游戏”会写出世界包并切换到新实例。
关闭编辑器时如果活动实例被切换，主页面会重载到新实例；否则只刷新世界入口
和当前角色。独立标签仍可通过 `/admin.html#view=authoring-view` 打开同一个
编辑器。

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
