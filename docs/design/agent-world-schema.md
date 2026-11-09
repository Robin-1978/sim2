# Agent Context 与世界模板协议

## 设计结论

LLM 使用简短的 XML-like 文本标签读取上下文，使用原生 JSON Schema 工具和 JSON Intent 表达请求。SQLite 保存运行时事实，SQL 表用于事务、索引和约束，内部 JSON 只作为配置交换格式、AgentView 投影和 Intent 协议。LLM 不直接编辑完整世界状态，也不执行 SQL。原始配置中的条件表达式、数据库字段名、效果原语和内部路径只供 Platform 使用，不直接进入 Prompt。

```text
Authoring YAML/JSON
  → Bootstrap normalized domain objects
  → SQLite tables + JSON payloads
  → AgentViewCompiler
  → compact tagged text prompt
  → LLM Intent JSON
  → Platform validation and transaction
```

## 统一 AgentView

Platform 根据当前 Agent 角色和权限生成内部视图对象，再渲染为简短的 XML-like 文本。视图只包含与当前任务相关的信息，使用简短英语字段，避免把数据库列、内部实现和无关实体传给 LLM。工具的原生参数契约通过 API 的 JSON Schema 传递，不重复塞进 Prompt。

Decision Agent 只选择 Platform 已经注册、校验并过滤过的行为。它不编写 BT、不填写效果、不计算成本，也不直接选择底层 Action。BT 创新由 Reflection/Evolution、God 或 WorldBuilder Agent 通过单独的 Proposal Function 提出，Platform 校验成功后才会进入后续候选目录。

每个 Agent 的 BT 目录不是永久固定值：配置创世时导入并校验初始目录；运行中新的 BT 先进入 Platform 验证流程，验证节点、Action 引用、参数、循环、资源和权限成功后注册，并按提案作用域加入发起 Agent 或授权群体的目录。未注册、未验证、当前条件/资源不满足的 BT 不会出现在 `executable_behaviors`，因此 Agent 永远不会从“未验证选项”中选择。

世界配置可以声明 `fallback_behavior_ids`，例如安全地点的恢复 BT。它们和角色专属 BT 一样先经过 Bootstrap/Platform 校验；当角色没有其他可执行工作时，Agent 可以选择恢复行为。只有恢复行为也不满足条件时，Decision Agent 才返回 `wait`，而不是把“没有选择”当成正常的长期策略。

```json
{
  "context_version": "1",
  "role": "agent_decision",
  "world_time": {"minute": 1530, "label": "Day 2 01:30"},
  "self": {
    "id": "he_yan",
    "role": "watch_captain",
    "status": "injured",
    "location": "north_gate",
    "health": 72,
    "energy": 44,
    "goal": "Build a reliable warning and patrol system"
  },
  "drives": [
    {"id": "safety", "urgency": 0.91, "reason": "boundary threat is high"},
    {"id": "esteem", "urgency": 0.58, "reason": "patrol failure needs repair"}
  ],
  "observation": {
    "facts": ["fresh marks at the north gate", "one civilian is nearby"],
    "source": "direct_sight",
    "expires_in_minutes": 30
  },
  "known_facts": [
    {"fact": "north gate marks are new", "source": "observation", "confidence": 0.88}
  ],
  "active_task": null,
  "available_behaviors": [
    {
      "ref": "behavior:sound_alarm",
      "name": "Raise warning alarm",
      "when": ["A boundary threat is confirmed.", "You are at a watch post."],
      "time": "6 minutes",
      "cost": ["Attention: low"],
      "result": ["The village receives a warning."],
      "failure": ["The alarm device is unavailable."]
    }
  ],
  "last_platform_result": null
}
```

`available_behaviors` 只包含 Platform 已经验证过的 BT 或 Action。实体、地点、物体和规则也使用相同的简洁视图：`ref`、`name`、`type`、`summary`、`known_state`、`distance`、`visibility` 和 `constraints`。隐藏真相、完整数据库和其他 Agent 的私有记忆不进入视图。

候选资格必须在两个边界重新计算：编译 AgentView 时按当前状态、入口条件和资源过滤；提交选择时 Platform 再按同一套配置条件校验一次。需求阈值只提供直白的驱动提示，不是食物行为的硬门槛；只要库存或服务条件满足，Agent 可以在阈值前选择进食或取粮，Platform 仍会校验实际资源。旧上下文或模型直接提交过期选择只能得到可重试的 `entry_condition` 拒绝，不会启动无法执行的行为。

这里的 `ref` 是 Platform 内部字段；渲染给 Agent 的视图只保留 `name`/`choice` 等可读名称。工具 schema 同样使用 `entity`、`recipient`、`record` 和 `offer` 这类名称字段，Platform 在权限范围内解析为内部引用。

这段 JSON 是 Platform 内部的单个决策切片，不是发送给 LLM 的完整 Prompt。发送时它被渲染为简短文本；函数名和参数只通过 API 的 JSON Schema 工具通道提供。Context Compiler 默认只保留：当前 Agent、当前地点、当前事件、最高的 3 个驱动、直接关系、最相关的 3 到 8 条知识或记忆、最多 5 个可执行候选和最近一次 Platform 结果。God Agent 使用世界级摘要和按需查询；WorldBuilder Agent 分阶段处理世界模板。上下文预算先保证当前错误、活动任务、直接关系和候选行为，再压缩长期摘要和低相关信息。

关系使用人类可读摘要表达：

```json
{
  "person": "Lin Bo",
  "relation": "village leader",
  "trust": "high",
  "recent_change": "He concealed the old pact from the council.",
  "relevant_to_goal": true
}
```

Agent 需要更远或更详细的信息时，通过 `observe_nearby`、`recall_memory` 或 `preview_behavior` 按需获取。工具结果也必须是短句、有限列表和明确来源，Platform 不返回原始表格或长事件日志。

## ActionView 与 BTView

LLM 永远不接收 BT JSON/AST、节点类型、内部 predicate 或 Action handler。Bootstrap 将 BT 定义存入 SQLite；AgentViewCompiler 只把当前可执行 BT 翻译成简短自然语言：目的、何时适用、需要满足的条件、步骤名称、预计时间、成本、影响、成功/失败结果和中断方式。Agent 只提交当前视图中的人类可读 `choice`（例如 `Explore Greywood`），Platform 在同一份候选视图中做规范化的精确映射，再用 SQLite 中的原始 BT 执行。

`behavior_ref`（例如 `behavior:explore_greywood`）仍然是 Platform、SQLite、审计和重放使用的内部键，不进入模型可见的上下文、工具参数或工具结果。选择映射不做模糊搜索：大小写、空格和标点归一化后必须唯一匹配；未知、重名或已经过期的 choice 会被拒绝，并返回当前可选名称。配置可为重名行为声明 `agent_label`；如果仍重名，编译器只添加人类可读的 `(option 1)` 后缀。旧集成可以在 Platform 边界暂时提交内部 ref，但它不是 Agent 协议。

同一规则适用于可见地点、人物、物体和交互对象：上下文显示名称，Agent 提交名称，Platform 只在当前权限和可见范围内做精确解析。两个实体同名时返回歧义错误和带上下文的可选名称；Platform 不做模糊猜测，也不接受 Agent 自己填写的数据库 ID。交易 offer 使用当前互动会话中的人类可读描述，内部 offer ID 只留在 Platform 事务和审计中。

每个元动作和 BT 都必须生成以下英文视图字段：

| 字段 | 含义 |
|---|---|
| `choice` | 当前视图中供 Agent 选择的简短名称；Agent 原样回传 |
| `ref` | Platform 内部稳定引用；仅用于 SQLite、审计和执行，不渲染给 LLM |
| `name` | 人类可读名称（通常与 `choice` 相同） |
| `summary` | 一句话目的 |
| `when` | 简短自然语言条件 |
| `purpose` | 行为的目标 |
| `requires` | 当前选择需要满足的条件 |
| `steps` | BT 内部的简短步骤名称 |
| `target` | 人类可读的目标范围 |
| `time` | 人类可读执行时间 |
| `cost` | 人类可读消耗 |
| `impact` | 可观察的状态或事件影响 |
| `success` | 成功后的可观察结果 |
| `failure` | 可能失败或被拒绝的原因 |
| `interrupt` | 是否可暂停、取消或不可中断 |

英语视图由规范化配置和模板编译生成，不能由 LLM 随意改写 Platform 的真实数值。世界配置可以保存中文说明和英文说明，英文视图是给 LLM 的稳定协议。内部 predicate、path、opcode、handler 和表名永远留在 Platform 层。

## Intent JSON

Decision Agent 返回选择 Intent，而不是完整状态或 BT 定义：

```json
{
  "intent_type": "select_behavior",
  "actor_id": "he_yan",
  "choice": "Raise warning alarm",
  "target": {"location": "North Gate"},
  "parameters": {"alarm_type": "boundary"},
  "reason": "The threat is high and the watch post is available.",
  "confidence": 0.87
}
```

Platform 使用 SQLite 中的定义重新计算条件、成本和效果，忽略 LLM 自己填写的数值。校验失败时返回简短英语 `message`、`failed_check`、`repair_hint` 和新的可选项，LLM 在同一 ReAct 任务中修正 JSON。

## ReAct 会话协议

Agent Engine 以一次会话处理一个语义任务。会话不等待模拟分钟，也不把 LLM 调用放进 Action 的进度循环：

```text
AgentView + task
  → model response
  → tool_call(name, arguments)
  → schema/permission validation
  → tool_result or tool_error
  → append compact result to the next context
  → model response ...
  → final Intent
  → Platform accepted / rejected
```

工具错误和 Platform 拒绝都必须返回 `status`、`failed_check`、简短 `message` 和 `retryable`（适用时）。相同工具参数超过会话预算后停止重复调用。Intent 只有在 Platform 返回 `accepted` 或 `wait` 时结束会话；会话达到调用上限后返回 `budget_exhausted`，不产生副作用。原生 OpenAI-compatible `tool_calls` 与本地模型输出的 JSON `tool_call` 使用同一内部信封。决策 Agent 若已经有活动 BT，Engine 直接跳过 LLM，会话只在完成、失败或中断边界重新开启。

上下文生命周期是一次唤醒一次快照：每次 Decision、Reflection 或 God Agent 被唤醒时，Platform 从 SQLite 当前投影、记忆和相关事件重新编译 AgentView。只有当前 ReAct 会话保留 assistant/tool/result 消息并追加交互结果；会话结束后丢弃消息历史，下一次唤醒不会继承上一会话的完整 Prompt，只会重新获得当前世界状态和持久化摘要。

运行时渲染把稳定的协议、规则和工具名称放在动态世界快照之前，以保持 Provider 前缀缓存；世界时间显示为日、时刻和季节，同时保留内部分钟供审计。若上下文超出预算，Compiler 先移除可通过只读工具重新获取的背景段落，始终保留当前可选 BT、活动执行和最终任务。原生工具 ReAct 在下一轮只追加工具结果，不重复发送完整快照；一个模型响应最多提交一个被接受的有副作用 Intent。工具返回和感知投影会过滤执行实例、数据库引用和行为内部键，Prompt 只显示可读角色名、行为名和来源/置信度。

## 记忆、经验与三类上下文

记忆会进入上下文，但不会以“全部历史”的形式进入。Platform 始终记录权威事件、Action/BT 的预期与实际结果、对话和探索结果；这些原始记录不等于经验。反思阶段只在发现内容新颖、稳定且能改变未来选择时提交 `remember_episode` 或摘要 Intent，Platform 负责去重、来源、置信度、重要性和激活次数。没有可复用结论时返回 `wait`，不生成每日流水账。

| 唤醒阶段 | 必须提供的动态内容 | 记忆/经验策略 | 不提供 |
|---|---|---|---|
| Decision | 当前时间、地点、状态/需求/驱动、目标、可见事实、直接关系、活动 BT、已过滤的可执行 BT 及成本/时间/结果、最近一次 Platform 结果 | `short_term` 保留当前对话/交易/观察等工作上下文；`long_term` 保留经反思确认的经验和摘要。两层都按当前目标、地点、驱动和候选 BT 组成的查询排序，通常只注入 3–8 条，附来源和置信度；必要时用 `recall` 指定范围追加查询 | 全量记忆、完整事件日志、隐藏事实、目录外 BT、其他角色私有记忆 |
| Reflection / Evolution | 上次反思窗口以来的完成/失败/中断 outcome 摘要，expected 与 observed delta，相关对话/探索，当前信念、目标、能力和已有经验 | 先看窗口内的有限结果摘要，必要时用只读查询取回更早结果；再检索相关长期经验，并显示重复/相似提示，避免重复写入。输出只能是经验、信念、目标或 BT/技能提案，均需 Platform 接受 | 直接修改资源、关系或世界；把每次成功动作都写成经验 |
| God | 世界时间、公开世界属性和规则版本、势力/资源/风险聚合、近期公共事件、已提交世界提案及其 expected/observed 结果、按周/月的资源、关系和目标趋势、待处理触发器 | 不读取角色私有记忆；只看公开结果和聚合指标。每次只提出一个可验证的实体、规则、关系或事件提案，下一窗口比较效果后再决定是否调整 | 角色私密对话/记忆、未授权状态、没有结果证据的因果结论 |

三类上下文都遵循同一顺序：稳定协议和角色政策 → 工具 Schema → 稳定身份/世界规则 → 本次唤醒的动态切片 → 当前任务；只有当前 ReAct 的工具调用和错误结果继续追加。SQLite 中的长期记录通过检索重新注入，而不是跨会话保留整段 Prompt。这样“选择/执行 → 结果 → 反思或 God 评估 → 记忆或世界提案 → 下一次选择”形成闭环，同时保持上下文短而可审查。

## LLM 创建新世界模板

创建新世界使用独立的 `WorldBuilder Agent`，它和运行中的 God Agent 分离。WorldBuilder Agent 生成 `WorldTemplateDraft` JSON，Platform 校验后才创建新的配置包和 SQLite 实例。

```json
{
  "template_version": "1",
  "world": {
    "id": "mist_valley",
    "title": "Mist Valley",
    "premise": "A frontier settlement depends on a failing pact.",
    "time_unit": "minute",
    "calendar": {"minutes_per_day": 1440}
  },
  "entities": [],
  "capabilities": [],
  "actions": [],
  "recipes": [],
  "behavior_trees": [],
  "rules": [],
  "initial_events": [],
  "agent_views": {"public_summary": "A frontier settlement depends on a failing pact."}
}
```

WorldBuilder Agent 的流程是：

1. 根据用户自然语言需求生成 Draft JSON。
2. 调用只读 `validate_world_template`。
3. Platform 返回 Schema、引用、循环、能力、效果、资源、时间和安全错误。
4. Agent 在 ReAct 中修正 Draft。
5. Platform 通过后生成规范化配置包、计算哈希并创建新的 SQLite 实例。
6. Bootstrap 导入实体、能力、元动作、BT、规则、初始知识、提示词和开局事件。

WorldBuilder Agent 可以提出世界实体、初始 Agent、元动作、配方和基础 BT，但不能写文件、执行 SQL 或直接创建实例。创建结果仍然经过 Platform 的白名单原语和事务初始化。
