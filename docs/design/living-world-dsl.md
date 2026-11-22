# 活世界 DSL：从写死内容到可生长内容

Status: **IMPLEMENTED**
Last updated: 2026-11-22

## 1. 问题

《长夜当铺》的第一版把 21 个案子写死。这样可以把 10 分钟拉长到 3 小时，但违背了项目真正的优势：

- Agent 有自己的记忆、目标、关系；
- Director 可以选下一段戏剧压力；
- God 可以在运行时创建实体、Action、Behavior、Rule、Relationship；
- Story 可以从已验证事件里写出下一拍；
- Platform 拥有全部状态，LLM 不能凭空改库存、价格、债务。

写死内容把这些能力降级成了“LLM 对话皮肤”。活世界需要 DSL 支持**参数化、延迟、动态引用和可验证的生长契约**。

## 2. 已实现的 DSL 能力

### 2.1 动态属性与物品引用

条件里可以写：

```yaml
requires:
  - world[context.case_slot] == 0
  - actor.inventory[context.item_id] >= 1
```

运行时，`context.case_slot = dyn_case_1` 会解析成 `world.dyn_case_1`，`context.item_id = sealed_tag` 会解析成 `actor.inventory.sealed_tag`。

实现位置：

- `ConditionEngine.resolve_dynamic_references()`
- Action / Behavior 的标量参数进入 `context.*`
- 未知动态引用会解析成 `__unresolved__`，条件 fail closed，而不是静默通过

### 2.2 `world_property_set`

以前只有 `world_property_delta`，设置一个槽位只能靠 `+N` 和 `maximum` 夹。现在：

```yaml
effects:
  - type: world_property_set
    property: context.case_slot
    value: 0
```

`value` 可以是字面量，也可以来自 `context.value`。Activity 与 world tick 都支持。

### 2.3 `deferred: true`

God 经常需要创建一个“以后才会出现”的行为：

```yaml
definition:
  id: dyn_sell_1
  deferred: true
  entry_conditions:
    - world.dyn_case_1 == 1
    - actor.inventory.sealed_tag >= 1
  nodes:
    - type: action
      action_id: travel_to
      parameters: {location_id: watchhouse}
    - type: action
      action_id: dynamic_sell
      parameters:
        item_id: sealed_tag
        case_slot: dyn_case_1
        price: "40"
        faction: watch_rep
```

注册时它不会因为“现在条件不满足”被拒绝；决策视图只在条件满足时把它放进行动列表。没有 `deferred` 的行为仍然必须现在可执行，避免死内容。

### 2.4 契约级工具预算

复杂 God 会话可以声明：

```yaml
contracts:
  god_structural:
    max_tool_calls: 30
    max_proposals: 7
    required_content:
      growth_cell: true
      behaviors_using_action: [dynamic_pawn, dynamic_return, dynamic_sell]
```

- `max_tool_calls` 由 runner 传给 `AgentEngine`。
- `behaviors_using_action` 要求存在某个使用指定 Action 的 Behavior，防止 God 只创建一个客人就结束。

### 2.5 可见物品名与实体 id

LLM 会写“封蜡灯”，库存键可能是 `entity_abc123`。现在有两层规范化：

1. **注册时**：Behavior 的 `actor.inventory.<中文名>` 和节点 `item_id` 会按实体名规范化到实体 id；支持精确匹配和唯一模糊匹配。
2. **执行时**：`resolve_activity_parameters` 对 `item_id` / `target_item` / `source_item` / `inventory_item` 做同样处理。

如果存在多个候选，保持原值，避免把“灯”错误映射到某一件灯。

### 2.6 动态物品标签

`entity_type=item` 的实体：

```json
{
  "entity_type": "item",
  "name": "港务局封蜡铜牌",
  "state": {"uses": ["卖给港务局", "交给修会"]}
}
```

会进入：

- `player_view.item_labels`：物品栏显示中文名；
- `player_view.item_uses`：显示“用途：…”；
- God 创建的行为可以使用它的 `entity_id`。

### 2.7 事件唤醒

`public_event` 和目录事件现在走：

```python
events.publish(event_type, actor_id, minute, payload, wake="configured")
```

而不是只 `append`。这样 `case.pawned_dynamic` 能唤醒 `god_after_dynamic_pawn`，`case.resolved` 能唤醒 Story。没有配置的消费者不会收到任务。

### 2.8 实体提案的 LLM 友好别名

`propose_entity` 接受常见的模型写法：

- `location` / `place` → `location_id`
- `behaviors` → `initial_bt_ids`
- `traits: ["谨慎", "口吃"]` → `{谨慎: 0.7, 口吃: 0.7}`
- `beliefs: ["..."]` → `[{id, statement, confidence}]`
- `inventory: ["灯"]` → `{"灯": 1}`
- `needs: {safety: 0.4, money: 0.8}` → 0..100 比例并补默认值

`ultimate_goal` 缺失时会明确拒绝；不会用 `goal` 静默替代，因为目标必须可验证。

## 3. 仍然有限的 DSL

这些是下一轮应该补的，不应假装已经解决：

| 限制 | 影响 | 建议 |
| --- | --- | --- |
| `costs.reserve` 不能参数化金额 | 活案子只能固定 20 金币当金 | 增加 `cost_parameter` 与原子校验 |
| 目录条目只能静态配置 | God 不能追加新的 trade offer | 增加运行时目录扩展或统一用 Action |
| 没有表达式价格 | 价格不能随声望/怀疑变化 | 增加只读公式字段，仍由 Platform 计算 |
| 没有跨 Agent 物品转移 | 玩家不能直接把东西给客人 | 增加 `inventory_transfer` 原语与原子回滚 |
| 没有随机权重 | 内容变化靠 LLM，不靠 RNG | 可加 seed + 权重，保持可复现 |
| 没有 `all/any` 聚合 | 不能写“所有客人都满意” | 增加 `count(...)` 或 `all(...)` 谓词 |
| `deferred` 没有过期 | 未触发的未来行为会留在定义表 | 增加 `expires_after_minutes` 与清理审计 |

## 4. 验证

聚焦测试：

```bash
python -m pytest -q tests/test_night_pawnshop.py tests/test_dsl_living_bindings.py \
  tests/test_condition_engine.py tests/test_action_binding.py \
  tests/test_definition_schema.py tests/test_definition_validation.py
```

远程活世界探针：

```bash
python scripts/verify_night_pawnshop_growth.py --provider remote
```

探针检查：

1. remote Director 选出 growth cell；
2. remote God 创建物品、客人、收当/归还/卖出行为；
3. 玩家收当后物品进入背包，`dyn_case_N=1`；
4. 玩家卖出后 `dyn_case_N=3`，金币和阵营声望变化；
5. remote Story 提交一条 beat。

## 5. 设计原则

1. **DSL 负责确定性，LLM 负责意义**：价格、库存、时间、债务由 Platform 算；名字、动机、秘密、反应由 LLM 写。
2. **新能力必须能 fail closed**：未知动态引用、缺失实体、错误参数都拒绝，不能静默生成 `context.*` 属性。
3. **延迟不等于死内容**：`deferred` 行为必须在条件满足时真的出现在玩家视图；测试覆盖这一点。
4. **每个生长单元都要可玩**：God 创建的不是一段描述，而是一个玩家现在或稍后能点击的行动。
5. **世界继续**：章节结局不重置世界；下一季保留客人、物品、关系和已经发生的选择。
