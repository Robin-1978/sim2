# 潮汐公社 (Tidebound Commons)

`tidebound_commons` 是 sim2 的中文海岸生存世界，也是网页游戏客户端使用的
完整内容包。它围绕一条公共目标链展开，但**不预写完整剧情**：Story 与 God
根据已经提交到事件日志的事实，持续生成下一段章节和世界结构。

居民必须在同一份时间、体力和可再生材料之间做选择：

- 每天在配给、抢修与沼泽休养之间做一次公开决策；
- 以不同出身进入时，第一件事、第一段开场和第一条专属目标都不同；
- 在风暴压力上升前测量并修复破损的潮门；
- 保留淡水和口粮，让远行和修复都还有余量；
- 勘察并补全沉没观测台的海图，找到真正的低潮通道；
- 校准季节信号，把安全航路的消息送出海岸；
- 让每一次采集都留在沼泽可以恢复的范围内。

世界包只包含声明式 YAML：地点、距离、属性、行动、配方、行为树、资源再生、
Story/God 唤醒策略和玩家模板。运行中的世界是 SQLite；后续 God 提案写入实例
定义，不会反过来修改这个包。

## 平衡目标

初始资源点：

| 资源 | 初始量 | 上限 | 再生 |
| --- | --- | ---: | --- |
| 淡水 | 24 | 30 | 每 6 小时 +8 |
| 贝类 | 28 | 36 | 每 6 小时 +10 |
| 浮木 | 20 | 28 | 每 8 小时 +6 |
| 芦苇 | 20 | 30 | 每 6 小时 +8 |

一次采集预留 2 份资源，步行到浅滩通常需要 48 分钟。配方形成一条
可读的加工链：

- **腌制海藻口粮**：贝类 2 + 淡水 1 → 口粮 3（60 分钟）
- **搓制芦苇绳**：芦苇 2 → 芦苇绳 1（30 分钟）
- **潮门修理工具包**：浮木 2 + 芦苇绳 1 → 工具包 1（75 分钟）

玩家基础模板是 3 份口粮、2 份淡水、2 份贝类和 1 份芦苇；不同出身会覆盖
对应物品与能力。工具包上限 3，防止无限囤积；口粮仍需要真实消耗输入和制作
时间。

生存阈值刻意对齐，避免“不能吃也不能干活”的夹层：

- 饮水/进食警戒线：水分 ≤ 45、饱食 ≤ 45；
- 研究、修复、采集等需要体力的行为：水分 > 40、饱食 > 40；
- 因此 41–45 区间仍可工作，0–45 区间可以进食饮水，不存在纯死区；
- 严重脱水/饥饿只在 ≤5 时造成体力伤害。

## 居民

| 居民 | 身份 | 强项 | 张力 |
| --- | --- | --- | --- |
| 玛拉 | 潮务官 | 公共维护、修复 | 必须把稀缺工具包用在公共潮门上 |
| 伊沃 | 沼泽采集者 | 采集、腌制 | 采得太勤会拖慢修复 |
| 塞琳 | 海图保管员 | 研究、勘察 | 远行需要口粮和净水 |
| 塔罗 | 渡船信使 | 交涉、探索 | 能传播消息，但不能独自证明 |
| 妮娅 | 码头帮工 (`bt`) | 浮木、芦苇、贝类 | 后台行为树，固定优先级 |
| 伦 | 码头帮工 (`bt`) | 贝类、淡水 | 后台行为树，固定优先级 |

妮娅和伦使用 `controller: bt`，按 `bt_priority` 和需求紧急度工作，不消耗
模型调用；四名决策角色使用 LLM，拥有记忆、反思和社交回应能力。

## 公开目标链

`rules.progression` 声明六个公开阶段，玩家和配置的受众都能看到：

1. 测量潮门
2. 修复潮门（先备齐浮木、芦苇绳和工具包）
3. 让潮门撑过第一场风暴（把完整性修到 80% 以上）
4. 补全沉没海图（两个不同研究阶段）
5. 校准季节信号（潮门完整性阈值 + 工具包）
6. 重开外海航路（公共信号阈值）

这些阶段只声明目标、目标行为和完成事件，不直接发放资源或跳过前置条件。
Story 负责把已验证结果变成章节；God 负责在证据足够时增加可达的新结构。

玩家还会看到三类非公共阶段：

- **每日公共决策**：每天只开一次，目标卡直接给出三个可执行选项；
- **出身第一步**：由 `rules.progression.origins` 限定，只对匹配出身的玩家显示；
- **出身第二幕**：通过 `requires_goal` 等第一步完成后才出现。

目标卡支持 `visible_when`（例如 `world.public_decision_open >= 1`）、`origins`
（例如 `[forager]`）和 `requires_goal`（例如 `forager_first_haul`）。这些字段
都是通用投影能力，其他世界也可以做条件目标、出身目标和阶段式目标。

## 出身开场

四个出身不再只差属性和物品。`player_template.yaml` 的每个出身可以声明：

```yaml
opening:
  event_type: origin.forager
  summary: 你以沼泽采集者的身份加入公社……
knowledge_seed:
  - 老采集者叮嘱：贝滩每采一轮要留一半。
```

玩家创建时，Platform 把 `origin_id` 写进角色，把出身知识写入记忆，并发布
一次 `origin.<id>` 事件。入口卡会显示“第一件事”，进入后世界动态会保留这条
开场；`rules.progression.origins` 再给该出身一条专属第一步目标。
专属目标的 `complete_when.self_only: true` 只统计该玩家自己的事件，因此其他
居民的采集或对话不会替玩家完成第一步。

| 出身 | 第一件事 | 专属目标 |
| --- | --- | --- |
| 潮务学徒 | 先测量潮门，再决定材料 | 搓出第一段公共用绳 |
| 沼泽采集者 | 先看清哪片浅滩不该再动 | 带回第一篓贝类 |
| 海图学徒 | 先验证第一个研究阶段 | 验证海图的第一个阶段 |
| 渡船信使 | 先找一位居民交换消息 | 把倒计时送到第一位住户 |

## 每日公共决策

世界开局就有一个打开的决策窗口（`public_decision_open: 1`），之后每天
07:00 重新打开。玩家在三个行动中只能选一个，选完当天窗口关闭：

| 决策 | 收益 | 代价 |
| --- | --- | --- |
| 收紧配给 | 淡水 `+6`（上限 30） | 公共信心 `-5%` |
| 优先抢修 | 潮门完整性 `+5%` | 淡水 `-4` |
| 沼泽休养 | 公共信心 `+5%`、归属 `+4` | 潮门完整性 `-2%` |

决策由三种通用能力组成：

- `world_resource_delta` 直接移动共享资源库存（不是随身物品，也不伪造
  `resource_transfer` 的预留数量）；
- `world_property_delta` 调整世界数值并关闭窗口；
- `public_event` 支持 `summary` 和 `data`，因此决策会留下可读的世界动态。

`public_decision_open` 使用 `hidden: true`，不出现在公共数值面板里；目标卡
通过 `visible_when` 只在窗口打开时出现。每天重开由 `every_minutes: 1440`
的世界作用域分钟效果负责，事件为 `public_decision.due`。

每个决策还会写入一个隐藏的“昨日决策”标记。第二天同一时刻，世界作用域
分钟效果会读取该标记并应用后果：配给带来淡水余量和抱怨，抢修带来一点闸门
加固，休养让贝类和芦苇恢复。后果事件是 `decision.aftermath`，随后清除标记。
这些仍然是 YAML 规则，不依赖 Story 或 God 是否可用。

## 上手提示

`rules.onboarding` 是配置驱动的第一轮提示，不是固定任务链。每一步声明：

```yaml
- id: first_measurement
  title: 先测量潮门
  body: ...
  when: world.maintenance_evidence < 1
  action_ref: behavior:inspect_gate
  goal_id: inspect_tide_gate
```

玩家视图只投影**第一个条件满足**的步骤；条件全部不满足时提示自动消失。
`onboarding_window_minutes: 2880` 让上手提示只覆盖开局两天，之后完全交给
Story/God 和目标系统。网页把它显示为“上手提示”，而不是章节或剧情。步骤可以
引用任意 `world.*` 属性，也可以只给目标 id，让玩家在目标卡里做选择。

## 风暴倒计时

`storm_eta_hours` 是一个可见的世界属性，开局为 18 小时，之后每 48 小时一个
风暴周期。世界每分钟扣减倒计时，归零时按潮门完整性结算：

| 潮门完整性 | 结果 | 事件 | 后果 |
| --- | --- | --- | --- |
| `< 20%` | 风暴潮灌入码头 | `storm.flooded` | 公共信心 `-22%` |
| `20%–79%` | 潮门被撕开 | `storm.breached` | 潮门完整性 `-16%` |
| `>= 80%` | 潮门扛住风暴 | `storm.held` | 公共信心 `+5%` |

结算后风暴压力回落、倒计时重置为 48 小时。这样“修一次”和“修到八成”是
两个不同的决定：前者只能让公社勉强撑过，后者才能把风暴变成公共信誉的
来源。倒计时归零不会删除角色或强制结束游戏；它降低公共设施和信心，把
世界推进到需要修复的下一段。

### 风暴后果是空间和材料，不只是数字

风暴规则使用世界作用域的 `effects` 列表，所有后果都在 YAML 里：

- `storm.flooded`：码头 `open: false`，淡水 `-6`、浮木 `-8`；
- `storm.breached`：盐沼 `open: false`，贝类 `-10`、芦苇 `-8`；
- `storm.held`：重新打开码头和盐沼；
- `repair_tide_gate`：修复成功后重新打开两处地点。

`location_is_open` 是通用拓扑规则：地点可以声明 `initial_state.open: false` 或
`initial_state.closed: true`；不可进入的地点会从可达路线、玩家路线卡和旅行动作
中消失，地图节点显示为封闭。Platform 不知道“码头”或“盐沼”是什么，这些地点
和行为全部来自世界包。

实现放在世界作用域的 `minute_effects` 中，使用通用能力：

- `condition` 读取 `world.*` 属性；
- 条件满足时应用 `world_property_delta`；
- `event_type` / `event_summary` 每条规则每分钟最多发布一次公共事件；
- 结算和重置规则按 YAML 顺序执行，因此先结算后果，再重置倒计时。

## 零 LLM 态势锚点，而不是固定剧情

`story.yaml` 的 `situation_anchors` 不是剧情分支，也不预写章节。它把一段
**只读**的环境旁白绑定到已经验证的公共事件，只在没有模型驱动的 Story 章节时
兜底显示：

| 触发事件 | 态势标题 |
| --- | --- |
| `storm.watch_begun` | 风暴季警戒 |
| `tide_gate.ready` | 潮门已加固 |
| `storm.held` | 潮门守住了 |
| `storm.breached` | 潮门被撕开 |
| `storm.flooded` | 潮水进了码头 |

锚点只在开局窗口内有效：`situation_anchor_window_minutes: 2880`（两天）。窗口
结束后，`chapter` 只投影 Story 生成的 `story.beat.activated`；如果 Story 还没
生成新内容，页面退回“世界目标”，不会再用固定旁白冒充后续剧情。

优先级是：**玩家可读的 `story.beat.activated` 永远优先**；没有 Story 章节时
才投影态势锚点。锚点不推进状态、不发放资源、不解锁行为、不阻止目标，也不影响
Story/God 的自动演化。网页会按来源显示“自动演化”或“当前态势”，不会把兜底
旁白伪装成固定章节。

## 开场之后交给 Agent

固定内容只覆盖开场：`situation_anchor_window_minutes: 2880` 和
`onboarding_window_minutes: 2880` 都在两天后失效。之后的章节、地点、势力、
规则和 NPC 目标由 Story/God 根据已验证事件继续生成。

`decision.aftermath` 也被列为 Story/God 的 external 唤醒源：每天决策的后果
会进入自动演化，而不是只改一个数值。God 仍然可以通过通用提案增加新地点、
势力、规则和可执行行为；Story 仍然可以提出 emergent 章节。态势锚点不会阻止、
覆盖或替代这些结果。

## 自动演化，而不是固定剧情

这个包**没有预写 `story.beats`**。`story.yaml` 只声明哪些事件可以作为
emergent 来源；真正的章节由 Story Agent 在事件唤醒时提出（零模型时的态势锚点
`situation_anchors` 见上一节）。当前策略：

- Story 在 `gate.inspected`、`research.completed`、`tide_gate.repaired`、
  `signal.tuned`、`signal.route_open`、`storm.warning`、`storm.breached`、
  `storm.held`、`storm.flooded`、资源收获和对话后被唤醒；`count`、`every`、
  `distinct_by` 抑制同一次收成被反复讲述。风暴结算事件由世界作用域分钟效果
  产生，因此在 evolution 中声明为 external producer。
- God 在公共工程、资源压力和 `world.stagnation` 时被唤醒，并按
  `cadence.god: 3d`、`cadence.god_structural: 7d` 周期复查。
  `god_structural` 不是泛泛的“看一看”，而是一份 `contracts.god_structural`
  契约：必须提交一个非研究 Action 和绑定行为树，分配给玛拉，产生新的结果
  事件；研究源不能替代它。
- `stagnation.growth: structural`：每 1 天比较行为、可达地点、势力和规则，
  外加持久化的世界结构签名。新增研究源只是给已有 `study_source` 增加一个
  参数值，不会重置结构停滞；只有真正新增地点/动词/势力/规则才会。因此研究
  跑步机不会再把结构审查无限推迟。
- `world.location.created` 和 `world.resource.created` 会分别唤醒
  `god_after_place`、`god_after_resource`：新地点补一个可执行活动，新资源补
  一个真正改变它的行为。新资源用 `world_resource_delta` 改变，不能由 God
  直接灌库存。这样动态地点和库存不会变成“死内容”。
- 自动运行和玩家手动同步推进都会执行这些 Story/God 任务；不要把演化源标成
  `manual_only`，除非确实需要管理员触发。
- 演化任务使用玩家在网页设置里选择的 provider。若默认 `local` 模型失败，
  NPC 决策和演化都会停摆，因此游戏客户端默认优先选择可用的 `remote`。

一次真实检查中，玛拉完成 `inspect_gate` 后，Story 在 90 分钟自动生成了新的
`emergent` 章节，并推动她先打捞浮木、再与塔罗交换情报；God 周期任务和
stagnation 也在同一套配置下运行。

## 生存与目录语义

行为目录把“准备”和“恢复”分开：

- `drive_tags` 表示行为本身立即改变的需求；
- `supports_drive_tags` 表示产物以后可以帮助的需求；
- 采贝类不会恢复饱食，只有吃口粮才会；
- 腌制口粮是制作行为，不是进食行为；
- 没有口粮的饥饿居民仍可采贝类，因为这是恢复链的第一步；
- 沼泽采集要求水分 > 40，避免脱水居民硬走远路。

Platform 只为当前可执行的行为生成行动卡；其余行为以
`unavailable_actions` 暴露失败谓词，网页会把它翻译成可读原因。

## 验证

```bash
sim2 validate-world --config worlds/tidebound_commons
sim2 describe-world --config worlds/tidebound_commons
sim2 init --config worlds/tidebound_commons --instance-id tidebound-demo
```

真实模型因果审计：

```bash
python scripts/audit_world_evolution.py \
  --config worlds/tidebound_commons \
  --provider remote \
  --minutes 1440 \
  --round-minutes 360 \
  --runtime-dir /tmp/sim2-tidebound-audit \
  --instance-id tidebound-audit
```

重点回归测试覆盖：包验证、地点/资源/配方数量、资源再生上限、公开目标投影、
`story.beats == []`、Story/God 自动唤醒、God cadence 与 stagnation 配置，以及
一次真实 `inspect_gate` 后自动生成 ready Story 任务。

## 地图扩展

六个初始地点只是起点。两个研究阶段完成后，God 可以创建一个新的外部地点，
再用通用 route 变化建立带权重的双向路线。新地点只是“可导航的可能”；只有
现有居民获得可执行的探索行为并真的走完旅程，它才进入实际玩法。Story 可以
描述这次扩展，但不能声称旅程已经发生。

如果证据不足，God 应选择 `wait`，而不是凭空生成战争、灾难或已完成的结果。
扩展的代价、可达性和可执行性仍由 Platform 校验。
