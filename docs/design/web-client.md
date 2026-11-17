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

- **HUD**：世界名、世界日/时刻/阶段、季节、运行时状态、连接状态、刷新和设置。
- **左栏**：角色头像/身份/地点、需求与体力条、当前目标、随身物品、能力。
- **中栏**：地点场景、附近角色、资源点与设施、任务进度、平台校验过的行动卡、
  条件不足行动及原因、公社公开目标。
- **右栏**：世界动态、玩家对话、交易报价、公共世界数值。
- **地图弹窗**：按配置坐标渲染地点与路线；相邻地点可直接出发。
- **设置抽屉**：实例切换、同步推进模型、自动运行控制、Operator 解锁、运营台入口。

视觉语义由通用字段驱动：地点 `tags` 决定场景色调，物品名来自
`rules.item_labels`，世界数值名来自 `rules.world_property_labels`，饮水/进食
警戒线来自 `rules.context_values.*_threshold`。代码不包含世界专用 ID 分支。

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

- 已有玩家 ID 再次提交会恢复角色，并补齐当前 `player_template` 中缺失的
  能力、物资、行为和 controller。
- 行动卡只显示 Platform 判定可执行的行为；其余行为进入“条件不足”列表，
  并显示失败谓词的可读原因。
- 带目标或参数的行为（例如交谈）会弹出行动配置对话框。目标以稳定 ID 提交，
  避免重名角色产生歧义。
- 长时间行为显示剩余分钟和进度条，支持“推进到完成”和“等待 1 分钟”。

### 手动推进与世界同步

玩家角色使用 `controller: manual`，自动运行不会替玩家角色做 LLM 决策。
提交行动后，客户端按剩余分钟分块调用 `/player/wait`；服务端使用与自动运行
相同的 `SimulationRunner`，所以每个 tick 同时推进：

- 世界分钟与分钟效果、资源再生；
- NPC 的 Agent 决策、当前行为与行动；
- 对话、交易等即时交互；
- Story/God 的自动演化任务。

手动推进不是玩家私有时钟。`/player/wait` 接受可选 `provider`，游戏客户端
把设置中选择的模型传给决策、反思、Story 和 God 角色；默认优先选择可用的
`remote`，也可以在设置里改回服务端默认。客户端每块请求后更新世界时间、
任务进度和 NPC 决策/错误计数，结束后明确显示成功或模型失败原因。

### 服务端投影

`build_player_view` 在决策投影之上补充客户端需要的展示事实：

| 字段 | 用途 |
| --- | --- |
| `location` | 当前地点的名称、描述、标签、安全性 |
| `item_labels` | `rules.item_labels` 的物品显示名 |
| `capabilities` | 角色能力值与 `capabilities.yaml` 中的名称 |
| `goals` | 公开目标及 ready/needs_support/blocked 状态 |
| `alert_thresholds` | 需求条警戒线 |
| `world_properties` / `world_property_labels` | 右侧公共账目 |
| `unavailable_actions` | 不可执行行为及失败原因 |

`GET /world` 提供进入界面需要的事实：世界标题、premise、题材、locale、
日历、起始分钟和当前实例。`GET /player/entry` 返回配置的出身，包括名称、
描述、目标和初始配置。

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

## 运营台

`/admin.html` 保留原有能力：

- 世界入口与角色预览；
- 自动运行开始/暂停/继续/停止/单步；
- 运行历史、LLM 成本、Provider 选择；
- 实例创建与热切换、快照创建/恢复/下载；
- 全量 CLI 等价命令；
- Schema 驱动的世界设计、编译、smoke、导出和 AI 草稿。

运营台使用独立静态资源 `admin.html` / `admin.js` / `admin.css`，不会与游戏
客户端争用 DOM 或状态。管理写操作仍需 Operator Token 解锁。

## HTTP 面

游戏客户端使用的主要端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/world` | 世界展示事实与起始时钟 |
| GET | `/player/entry` | 玩家出身与入口地点 |
| POST | `/players` | 新建或恢复玩家，返回 Player session |
| GET | `/player/view` | 玩家投影、行动、目标、物品标签、世界数值 |
| POST | `/player/act` | 提交 Platform 校验的行为 |
| POST | `/player/wait` | 以共享世界 tick 推进，可指定 provider |
| GET | `/map` | 玩家可见地点、路线与占用者 |
| GET | `/timeline` | 公开事件流 |
| GET | `/observer/dialogue` | 公开对话记录 |
| POST | `/dialogue` / `/trade/*` | 即时对话与交易 |
| GET | `/runtime` | 运行时状态和可用 Provider |
| POST | `/runtime/*` | 需要 Operator session |
| POST | `/instances/activate` | 需要 Operator session |

`/admin.html` 额外使用 `/authoring/*`、`/snapshots*`、`/audit`、`/decisions`
等运营端点。

## 缓存与构建标记

`index.html` 的 `?v=` 用于资源缓存失效，页脚 `web-game-vN` 用于确认当前标签页
是否加载了最新游戏客户端；运营台页脚使用 `admin-vN`。修改 `app.js`、
`app.css`、`admin.js` 或 `admin.css` 后必须同步更新对应版本号。

## 测试

- `tests/test_web_client.py`：游戏页面与管理页面资源、玩家恢复、世界投影、
  对话/交易、手动同步推进、会话头、Operator 解锁。
- `tests/test_web_assets_world_neutral.py`：静态页面不含任何已交付世界的名称、
  类型或季节，地图弹窗标题取自世界标题。
- `tests/test_player_entry_templates.py`：玩家模板、恢复补齐、文本客户端和
  行动目标。
- `tests/test_tidebound_commons.py`：世界配置、资源/配方、公开目标、自动
  Story wake、God cadence 与 stagnation 配置。

新增网页能力应先在 `ControlService` 或 `build_player_view` 中形成传输无关的
投影，再扩展 HTTP 与前端；不要在浏览器里复制 Platform 规则。
