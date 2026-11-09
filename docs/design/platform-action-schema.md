# Platform 数据驱动动作与效果模型

## 目标

世界、物体、能力、元动作和 BT 都以配置形式导入 SQLite。Platform 不为“采集食物”“攻击”“制作食物”等语义动作编写业务分支；Platform 只实现通用的目标解析、条件求值、资源预留、时间推进、效果解释、事件提交和投影更新。

条件 DSL 只读取实例配置导入的状态路径、需求路径、库存路径、权限、地点标签和能力别名。能力别名、地点标签和属性含义属于故事包规则，不能写入 Platform 代码。

LLM 不读取原始 YAML，也不调用 Python handler。Platform 根据配置和当前状态生成精简的 `ActionView`、`BTView` 和 `ObservationSnapshot`，LLM 只提交带 ID、目标和参数的 Intent。

## 配置到执行链路

```text
配置文件
  ↓ Bootstrap Schema 校验
SQLite action_def、capability_def、recipe_def、bt_def、object_def
  ↓ Platform 当前状态过滤
ActionView、BTView、规则摘要
  ↓ LLM 选择并提交 Intent
Intent Gateway 校验目标、能力、条件、资源、权限
  ↓
ActionInstance
  ↓ 每个世界分钟执行通用效果原语
事件日志 + 状态投影 + 结果知识
```

## ActionDefinition

每个元动作必须提供以下字段：

| 字段 | 作用 |
|---|---|
| `id`、`version` | 稳定引用和版本 |
| `summary` | 给 LLM 看的短描述 |
| `target_spec` | 目标类型、选择条件、数量和可见性 |
| `requires` | 能力、前置条件、地点、物体和关系要求 |
| `duration_minutes` | 固定分钟数或由距离、数量计算 |
| `costs` | 启动、每分钟、成功、失败和退款成本 |
| `progress_effects` | 每分钟应用的效果原语 |
| `success`、`failure` | 条件、结果摘要和效果原语 |
| `interrupt_policy` | `pause`、`cancel` 或 `uninterruptible` |
| `event_types` | Platform 追加的领域事件类型 |
| `visibility` | 谁能看到动作和结果 |

`handler` 不属于运行时配置。Platform 的实现只有通用的 `effect_pipeline`、`condition_engine`、`target_resolver` 和 `event_writer`。

## Action、Recipe、BT 与 Capability 的边界

| 对象 | 作用 | 是否直接改变世界 | 示例 |
|---|---|---|---|
| Action | 一个可持续执行的原子能力，拥有目标、条件、时间、成本和效果 | 由 Platform 执行后改变 | 移动、采集、攻击、治疗、沟通 |
| Recipe | 输入到输出的声明式变换，描述材料、工作站、能力和产物 | 由通用 `craft_recipe` Action 执行后改变 | 谷物加水变成熟食，草药加水变成药剂 |
| BT | 组织 Action、Recipe 和子 BT 的控制流，处理顺序、选择、条件、失败、重试和中断 | BT 自身不改数值，节点中的 Action 负责改变 | 探索灰林、应对村庄威胁、制作口粮 |
| Capability | 角色执行 Action 或 Recipe 的资格和熟练度 | 熟练度由 Platform 事件更新 | 采集、烹饪、战斗、探索、研究 |

LLM 在决策边界通常选择 BT；BT 执行器再按分钟推进 Action。只有一个短小且没有控制流的任务可以直接提交 Action Intent。复杂能力由 BT 组合原子 Action，Platform 仍然是每一步的唯一执行者。

Decision Agent 只选择 Platform 提供的已验证 BT。它收到的是少量人类可读选择名称、适用条件、时长、成本摘要和结果摘要，返回当前视图中的 `choice`。SQLite 内部 `ref` 只用于 Platform 执行、审计和重放，不进入模型协议。BT 草案只由反思、世界演化或模板创建任务提出；Platform 校验成功后才注册并加入候选目录。

例如“制作熟食”不是一个硬编码的 `cook_food` 函数：

```text
Decision Agent 选择 craft_meal BT
  → 检查工作站和材料
  → craft_recipe Action 读取 cooked_meal Recipe
  → Platform 消耗谷物和水，执行 60 分钟，生成熟食
  → Platform 更新烹饪熟练度并写入事件
```

例如“应对灰林狼群”使用 BT 的选择节点：

```text
defend_village BT
  → sound_alarm Action
  → 选择 defend_position BT 或 flee_to_safety BT
  → defend_position BT 可以执行 defend Action 和 attack Action
```

BT 负责计划和控制流，Recipe 负责材料变换，Action 负责时间化执行，Platform 负责全部状态变化。

## 通用目标与能力

目标通过 `target_spec` 描述：

```yaml
target_spec:
  kind: resource_node
  select: nearest_known
  tags: [food]
  quantity: 1
  requires_visibility: true
```

能力是数据，不是 Python 类：

```yaml
capability:
  id: gather
  level_path: agent.capabilities.gather
  min_level: 0.40
  proficiency_gain: 0.01
```

Platform 从 Agent 能力表、伤势、疲劳、工具和目标状态判断 Action 是否可执行。能力升级由效果原语 `capability.add_xp` 写入状态。

## 通用效果原语

Platform 只解释受 Schema 约束的效果原语：

- `state.add`、`state.set`、`state.multiply`
- `inventory.consume`、`inventory.add`、`inventory.transfer`
- `resource.consume`、`resource.regenerate`
- `health.damage`、`health.heal`、`condition.add`、`condition.remove`
- `location.move`、`route.discover`
- `knowledge.add`、`knowledge.update_confidence`
- `relationship.adjust`
- `entity.create`、`entity.update`
- `event.emit`
- `capability.add_xp`
- `action.lock_resource`、`action.release_resource`

每个原语声明输入路径、类型、数量、来源和失败策略。原语只能修改 Platform 注册的组件，不能执行任意表达式、脚本或外部调用。效果在一个 SQLite 事务中按顺序应用；任何一个效果失败时整次提交回滚，并返回 `IntentValidationError` 或 `EffectExecutionError`。

## 示例：采集食物

```yaml
id: gather_food
version: 1
summary: 采集附近已知的食物资源，获得食物并增加疲劳
target_spec:
  kind: resource_node
  tags: [food]
  select: nearest_known
  requires_visibility: true
requires:
  capabilities: [{id: gather, min_level: 0.40}]
  conditions: [actor.alive, actor.can_move, target.available]
duration_minutes: 144
costs:
  reserve: [{path: actor.inventory.tool_durability, amount: 1}]
  per_minute: [{path: actor.energy, op: add, value: 0.8}]
progress_effects:
  - op: resource.consume
    target: target
    quantity_formula: target.yield_per_action / 144
success:
  condition: target.resource_remaining > 0
  effects:
    - {op: inventory.add, item: food, quantity_formula: target.yield_per_action}
    - {op: capability.add_xp, capability: gather, value: 0.01}
  event_types: [resource.gathered]
failure:
  condition: actor.incapacitated or target.depleted
  effects: []
  event_types: [resource.gather_failed]
interrupt_policy: pause
visibility: actor_and_observers
executor: effect_pipeline
```

Platform 会从目标资源节点读取 `yield_per_action`，扣减资源、增加背包食物、增加疲劳和熟练度，并写入 `resource.gathered`。LLM 只看到“采集附近食物，耗时 144 分钟，预计消耗工具耐久和疲劳，成功获得食物”。

## 示例：制作食物

制作不是特殊硬编码动作，而是配方数据加通用 `craft` Action：

```yaml
recipe:
  id: cooked_meal
  station_tags: [fireplace, kitchen]
  inputs:
    - {item: grain, quantity: 1}
    - {item: clean_water, quantity: 1}
  outputs:
    - {item: cooked_meal, quantity: 2}
  requires: [{id: cooking, min_level: 0.30}]
  duration_minutes: 60
  effects:
    - {op: inventory.consume_inputs}
    - {op: inventory.add_outputs}
    - {op: capability.add_xp, capability: cooking, value: 0.02}
```

`craft` Action 的目标是 `recipe_def`，Platform 校验工作站、材料、能力和库存后执行同一套 `consume_inputs` 与 `add_outputs` 原语。新增配方只增加配置和 SQLite 记录，不增加 Platform 分支代码。

## 示例：攻击与受伤

攻击同样由目标、能力、成本和效果描述：

```yaml
id: attack
summary: 使用装备攻击一个可见且可达的敌对目标
target_spec: {kind: entity, tags: [combatant], relation: hostile}
requires:
  capabilities: [{id: combat, min_level: 0.30}]
  conditions: [actor.alive, target.visible, target.reachable]
duration_minutes: 6
costs:
  per_minute: [{path: actor.energy, op: add, value: -1.0}]
  reserve: [{path: weapon.durability, amount: 0.1}]
success:
  condition: attack.resolved
  effects:
    - {op: health.damage, target: target, formula: actor.attack_power + weapon.damage - target.defense}
    - {op: relationship.adjust, target: target, relation: hostility, value: 0.15}
    - {op: event.emit, type: combat.attack_resolved}
    - {op: condition.add, target: target, condition: injured, duration_minutes: 240}
interrupt_policy: cancel
visibility: participants_and_witnesses
executor: effect_pipeline
```

Platform 根据伤害效果更新目标生命、伤势、状态效果、关系和事件；伤势再通过 `eligible_bt_catalog` 影响目标后续可执行 BT。攻击、受伤和死亡都不需要为每种武器编写新的业务函数。

## BT 与 Action 的连接

BT 只负责顺序、选择、条件和长时计划，不直接修改数值。BT 节点引用 `action_id`、目标绑定和参数绑定：

```yaml
- type: action
  action_id: gather_food
  target_binding: nearest_known_food_node
  parameter_bindings: {location_id: target.location_id}
```

Platform 创建 `bt_instance` 后，按 ActionDefinition 执行。LLM 选择 BT 或分支，Platform 解析目标、校验条件、创建 ActionInstance、按分钟推进并提交效果。

## LLM 视图

Platform 每次决策只生成相关候选，不把全部配置喂给模型：

```text
可选行动：采集食物
目的：补充食物
目标：附近已知食物节点
耗时：144 分钟
需要：采集能力 0.40，目标可见且有资源
消耗：工具耐久 1，疲劳每分钟 +0.8
成功：背包食物增加，采集熟练度增加
失败：目标枯竭或角色失去行动能力
```

LLM 返回 `action_id`、目标 ID、参数和 Intent 摘要。Platform 不信任摘要，只使用 SQLite 中的 ActionDefinition 和当前状态执行。
