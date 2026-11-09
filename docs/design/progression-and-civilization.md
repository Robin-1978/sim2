# Progression and Civilization Design

能力成长、知识发现、文明进步和规则不是同一种数据。它们通过 Platform 的
通用效果管线连接起来，所有变化都来自已完成的行为结果。

## 四类状态

| 类型 | 所属 | 例子 | 变化来源 |
|---|---|---|---|
| Capability | 角色 | gather、cooking、research、navigation | 成功行为产生能力经验，Platform 按配置曲线提升能力值 |
| Knowledge / Discovery | 角色或世界 | 新矿点、污染事实、旧矿路线 | 探索、研究、沟通和检查产生事实，并可解锁已注册 BT、配方或地点 |
| Civilization Progress | 世界 | food_system、public_health、governance_capacity、trade_network | 治理、研究、生产、交易和防御的 outcome 累积到 `progress_state` |
| Rule / Policy | 世界 | 配给制度、通行制度、巡逻制度 | God 或配置提出声明式政策；Platform 激活后约束后续行为 |

规则是“如何处理结果”的声明，不是结果本身。`cooking=0.62` 是能力，
`research:clean_water=2/2` 是研究进度，`water_quality=0.72` 是世界状态，
“研究完成后开放净水制度”才是规则。

## 能力成长

Action 或 BT 的成功结果可以配置通用 `capability_xp` 效果：

```yaml
effects:
  - type: capability_xp
    capability: cooking
    value: 0.04
    source: craft_recipe
```

Platform 记录 `capability_progress`，按故事包配置的上限、曲线和阈值更新
角色能力。能力值只能影响配置声明的条件、时间、成本或成功率；代码不写
`if cooking > ...`。例如：

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

完成研究后的闭环是：

`研究进度 → 新事实 → Story 解锁目标/BT/剧情 → 角色执行新行为 → 世界状态变化`

如果事实影响公共水源、贸易路线或派系制度，事件同时唤醒 God。God 只能
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
或治安数值调高，必须有后续行为产生这些变化。

## 规则激活边界

God 提交的规则必须经过 Platform 校验，并引用已有的条件、行为、效果或
进度类型。当前实现只把规则保存到 `rules.world_rules`，还没有把规则编译
成运行时约束，因此规则提案仍标记为待后续验证。下一步需要实现通用
`RuleCompiler`：把已验证规则映射到 AgentView 的政策摘要、行为候选条件、
资源结算和 outcome 验证；规则没有对应行为和效果时必须拒绝注册。
