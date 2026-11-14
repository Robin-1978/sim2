# sim2 Web Client 设计

## 定位

网页客户端和 World Authoring Web 是两个不同的产品界面，共用同一个
HTTP/JSON 传输层，但权限和数据目标不同：

| 界面 | 读取 | 可以提交 | 作用 |
|---|---|---|---|
| Runtime Web Client | 当前世界的可见投影、地图、时间线、角色状态 | 玩家对话、交易、控制命令（按权限） | 观察和参与一个已初始化的世界 |
| Authoring Workbench | 草稿、Schema、编译诊断、临时 smoke world | 草稿编译、导出世界包 | 创建和审查初始配置 |

两者都不能直接打开 SQLite。所有状态变化都经过 `ControlService` 和
Platform；客户端只处理投影、输入和错误显示。

## Runtime Web Client 的 MVP

首屏使用一个三栏布局，窄屏时改为可切换的标签页：

1. **World header**：世界名称、世界时间、日期/季节、运行/暂停状态、
   健康状态和最近一次提交结果。
2. **Map**：使用 `GET /map` 的 `nodes` 和 `edges` 绘制 SVG/canvas 拓扑。
   节点坐标优先使用配置的 `map_position`，没有坐标时使用确定性的力导向
   或网格布局。位置和标签来自投影，前端不认识“村庄”“北门”等固定名称。
3. **Timeline**：使用 `GET /timeline?after=<event_id>` 增量读取已公开事件；
   每行显示世界分钟、可见人物、地点、结果、因果来源和 Story/God 后续任务
   状态。默认隐藏 `world_minute.advanced`，用户可以打开完整 tick 视图。
4. **Characters**：显示可见人物、地点、当前行为、剩余时间、公开需求和
   当前剧情目标。点击人物打开其允许的决策视图，不显示 SQLite 内部 ID、
   私人知识或隐藏世界事实。
5. **Interaction panel**：玩家可以发送对话、提交交易、移动到已知地点，
   或查看 Platform 返回的明确拒绝原因。所有表单提交后以服务端结果覆盖
   乐观状态，不能本地猜测资源或关系变化。

Observer 模式是只读的；暂停、单步和注入事件需要本地管理员权限，普通
玩家界面不显示这些控件。

## 数据刷新和性能

初次加载只请求一次 `/status`、`/observer`、`/map` 和 `/timeline`。之后：

- 用 `after_event_id` 作为时间线游标，避免反复传输完整历史；
- 世界时间变化只刷新 header 和发生变化的投影；
- 角色详情按需加载，不在每个 tick 请求所有 Agent 视图；
- 首版采用 1–2 秒轮询，避免在 `ThreadingHTTPServer` 上引入另一个复杂
  写入通道；稳定后可增加只读 SSE `/stream`，事件仍由 SQLite 提交结果驱动；
- 地图和时间线组件使用事件 ID 去重，重连从最后游标继续；
- 所有请求带 `revision`/`event_id`，服务端返回冲突时重新加载当前投影。

网页不需要每分钟调用 LLM。LLM 仍由运行时调度器在行为完成、事件或配置
唤醒时调用，网页只显示已经提交的结果和待处理任务。

## HTTP 契约

当前已经存在并可直接被客户端使用的只读接口：

- `GET /status`
- `GET /health`
- `GET /metrics`
- `GET /observer?limit=...`
- `GET /timeline?limit=...&after=...&ticks=...`
- `GET /map?viewer_id=...&observer=...`
- `GET /audit`
- `GET /evolution/tasks`

当前已有的写接口：

- `POST /pause`、`POST /resume`、`POST /step`
- `POST /events`
- `POST /players`
- `POST /dialogue`
- `POST /trade/offers`、`POST /trade/accept`

后续增加玩家行为、角色详情和 Authoring 草稿接口时，仍应先定义结构化
请求/响应 Schema。错误统一返回 `status`、`code`、`message`、`failed_check`、
`retryable` 和字段路径；前端不能依赖自然语言关键字判断错误类型。

## 视觉和交互原则

- 世界时间和分钟使用世界包配置的日历格式，不在前端写死 1440 分钟或季节名；
- 地图、人物和事件使用服务端可见名称，内部 ID 仅用于请求参数和审计链接；
- 重要状态变化显示“发生了什么”和“谁/什么事件导致”，不把 God/Story
  内部角色名称强行显示为世界内 NPC；
- 资源、需求和关系的数值使用配置的 label、unit 和精度，避免长小数；
- 错误、等待、运行中、暂停、冲突和不可见状态使用明确的状态标签与颜色，
  同时提供文本说明，保证键盘和屏幕阅读器可用；
- 中英文和世界包自己的 locale 由投影提供，组件不维护世界词汇表。

## 前端结构

首版可以使用一个轻量静态前端，避免引入和 Runtime 无关的服务端框架：

```text
web/
  index.html
  app.ts
  api.ts          # typed HTTP client
  state.ts        # snapshot, event cursor, optimistic request state
  components/
    map.ts
    timeline.ts
    characters.ts
    interaction.ts
```

`api.ts` 的类型从 HTTP Schema 或稳定的 Python DTO 文档生成；组件只消费
投影，不导入 Platform、SQLite、YAML 或 Agent 工具代码。网页静态文件可以
由独立开发服务器提供，生产部署再由 `sim2 serve` 或反向代理托管。开发
阶段默认绑定 `127.0.0.1`，不把 API key、Provider 配置或 SQLite 路径发给
浏览器。

## 验收测试

Runtime Web Client 至少需要：

1. 地图投影能显示无坐标和有坐标的不同世界，且不出现世界专用分支；
2. 时间线使用 `after_event_id` 增量加载，重连和重复事件不会重复显示；
3. 玩家只能看到自己的可见投影，Observer 才能看到全局公开时间线；
4. 对话、交易、暂停和单步显示 Platform 的真实接受/拒绝结果；
5. 在一个长 Action 执行期间，网页不会重复提交决策，也不会把本地倒计时
   当成世界事实；
6. tick 并发、SQLite 回滚和网络重试不会让 UI 显示未提交的状态；
7. 视觉回归覆盖窄屏、无地图坐标、空时间线、错误、暂停和 Unicode 名称。

Authoring Workbench 的验收见
[`docs/design/world-authoring-web.md`](world-authoring-web.md)。先完成
Runtime/Platform 的单一状态和事件契约，再实现前端，避免用 UI 逻辑掩盖
运行时的因果问题。
