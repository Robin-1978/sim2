# Progression and Civilization Design

能力成长、知识发现、文明进步和规则不是同一种数据。它们通过 Platform 的
通用效果管线连接起来，所有变化都来自已完成的行为结果。

## Agent roles

运行时只需要三类角色：

1. **Individual Agent**：一个统一 Agent Engine，包含 Decision、Reflection
   和 Innovation 阶段。它选择并执行行为，复盘结果并沉淀个体经验。
2. **Story Agent**：消费探索、研究、关系和文明里程碑，推进目标、剧情、
   知识和已注册 BT/配方解锁。
3. **God Agent**：可插拔的全局结构角色，观察资源、势力和文明趋势，提出
   规则、制度、势力策略和结构实体。它不能直接增加资源、需求、关系或生产
   数值。

`WorldBuilder Agent` 只在创建新世界模板时运行，不参与日常世界循环。能力、
研究、治理和文明进度的证据由 Platform 记录，能力更新由角色 Agent 提案，
文明阈值直接唤醒 Story/God，不为每种行为单独创建 Agent。

## 四类状态

| 类型 | 所属 | 例子 | 变化来源 |
|---|---|---|---|
| Capability | 角色 | gather、cooking、research、navigation | 行为产生证据；同一角色 Agent 的 Reflection/Innovation 确认后提交能力更新 |
| Knowledge / Discovery | 角色或世界 | 新矿点、污染事实、旧矿路线 | 探索、研究、沟通和检查产生事实，并可解锁已注册 BT、配方或地点 |
| Civilization Progress | 世界 | food_system、public_health、governance_capacity、trade_network | Platform 累积行为证据；达到阈值后直接唤醒 Story/God 处理里程碑 |
| Rule / Policy | 世界 | 配给制度、通行制度、巡逻制度 | God 或配置提出声明式政策；Platform 激活后约束后续行为 |

规则是“如何处理结果”的声明，不是结果本身。`cooking=0.62` 是能力，
`research:clean_water=2/2` 是研究进度，`water_quality=0.72` 是世界状态，
“研究完成后开放净水制度”才是规则。

## 能力成长

Action 或 BT 的成功结果只记录能力证据，不自动提升能力。Reflection/Innovation 阶段根据重复成功、难度、失败修正和结果质量判断是否值得提交能力更新：

```yaml
intent_type: update_capability
capability: cooking
delta: 0.04
evidence: "Three completed meals used the configured recipe without failure."

## Emergent contribution institutions

Contribution is not an initial person attribute. A world may begin with no
contribution metric, no ledger, and no quota policy. NPC behavior only produces
the verified evidence and threshold event; only God can create or activate a
public-allocation institution. The institution proposal must name its evidence
selectors and minimum count, which Platform checks against persisted events.

After acceptance, Platform stores the institution and its policy as declarative
SQLite data. The policy names the event evidence that can create ledger
entries, the categories, period, aggregation, minimum safety allocation, and
whether historical events may be backfilled. A completed configured behavior
can then append a verified entry using its source event. Agents cannot assign
themselves points or change the ledger directly.

The first quota calculation must preserve a configured minimum allocation for
every eligible person. Only the remaining pool can be distributed using the
new ledger. If the proposal is rejected, the world keeps its previous food
rules and no contribution state is created.
```

Platform 验证 evidence 是否对应该角色真实 outcome、能力是否在配置目录中、
更新是否超出上限，再记录 `capability_progress` 并更新能力值。能力值只能影响配置
声明的条件、时间、成本或成功率；代码不写
`if cooking > ...`。例如：

能力证据按 Capability 的 `uses` 或配方中的 `capability.add_xp` 产生
`capability.evidence`。Reflection 看到的候选和 Platform 的提交校验只统计该
角色、该能力、上次更新之后的匹配证据；普通 `behavior.completed` 不能给任意
能力充数。角色必须已具备该能力，除非 Capability 明确声明
`acquirable: true`。因此研究不会提高烹饪，巡逻也不会提高采集。

```yaml
capability_modifiers:
  cooking:
    craft_recipe:
      time_multiplier: {at: 1.0, level_1: 0.9, level_2: 0.78}
      failure_probability: {at: 0.20, level_1: 0.12, level_2: 0.05}
  navigation:
    travel_to:
      time_multiplier: {at: 1.0, level_1: 0.9, level_2: 0.8}
```

旅行速度、采集效率和烹饪质量都通过同一个 duration/cost/effect 估算器
计算。Agent 看到当前能力、预计时间和预计消耗，不能自己填写数字。

## 发现和研究闭环

探索发现资源节点只产生地点、事实和可用对象，不凭空增加库存。之后的
`gather_resource` 才减少节点数量并增加角色库存，同时提升采集能力。制作
食物消耗材料并产出食物，同时提升烹饪能力。研究行为可以多次执行，进度
写入 `progress_state`；未完成时只保留进度，完成时写入知识事实并产生
`research.completed`。

研究来源不是一次性开关。一个来源可以在实体数据中声明一个
`research_discovery`，也可以声明任意长度的 `research_discoveries` 阶段列表。
每个阶段有自己的事实键、所需进度和可选 `supersedes` / `status`。完成一个阶段
只关闭这个阶段；只要仍有可用阶段，通用 `research_source` Action 继续出现在
执行目录中。阶段耗尽也不代表世界停止研究：God 可以根据已验证结果提出新的
研究来源实体或新的研究行为，Platform 按普通实体/BT Schema 校验后写入 SQLite。
因此后续问题由运行中的证据和 Agent 提案产生，而不是预先写死完整科技树。

知识事实保留生命周期：`active` 是当前采用的结论，`superseded` 表示被新的
已验证结果替代，`obsolete` / `disabled` 表示当前不再可用。旧事实不会被删除，
回放和审计仍能看到它；检索和研究门控会跳过已淘汰阶段，避免 Agent 继续依据
过时结论行动。

完成研究后的闭环是：

`研究进度 → 新事实 → Story 解锁目标/BT/剧情 → 角色执行新行为 → 世界状态变化`

如果事实影响公共水源、贸易路线或派系制度，事件按世界包配置的累计证据门控
唤醒 God。God 只能
提出规则、制度、势力策略或结构实体；实际水质、库存和生产结果仍由角色
行为执行。

## 文明进步

治理、巡查、研究、生产、交易、调解和防御都可以配置世界级进度：

```yaml
progress:
  key: civilization:governance_capacity
  scope: world
  amount: 1
  required: 5
```

进度达到阈值时 Platform 产生 `civilization.milestone`，并保存完成行为、
事实和前后值。Story 可以把里程碑转成角色目标、剧情和已注册 BT；God
可以观察趋势并提出公共制度或派系变化。里程碑本身不能直接把粮食、信任
或治安数值调高，必须有后续行为产生这些变化。普通治理和研究事件不会逐条
唤醒 God；世界包在 `evolution.yaml` 的 `wakes` 中声明
`on`、`for`、`count` 和可选的 `every`，例如两次研究完成、五次治理结果或
一次公共安全事故。启动时 Platform 才把这几项编译成内部事件组和证据选择器。
这个门控按真实事件计数，不按每天固定调用计时。

事件组可以用 `distinct_by` 只统计“新的”证据键，重复结果不会再次唤醒；
`manual_only` 事件只通过行为/Action 的显式声明送达。世界包还可以声明
`stagnation: {window, max_interval}`：Platform 每个窗口比较每个角色自己决策
视图中的累计可执行选项，没有新增时按 1、2、4…个窗口的间隔发布派生事件
`world.stagnation`，其内容列出每个角色现在能做什么、被哪些条件挡住；
被挡住的 Action 会直接给出失败的检查（未满足的前置条件、耗尽的资源、
制度分配的资金池与政策下限），而不是笼统的拒绝文本。每个窗口最多评估
`max_characters` 个角色（默认 12，上限 64），更大的名册按 id 顺序轮转，
因为每评估一个角色都要编译一次决策视图，其成本随人口增长。
具体字段见 `docs/world-authoring.md`。

## 规则激活和验证闭环

God 提交的规则必须经过 Platform 校验，并使用声明式条件和效果。通用
`RuleCompiler` 将已注册规则同时用于四个边界：

1. 把条件和效果摘要加入相关 Agent 的动态上下文；
2. 在 Action 启动前拒绝违反 `deny`/`deny_access` 规则的行为；
3. 在世界条件从未满足变为满足时产生一次规则信号，并按配置唤醒 God 或 Story；
4. 对规则明确列出的 BT 记录 `world.rule.followed`，把行为结果和实际 delta
   交给下一次全局复盘。

规则本身从不增加库存、需求、关系或治安。它只允许 Platform 约束行为或发出
信号，后续行为的配置效果才是实际状态变化。没有行为/Action follow-up 的 God
规则可以作为观察性政策保存，但不能被质量审查标记为已完成；需要等到规则列出的
行为完成并产生可观察结果。能力或文明进度也不得由 Platform 隐式累加，必须由
Agent 提案或由阈值事件唤醒 Story/God。

规则条件只能引用 Agent、target、context 或已配置的 `world.*` 字段；未知字段会
在注册时拒绝。`signal` 只产生事件，不能声称行为已被门控；需要阻断/门控 BT 时使用
`behavior_constraint` 并列出可见 BT 名称。
