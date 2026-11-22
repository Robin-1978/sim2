# sim2 网页客户端设计

> 状态：网页已拆成两个产品面：面向玩家的游戏客户端 `/`，以及保留完整
> 运维与设计能力的运营台 `/admin.html`。两者都只是 Platform 的投影与输入层，
> 不直接读 SQLite、YAML、prompt 或 LLM provider。

## 产品边界

| 产品面 | 路径 | 面向用户 | 写入 |
| --- | --- | --- | --- |
| 游戏客户端 | `/` | 玩家 | 进入/恢复角色、行动、对话、交易、手动推进世界 |
| 运营台 | `/admin.html` | 维护者、世界作者 | 运行时控制、实例切换、快照、命令、世界设计工作台 |

游戏客户端不承担设计工作台职责。需要 Schema、草稿编译、smoke、快照或
原始命令时，从游戏内“设置 → 开发工具”打开运营台。

Web/API 的版本化契约、统一 SSE 和可恢复长任务设计见
[web-api-v1-refactor.md](web-api-v1-refactor.md)。

## 总体结构

```text
Browser
  ├─ Game Client  ──┐
  └─ Admin Page   ──┤
                    ▼
              ControlHTTPServer
                    ▼
               ControlService
                    ▼
                 Platform
                    ▼
              world.sqlite3
```

浏览器只发送经过认证的请求并渲染服务端投影。所有状态变化仍由
`ControlService` 和 Platform 校验、事务化并写入事件日志。

## 游戏客户端

### 布局

- **HUD**：世界名、世界日/时刻/阶段、季节、运行时状态、连接状态、世界大厅、
  世界编辑器、环境音开关、刷新和设置。
- **左栏**：角色身份、地点、需求与体力条、当前目标、物品/能力摘要；“角色详情”
  打开侧栏查看完整随身物品与能力，不再让库存和能力常驻挤占舞台。角色卡下方是
  **公共设施卡**：潮门、集雨器、信号透镜、公共工具架等由世界包声明的设施，
  显示位置、状态和关联世界数值。
- **中栏**：地点场景（世界调色板 + 分层 SVG 场景插画 + 可选的危机倒计时徽章）、
  **公共防线速览**（潮门、风暴倒计时、公共信心、公共工具包）、附近角色、
  资源点与设施、常驻世界路线小地图、任务进度、上一拍结果、
  **世界目标（公开篇章）**、一个推荐行动、紧凑行动列表、条件不足行动及原因。
- **右栏**：世界脉搏摘要、世界动态、附近人物列表、交易报价、按“防线 / 储备 /
  进展”分组的公共世界数值。
- **篇章节拍**：目标区位于场景和路线小地图之间，排在行动区上方；面板头部
  显示自动演化的 Story 标题（标注“自动演化”）、季末结局（标注“季末结局”）
  或开局窗口内的只读态势锚点（标注“当前态势”），摘要和 `goal_hint`
  显示在目标卡上方。开局的 `situation_anchor_window_minutes`
  结束后只显示 Story 生成的内容；结局之后如果 Story 生成新章节，新章节会
  替换结局卡。
  默认只显示优先级最高的三个目标；
  其余通过“显示全部”展开。每张目标卡显示 ready/needs_support/blocked
  状态、进度条、可读的缺失条件，以及可执行按钮：单目标显示“去做：…”，
  多选目标（例如每日公共决策）并列显示最多三个选项。推荐行动卡会额外标注
  “推进：某个目标”，把即时行动和公开篇章连起来。
- **季末收束层**：配置结局首次出现时弹出半屏结局面板，显示章节标题、摘要、
  `goal_hint` 和 `story.endings[].stats` 声明的世界数值；关闭后记住该结局，
  不再重复弹出。章节卡仍然保留结局，作为后续阅读和兜底。
- **行动节拍**：默认只突出一个推荐行动和最多五个备选行动；其余通过“显示更多
  行动”展开。行动筛选包含移动、采集、制作、公共、交易、社交和恢复；交易动作
  使用“交换”按钮，公共加固/工具架动作归入“公共”。开始行动后，桌面端把任务
  进度滚入视野，避免进度条停在当前滚动位置上方。行动完成后，“上一拍”显示
  世界分钟、结果和资源/需求变化。
- **人物侧栏**：点击附近人物或人物列表中的角色，从右侧滑出角色卡、对话记录和
  消息输入框；对话不再占用右栏 tab。
- **地图弹窗**：按配置坐标渲染地点与路线；相邻地点可直接出发。
- **移动端**：底部行动栏固定显示推荐行动或当前长行动的“推进”按钮，并提供
  “更多”跳到完整行动列表；场景后依次是公共防线、横向滚动的公共设施卡、上手
  提示和角色卡，角色摘要不再沉到完整行动列表之后；公共防线速览改为四列紧凑
  布局，隐藏长提示文本，提示仍保留在卡片的 `title` 中。
- **设置抽屉**：实例切换、同步推进模型、自动运行控制、Operator 解锁、世界编辑器入口。

小地图直接复用 `/api/v1/player/{id}/view` 的 `map` 投影，不额外读取 SQLite；
每次行动完成或自动刷新后随角色视图一起更新。点击可前往节点会提交同样的 `behavior:travel`
意图，因此地图、行动卡和 TUI 共享同一条 Platform 校验路径。

入口页的程序化封面由世界 ID 的稳定 hash 选择调色板和山脊形状，不包含任何世界
专用图片或名称；同一个世界每次打开得到一致封面，不同世界彼此可区分。出身卡
显示该出身的个人目标与“第一件事”；后者来自 `player_template.origins[].opening`
的摘要，因此玩家在进入前就能看出四个出身的开场不同。

入口首屏先讲“赌注”，再讲操作：标题下的 `ui.tagline` 是世界自己的钩子；
`ui.crisis_metrics` 与 `/api/v1/world` 返回的实时 `world_properties` 组成关键
指标卡（danger / warning / 正常三档）；`/api/v1/world.cast` 列出这个世界的人，
`ui.cast_order` 决定先展示谁。进入游戏后，最紧急的危机指标、章节标题和倒计时
组成场景左上角的态势条；`crisisMood()` 同时把场景和入口封面切到 warning / danger
色调，并在封面上显示雨。没有配置这些字段的世界自动隐藏对应区域，不会留下空白。
移动端有玩家时先展示存档与继续入口，其余情况先展示世界钩子或创建说明。

```yaml
ui:
  tagline: 风暴季正在逼近。潮门、海图和人心，只能先救一样。
  cast_order: [mara, ivo, selene, taro]
  crisis_metrics:
    - {property: tide_gate_integrity, label: 潮门完整性, format: percent,
       danger_below: 0.2, warning_below: 0.8, hint: 目标 80%}
```

游戏场景复用同一套世界调色板生成分层 SVG 场景插画：天空、星点、日/月、水面、
码头、闸门、工坊、观测塔、沼泽和住屋剪影由通用场景主题组合而成，地点的 `tags`
决定使用哪一套。雨、雾、星光、炉火和信号光束由 CSS 动画驱动。这样不同世界有
稳定的视觉身份，客户端仍不包含任何世界专用图片或 ID 分支。环境音由 Web Audio
在玩家第一次开启时合成风声、浪声和低频持续音，不加载外部音频；按钮状态写入
本地存储，默认关闭，避免自动播放。

右栏“世界脉搏”优先取最近的 Story/God/world-change 事件作为摘要，下面的
动态流只保留叙事和结果类事件；`behavior.started`、能力更新、记忆记录、
`world.property_changed`、`resource.regenerated`、演化任务到期和注册类系统
事件不再铺满玩家信息流。

### 属性与需求

角色的 `attributes` 不是装饰。`rules.capacity_attribute`（潮汐公社为 `stamina`）
被 Platform 用作行动容量：任何按分钟消耗超过当前值的行动会被隐藏或拒绝。
因此精力恢复必须只在非执行状态发生：

```yaml
minute_effects:
- id: stamina_recovery
  scope: agent
  every_minutes: 1
  condition: action.none
  effect: {type: attribute_delta, attribute: stamina, delta: 0.2, maximum: 100}
```

行动成本为 0.02–0.20/分钟，所以在执行长行动时精力净下降；空闲或等待时恢复。
当精力低于 70 时，世界包的 `rest_at_hearth` 行为出现，用一小时恢复 30 点。
`belonging` 与 `purpose` 每 10/15 分钟下降 0.05，社交和公共行动再补充；需求条
显示一位小数，避免慢速变化被整数四舍五入掩盖。`stamina_alert_threshold` 让
客户端在低精力时切换琥珀/红色状态。

潮汐公社的“公共淡水”不是随身水袋，而是共享 `fresh_water` 资源通过
`rules.resource_property_projections` 投到右栏的只读存量。喝水消耗随身
`fresh_water`；在安全地点可以执行 `draw_water` 从公共储备领一份（10 分钟，
储备 -1），不必每次都走 95 分钟去沼泽；沼泽采集仍是补充储备的主要方式。
配给决策、抢修决策、风暴损失和集雨器都会改变这份储备，低于 8 时触发
`resource.safety_low`。这样“淡水储备”有可见的来源、用途和低水位后果，
而不是一个装饰数字。

事件分类和结果文案也由世界包声明，不是网页里的世界分支：

```yaml
ui:
  event_kinds:
    weather: [storm., weather., season.]
    repair: [tide_gate., repair, signal., gate.]
  event_titles:
    weather: 风暴
    repair: 公共设施
  pulse_prefixes: [storm, story, gate., signal.]
  result_labels:
    marsh_rest_decided: 休养已安排
  reason_labels:
    station.available: 工坊当前不可用
```

`render_panels.eventKind`/`eventTitle` 读取 `ui.event_kinds`/`ui.event_titles`，
`renderWorldPulse` 读取 `ui.pulse_prefixes`；世界没有配置时使用
“天气 / 决策 / 对话 / 资源 / 交易 / 剧情 / 其他”的通用默认值。
`resultLabel` 从 `ui.result_labels` 读取行动结果文案，`humanizeReason` 从
`ui.reason_labels` 读取条件失败文案；属性名来自
`/player/view.player.attributes[].label`，行动成本的正负来自 `cost_mode`。
因此新增世界只需写配置，不需要改 `app.js` 或 `admin.js`。

视觉语义由通用字段驱动：地点 `tags` 决定场景色调，物品名来自
`rules.item_labels`，世界数值名来自 `rules.world_property_labels`，饮水/进食
警戒线来自 `rules.context_values.*_threshold`。世界数值标签可以声明
`unit: hours`、`alert: true`、`hidden: true`、`group` 和 `order`：`hours`
显示为小时，`alert` 把该数值作为危机徽章放进场景标题下方（例如风暴倒计时），
`hidden` 让内部计数（例如每日决策窗口）不出现在公共数值面板，`group`/`order`
把右栏公共账目分成“防线 / 储备 / 进展”并保持稳定顺序。代码不包含世界专用
ID 分支。

`setting.ui.crisis_metrics` 是可选的中栏防线速览配置：

```yaml
ui:
  crisis_metrics:
  - {property: tide_gate_integrity, label: 潮门完整性, format: percent, danger_below: 0.2, warning_below: 0.8, hint: 目标 80% · 支撑 40%}
  - {property: gate_supports, label: 临时支撑, format: percent, danger_below: 0.2, warning_below: 0.4, hint: 目标 40%}
  - {property: storm_eta_hours, label: 风暴倒计时, format: hours, danger_below: 3, warning_below: 8}
```

客户端按 `property` 读取 `world_properties`，按 `format` 复用百分比/小时/数量
格式，用 `danger_below` / `warning_below` 切换红/琥珀/青色状态；没有配置时
整块速览隐藏。这样危机指标仍由世界包声明，网页不硬编码潮门或风暴。

公共设施卡由世界包中的实体声明，客户端按 `order` 排序：

```yaml
# objects.yaml
- id: tide_gate
  type: machine
  facility:
    order: 1
    properties:
    - {key: tide_gate_integrity, label: 完整性}
    - {key: gate_supports, label: 临时支撑}
    - {key: gate_repairs, label: 已修复}

# locations.yaml resources
- id: commons_tool_rack
  type: resource
  facility:
    order: 4
    properties:
    - {key: commons_tool_kits, label: 库存}
```

`/player/view` 把声明了 `facility` 的实体投影为 `facilities`，只暴露名称、
描述、位置、公开状态、库存和声明的世界数值；没有 `facility` 声明的实体不会
进入这个投影。资源设施的状态由库存自动投影为 `stocked` / `empty`。右侧世界
数值和设施卡都支持 `unit: bool` 的 `true_label` / `false_label`。

结局收束层读取 `story.endings[].stats`：

```yaml
endings:
- id: outer_route_reopened
  kind: ending
  stats:
  - {property: tide_gate_integrity, label: 潮门完整性}
  - {property: public_confidence, label: 公共信心}
```

客户端按 `property` 从当前 `world_properties` 取值，用 `world_property_labels`
的 `unit` / `max` / `true_label` / `false_label` 格式化；未配置 `stats` 的结局
仍会显示标题、摘要和 `goal_hint`。

行动卡的成本和效果文本由 `agent_view.action_text` 统一生成。参数化库存消耗
会在投影时解析成具体物品和数量（例如 `Carried stock -1 repair kit on success`
或 `Use 1 repair kit (carried or shared stock)`），网页只负责本地化，不向玩家
显示 `item_quantity_parameter`、`resource_parameter` 等配置键。

静态页面（`index.html`、`app.js`、`admin.html`、`admin.js`）不写任何世界的名称、
类型或季节：HUD、入口卡和地图弹窗的世界名来自世界投影的 `title`（入口卡和
地图弹窗缺省显示“世界”，HUD 先退回世界 ID），入口卡的类型和季节来自 `genre`、
`season`（缺省显示“世界模拟”和“—”）。地点没有描述时显示中性的“这里没有更多
描述。”。

### 玩家循环

```text
进入/恢复角色 → 看场景与目标 → 选择行动卡或对话
             → Platform 校验并启动行为 → 查看进度与结果 → 选择下一步
```

- 新角色只需要角色名和出身；内部玩家 ID 由 Platform 生成（`player_<12 hex>`），
  不再要求玩家填写。已有角色再次进入时按 ID 恢复，并补齐当前
  `player_template` 中缺失的能力、物资、行为和 controller。
- 行动区先给出一个推荐行动，再以紧凑列表展示最多五个备选；其余按分类筛选或
  展开。所有行动仍只来自 Platform 判定可执行的行为，条件不足的行为保留在
  可展开列表并显示失败谓词的可读原因。
- 带目标或参数的行为（例如交谈）会弹出行动配置对话框。目标以稳定 ID 提交，
  避免重名角色产生歧义。
- 长时间行为显示剩余分钟和进度条，支持“推进到完成”和“等待 1 分钟”。
- 行动完成后，“上一拍”从 `recent_outcomes` 读取最近一次行动的公开结果、
  世界分钟、位置、物品、需求和体力变化；移动端底部行动栏同步切换为
  “推进当前行动”。

### 手动推进与世界同步

### 对话：消息先上屏，回复走 SSE

`POST /api/v1/player/{id}/dialogue` 只负责记录玩家消息并返回
`{"status":"accepted","reply":{"status":"pending"}}`。NPC 回复由服务端短线程在
后台运行 `resolve_pending_interactions`，不阻塞 HTTP 请求，也不推进世界时间。
SSE 通过两类事件通知客户端：

- `dialogue`：`dialogue.completed` 产生的新交换记录；玩家自己的消息先到，
  客户端保留“正在输入…”；只有 `speaker_id` 不是玩家时才清除 typing。
- `dialogue.append`：后台回复任务完成或失败；带 `player_id`，按玩家裁剪。
  失败时客户端清除 typing 并提示“NPC 回复暂不可用”。

客户端在发送前显示 typing，POST 返回后立即渲染玩家消息；30 秒没有回复会
自动清除 typing，避免界面像卡死。对话仍不推进世界分钟。

玩家角色使用 `controller: manual`，自动运行不会替玩家角色做 LLM 决策。
提交行动后，客户端调用 `POST /api/v1/player/{id}/wait`；服务端返回 `202` 和
`task_id`，由 `ManualTaskRunner` 在后台以一分钟为一块推进，并使用与自动运行
相同的 `SimulationRunner`。每个 tick 同时推进：

- 世界分钟与分钟效果、资源再生；
- NPC 的 Agent 决策、当前行为与行动；
- 对话、交易等即时交互；
- Story/God 的自动演化任务。

手动推进不是玩家私有时钟。任务接受可选 `provider`，游戏客户端把设置中选择
的模型传给决策、反思、Story 和 God 角色；默认优先选择可用的 `remote`，也可以
在设置里改回服务端默认。客户端通过 `task.progress` / `task.completed` 或
`GET /api/v1/player/{id}/tasks/{task_id}` 更新世界时间和任务进度，结束后明确
显示成功或模型失败原因。任务运行期间任何单个请求都不会长期持有控制锁；同一
玩家同时只允许一个任务。玩家空闲时等待任务会推进完整的请求分钟数；
如果等待是在长行动期间触发的，则在行动结束时停止。世界状态面板提供
“等待 1 小时 / 4 小时”两个显式入口，旁边显示推进进度，避免手动模式只能
一分钟一分钟点。

`GET /api/v1/observer/timeline` 的每个事件现在带一个可选 `activity` 投影：
`behavior.started/completed/failed` 会给出角色名、行为标签和公开理由，
`world.change.*` 给出 God 规则的标签和说明，`story.beat.activated` 给出
章节标题和摘要。右栏“人物”tab 用它渲染“居民动向”，场景人物 chip 和角色
卡显示 `active_behavior_label` 与进度；世界脉搏没有更大的 Story/God 事件时
退回最近一条居民行动。这样玩家能看到 NPC 为什么做、正在做什么，而不是只看到
“正在忙碌”。

### 服务端投影

`build_player_view` 在决策投影之上补充客户端需要的展示事实：

| 字段 | 用途 |
| --- | --- |
| `location` | 当前地点的名称、描述、标签、安全性 |
| `item_labels` | `rules.item_labels` 的物品显示名 |
| `capabilities` | 角色能力值与 `capabilities.yaml` 中的名称 |
| `goals` | 公开目标及 ready/needs_support/blocked 状态；支持 `origins` 与 `visible_when` |
| `chapter` | 优先投影玩家可读的 `story.beat.activated`；没有时退回 `story.situation_anchors` 的只读态势旁白 |
| `alert_thresholds` | 需求条警戒线 |
| `world_properties` / `world_property_labels` | 右侧公共账目 |
| `facilities` | 世界包声明为 `facility` 的公共设施卡 |
| `revision` / `last_event_id` | 客户端只在 revision 前进时重拉整个视图 |
| `unavailable_actions` | 不可执行行为及失败原因 |

`GET /api/v1/world` 提供进入界面需要的事实：世界标题、premise、题材、locale、
日历、起始分钟、当前实例，以及入口钩子用的 `world_properties`、
`world_property_labels` 和 `cast`（当前世界的非玩家角色）；没有活动实例时
返回 `status: "no_world"`。
`GET /api/v1/player/entry` 返回配置的出身，以及当前实例已有的玩家角色
（`players`、`can_create_player`），页面据此直接显示“继续游戏”，而不是要求
玩家记住 ID。`GET /api/v1/worlds` 返回可创建的内置世界与受
`--authoring-root` 约束的自建包摘要；内置包带 `kind` 与 `order`，创建页先列
`world`（按 `order`），`probe` / `test` 包排在后面并标注“测试”。

## 启动与首次游玩

`sim2 play` 是面向玩家的入口：启动本地服务并打开浏览器，URL fragment 中携带
一次性 owner 链接；页面把 fragment 换成 Operator session 后立即用
`history.replaceState` 清除，因此 fragment 不会进入服务器日志。

- 没有 `--db` 时优先使用 `current_instance.json` 或最近的实例。
- 一个实例都不存在时，服务器进入 launcher 状态，`/` 仍然可用，页面直接显示
  世界模板；首次 `POST /instances/init` 可无 Operator session 执行，创建后
  自动激活。此后创建或切换实例继续要求 Operator session。
- 没有 `~/.sim2/llm.json` 时，`/api/v1/runtime` 返回
  `llm_configured: false`；时钟、需求、长行动和 `bt` 控制器角色仍推进，
  LLM 驱动的 NPC 决策与 Story/God 暂停等待配置。

## 自动演化

世界不需要预写完整剧情。游戏客户端展示的章节来自 Story Agent 对已验证事件
的提案，世界结构来自 God Agent 对累积证据的提案。当前 `tidebound_commons`
的配置策略是：

- `story.yaml` 不包含预写 `beats`；只声明哪些事件可以作为 emergent 来源。
- Story 在检查、研究、修复、校准、风暴、收获和对话等已验证事件后唤醒，
  用 `count`、`every`、`distinct_by` 抑制重复讲述同一次收成。
- God 在公共工程、资源压力和 `world.stagnation` 时唤醒，也可以按
  `cadence.god` / `cadence.god_structural` 周期检查。
- `stagnation` 按窗口比较所有角色累计的可执行选项；如果世界长期没有出现新
  选项，就发布 `world.stagnation`，重新唤醒 God，而不是让角色重复封闭循环。
- 演化任务使用的模型与手动推进选择的 provider 相同，避免 Story/God 落到不可用
  的默认模型。

运营台的“演化队列”和“决策动态”仍用于审查事实、提案与结果，但普通玩家只需
在“动态”中看到已经提交的章节和世界变化。

主游戏页通过 HUD、设置抽屉和世界大厅打开 **世界编辑器**：全屏 overlay 内嵌
`/admin.html#view=authoring-view`，并用 fragment 把当前 Operator session 传给
运营台。运营台读取后立即从 URL 移除 token，只保留 `#view=...`，因此编辑中的
页面刷新仍停在设计视图。关闭编辑器时主页面比较 `active_db`；实例被切换就重载，
否则只刷新入口和角色视图。独立标签用同一 fragment 机制，也可从运营台顶栏
“返回游戏”回到主页面。

## 运营台

`/admin.html` 保留原有能力：

- 世界入口与角色预览；
- 自动运行开始/暂停/继续/停止/单步；
- 运行历史、LLM 成本、Provider 选择；
- 实例创建与热切换、快照创建/恢复/下载；
- 全量 CLI 等价命令；
- Schema 驱动的世界设计、编译、smoke、导出和 AI 草稿。

运营台使用独立静态资源 `admin.html` / `admin.css`；入口 `admin.js` 只加载
`js/admin_app.js`，其余代码拆在 `admin_api.js`、`admin_core.js`、
`admin_render.js`、`admin_editor.js` 和 `admin_actions.js`。它不会与游戏客户端
争用 DOM 或状态。管理写操作仍需 Operator Token 解锁。

## 前端模块

游戏与运营台都使用浏览器原生 ES modules，不引入 bundler、npm 或 TypeScript：

```text
src/sim2/web/
  app.js                 仅 import ./js/app.js
  js/app.js              boot、wireUi、renderAll
  js/core.js             共享 state、DOM/格式化/存储/提示
  js/api.js              fetch、错误信封、幂等键、owner link
  js/audio.js            Web Audio 环境音
  js/art.js              世界调色板与分层 SVG 场景
  js/render_*.js         入口、角色、场景、世界、行动、侧栏渲染
  js/actions.js          进入、视图、任务、对话、交易、地图、设置
  admin.js               仅 import ./js/admin_app.js
  js/admin_*.js          运营台 transport/core/render/editor/actions
```

`index.html` 和 `admin.html` 以 `type="module"` 加载入口。`web_asset` 只允许
web 根目录内的 `.html`、`.css`、`.js` 和 `.svg`，拒绝路径穿越。修改任一模块后
仍要提升 `index.html` / `admin.html` 中的 `?v=` 和页脚构建标记。

## HTTP 面

游戏客户端使用的主要端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/world` | 世界展示事实与起始时钟；无实例时为 `no_world` |
| GET | `/api/v1/worlds` | 可创建的世界模板摘要 |
| GET | `/api/v1/player/entry` | 玩家出身、入口地点与已有玩家 |
| POST | `/api/v1/player/enter` | 新建（名称+出身，ID 自动生成）或恢复玩家，返回 Player session |
| GET | `/api/v1/player/{id}/view` | 玩家投影、行动、目标、物品标签、世界数值 |
| POST | `/api/v1/player/{id}/actions` | 提交 Platform 校验的行为 |
| POST | `/api/v1/player/{id}/wait` | 返回 202 + task_id；后台按分钟推进 |
| GET | `/api/v1/player/{id}/tasks/{task_id}` | 任务进度与最终结果 |
| GET | `/api/v1/player/{id}/trades` | 玩家交易报价 |
| POST | `/api/v1/player/{id}/trades[/{offer_id}/{accept,counter,reject}]` | 发起或回应交易 |
| POST | `/api/v1/player/{id}/dialogue` | 记录消息并立即返回 pending；回复走 SSE |
| GET | `/api/v1/map` | 玩家可见地点、路线与占用者 |
| GET | `/api/v1/observer/timeline` | 公开事件流 |
| GET | `/api/v1/observer/dialogue` | 公开对话记录 |
| GET | `/api/v1/stream` | 统一 SSE；按 session 角色裁剪事件 |
| GET | `/api/v1/runtime` | 运行时状态、可用 Provider 与 `llm_configured` |
| POST | `/api/v1/runtime/*` | 需要 Operator session |
| GET | `/api/v1/operator/metrics` | 运营指标，需要 Operator session |
| GET | `/api/v1/operator/audit` | 审计导出，需要 Operator session |
| POST | `/api/v1/instances/init` | 创建实例；launcher 状态下首次创建免 Operator |
| POST | `/api/v1/instances/activate` | 需要 Operator session |
| GET | `/api/v1/meta/contracts` | 请求/响应 JSON Schema |

`/admin.html` 额外使用 `/api/v1/authoring/*`、`/api/v1/operator/snapshots*`、
`/api/v1/operator/audit` 和 `/api/v1/operator/events` 等运营端点。旧的无版本
路径已删除，不提供别名。

## 缓存与构建标记

`index.html` 的 `?v=` 用于资源缓存失效，页脚 `web-game-vN` 用于确认当前标签页
是否加载了最新游戏客户端；运营台页脚使用 `admin-vN`。修改 `app.js`、
`app.css`、`admin.js` 或 `admin.css` 后必须同步更新对应版本号。

## 测试

- `tests/test_web_client.py`：游戏页面与管理页面资源、玩家恢复、世界投影、
  对话/交易、手动同步推进、会话头、Operator 解锁、零实例 launcher 首次建世界
  与“继续角色”入口。
- `tests/test_play_without_llm.py`：没有 `llm.json` 时手动推进仍可运行。
- `tests/test_web_assets_world_neutral.py`：静态页面不含任何已交付世界的名称、
  类型或季节，地图弹窗标题取自世界标题。
- `tests/test_player_entry_templates.py`：玩家模板、恢复补齐、文本客户端和
  行动目标。
- `tests/test_tidebound_commons.py`：世界配置、资源/配方、公开目标、自动
  Story wake、God cadence 与 stagnation 配置。

新增网页能力应先在 `ControlService` 或 `build_player_view` 中形成传输无关的
投影，再扩展 HTTP 与前端；不要在浏览器里复制 Platform 规则。
