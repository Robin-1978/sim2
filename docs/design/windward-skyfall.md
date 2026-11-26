# 风脊号：天空群岛生存沙盒

Status: **IMPLEMENTED / VERTICAL SLICE PLAYABLE**
Last updated: 2026-11-26

## 0. 一句话

> 天空在漏。你有一艘还能飞的捞船、五个各有心事的船员，和一片正在坠落的群岛。  
> 每一次出航都能带回更多材料，也会让下一场风暴更近。

《风脊号》不是又一座镇子、又一本账本。它是 **Minecraft 式探索采集 + RimWorld 式船员叙事 + 生存沙盒式基地建造**，但所有状态仍然由 Platform 结算。

## 1. 为什么现在的游戏都差不多

河湾镇、潮汐公社、长夜当铺都证明了一件事：我们可以做出漂亮的“压力 + 取舍 + 多结局”，但它们共享同一套骨架：

| 共同骨架 | 结果 |
| --- | --- |
| 一个固定地点（镇/公社/当铺） | 玩家很快看完地图 |
| 一条截止时间（7 天/风暴/21 夜） | 节奏由倒计时推着走，而不是由玩家选择推着走 |
| 一条货币/债务/声望链 | 最优解通常是“先赚钱，再还债” |
| 采集/交易/对话都是列表按钮 | 动词相同，只是换皮 |
| 世界很大，但玩家不真的“去”哪里 | 探索是读文本，不是承担风险 |

行业研究给出的答案很一致：

- **Minecraft 的核心不是剧情，是 `gather → craft → build → survive` 的复合循环**。第一次学到的动词，之后一直可用；新内容只是同一套动词面对新环境。
- **生存沙盒的基地建造是情感回报**：玩家在稀缺之后建起来的东西，是“我的”，不是任务给的。基地还必须有 mechanical reason，不能只是装饰。
- **RimWorld 的产品不是胜负，是可讲述的故事**：AI storyteller 读取殖民地状态，在玩家最疼的时候安排事件，在玩家快撑不住时松手。
- **Roguelike 的重玩性来自组合，不是内容量**：少量互相作用的系统，比大量互不相干的清单更能产生“这一局不一样”。
- **程序生成要“受约束”**：先有可读的规则和保底路径，再谈随机；随机只负责排列，不负责发明可玩性。

参考：

- [Loops and Metagames](https://www.miguelsicart.net/publications/Loops%20and%20Metagames.pdf)：Minecraft 的第一分钟就是全部游戏的最小模型。
- [Survival and Sandbox Design](https://gamedesign.gg/articles/survival-and-sandbox-game-design)：动词先于目标；基地必须是生存答案；需求必须迫使取舍。
- [How RimWorld Generates Great Stories](https://www.rockpapershotgun.com/how-rimworld-generates-great-stories)：故事来自系统交叠与玩家的 apophenia，而不是脚本。
- [Roguelike and Roguelite Design](https://gamedesign.gg/articles/roguelike-and-roguelite-design)：变化来自组合；生成要公平、可读、有保底。
- [What makes or breaks agency in roguelikes](https://thom.ee/blog/what-makes-or-breaks-agency-in-roguelikes/)：随机要发生在决策之前，而不是决策之后。
- [Minecraft Game Design: What Can We Learn?](https://gamedesignskills.com/game-design/minecraft)：玩家自定目标、系统表达、失败可讲述。

## 2. 设计支柱

1. **动词先于任务**：走、采、造、建、探、救、撤。每个动词在第一次出现时就能独立成立。
2. **基地改变动词**：工坊不是加数值，而是让“铜镐/提灯/滑翔翼”进入行动列表；厨房让“带走的食物”变成“可以多待一天”。
3. **贪心是难度滑块**：稀有物资推高 `storm_pressure`。玩家不是被日历逼着走，而是被自己的收获逼着走。
4. **船员是赌注**：五个有目标的幸存者会自己行动、争论、救人、隐瞒。玩家带走谁、留下谁，决定结局。
5. **失败是航线**：船损、失散、弃岛不会读档，会改变后续事件与结局。
6. **世界继续长**：Director 每两天推进打捞计划。God 可以在既有群岛上生成新的打捞行为，Story 读取已提交的探索、风暴和船员事件。

## 3. 世界与幻想

**裂天群岛**。天空不是空的，而是一层正在碎裂的旧世界。岛屿从“高环”上剥落，慢慢沉进云海。  
**灯岬** 是最后一处稳定锚地；你的捞船 **风脊号** 停在这里，船体漏风，工坊只剩半面墙。

玩家不是天选之子，是 **捞船船长**。你能做的最好的事，是在一座岛沉下去之前，把还值得救的东西装进船：

- 材料：木材、纤维、石、陶土、铜、煤、晶石、遗物；
- 人：五个船员，各有自己的理由；
- 秘密：旧灯塔、浮鲸墓场、雷云工坊、高环门。

### 三个阵营

| 阵营 | 想要什么 | 给玩家什么 | 结局倾向 |
| --- | --- | --- | --- |
| 风筝人 `kite_folk` | 把群岛重新系在一起 | 绳索、滑翔翼图纸、锚定技术 | 留下，修好天空 |
| 熔炉派 `foundry` | 拆掉岛屿，带走引擎 | 铜、透镜、效率 | 离开，带走能带走的 |
| 风歌者 `choir` | 听风暴，让天空重新呼吸 | 风暴知识、安抚、另一种存在方式 | 成为风暴/与天空合一 |

阵营不是“善恶”，是三种对“世界要结束了”的回答。

## 4. 循环

### 4.1 微循环（30 秒–2 分钟）

```text
选一个岛 → 出发 → 采一份资源 / 探一处遗迹 / 救一个人 → 回船
```

- 每次采集都有明确产出、时间、体力消耗和风暴代价。
- 稀有资源会推高 `storm_pressure`；普通资源不会。
- 工具会改变同一节点的产出：徒手 2 木材，石斧 5 木材。

### 4.2 中循环（20–35 分钟，一天）

```text
早晨选航线 → 出航 2–4 个动作 → 傍晚回船 → 建造/制作 → 夜话/风暴 → 睡觉
```

- 白天可以出航；夜里在野外会受暴露伤害。没有提灯时，采集/探索/远航会被关闭；带提灯可以在夜里采集，但要承担暴露代价，也可以回灯岬睡觉，或在野外等到日历规定的下一次黎明。
- 回船后可以：
  - 建造/升级模块；
  - 制作工具/食物/药；
  - 分配船员工作（通过建造模块间接影响）；
  - 听一段船员故事。
- 风暴阶段会关闭远航，逼玩家回船整备。

### 4.3 长循环（3–5 小时，一轮）

```text
第一幕：落地（修工坊、找食物、探 2 座岛）
   ↓
第二幕：上升（铜镐、滑翔翼、阵营、信标框架）
   ↓
第三幕：离天（3 个核心、风暴、三种结局）
```

没有固定天数倒计时。第 30 天云海合拢是保底终点；大多数玩家会在那之前主动选择结局。

## 5. 地图

| 地点 | 资源/功能 | 进入条件 | 作用 |
| --- | --- | --- | --- |
| 灯岬 `lantern_cape` | 营地、工坊、厨房、花园、药棚、信标 | 初始 | 家；所有建造在这里 |
| 风蚀林 `windworn_woods` | 木材、纤维、浆果 | 初始 | 教学岛；发现旧灯塔 |
| 碎盐滩 `saltbreak_flats` | 石、盐、陶土、玻璃砂 | 初始 | 第一条工具链 |
| 旧灯塔 `old_lighthouse` | 灯油、透镜、风歌者 | 探索风蚀林 | 风暴知识、提灯 |
| 铜潮谷 `copper_tide` | 铜、煤 | 石镐 | 工具 tier 2；发现浮鲸墓场/雷云工坊 |
| 雨背崖 `rainshade_cliff` | 药草、蘑菇 | 绳索 | 药棚；发现断桥群岛 |
| 浮鲸墓场 `whalefall_reach` | 兽骨、琥珀 | 提灯 + 铜镐 | 滑翔翼材料；阴森故事 |
| 雷云工坊 `thunderhead_works` | 晶石、遗物、熔炉派 | 滑翔翼 + 铜镐 | tier 3；核心材料 |
| 断桥群岛 `broken_span` | 风筝人、绳索、交易 | 滑翔翼 + 绳索 | 阵营；锚定路线 |
| 高环门 `high_ring` | 终局 | 信标框架 | 离开/锚定/成为风暴 |

地图不是一次性全开。隐藏地点通过 `explore_area` 的事实解锁；`actor.knowledge.explored:<area>` 是硬门。

## 6. 资源与工具链

### 6.1 材料

`wood` 木材、`fiber` 纤维、`stone` 石块、`clay` 陶土、`glass_sand` 玻璃砂、`salt` 盐、`copper_ore` 铜矿、`coal` 煤、`herb` 药草、`mushroom` 蘑菇、`oil` 灯油、`bone` 兽骨、`amber` 琥珀、`crystal` 晶石、`relic` 遗物、`lens` 透镜。

### 6.2 工具

| 工具 | 材料 | 解锁 |
| --- | --- | --- |
| 石斧 `stone_axe` | 木 2 + 石 2 | 木材产量 2 → 5 |
| 石镐 `stone_pick` | 木 2 + 石 2 | 采铜、采石产量提升 |
| 绳索 `rope` | 纤维 3 | 去雨背崖 |
| 铜镐 `copper_pick` | 铜 2 + 木 1（工坊 1） | 采晶石、去浮鲸墓场 |
| 提灯 `lantern` | 铜 1 + 玻璃砂 1 + 纤维 2（工坊 1） | 去浮鲸墓场、旧灯塔深处 |
| 风衣 `storm_cloak` | 纤维 4 + 陶土 2（工坊 1） | 风暴中远航，并跳过野外风暴暴露 |
| 滑翔翼 `glider` | 纤维 4 + 兽骨 2 + 油 1（工坊 2） | 去雷云工坊、断桥群岛 |
| 晶锯 `crystal_saw` | 晶石 2 + 铜 2（工坊 2） | 拆遗物 |

工具不是消耗品；拿到就是永久动词。这样玩家不会因为“工具坏了”被迫回营地刷材料，节奏保持在探索上。

### 6.3 建造树

| 模块 | 成本 | 解锁 |
| --- | --- | --- |
| 工坊 1 | 木 5 + 石 3 | 铜镐、提灯、风衣 |
| 工坊 2 | 木 6 + 铜 4 | 滑翔翼、晶锯 |
| 厨房 1 | 木 4 + 石 2 | 口粮配方；船员做饭 |
| 厨房 2 | 木 4 + 陶土 3 | 蘑菇炖菜；更好的恢复 |
| 花园 1 | 木 3 + 纤维 4 | 船员每天补口粮；营地士气 |
| 花园 2 | 纤维 6 + 药草 4 | 锚定结局条件；更多食物 |
| 药棚 1 | 木 4 + 药草 4 | 药膏；船员治疗 |
| 船体 1 | 木 6 + 铜 2 | 风暴伤害降低；修船上限 |
| 船体 2 | 铜 6 + 晶石 2 | 风暴伤害进一步降低 |
| 信标框架 | 木 4 + 铜 6 + 晶石 2 | 安装核心；去高环门 |
| 信标核心 ×3 | 晶石 2 + 遗物 1 + 琥珀 1 + 透镜 1 | 终局 |

每个模块都改变动词或生存答案，不做纯数值装饰。

## 7. 风暴：玩家自己推高的难度

`storm_pressure` 0–100：

- 每次采集的压力变化为铜 +2、琥珀 +2、透镜 +3、晶石 +4、遗物 +6。
- 每次探索新岛增加 1 点。
- 日历每天 00:00 增加 1 点。
- 建造信标框架增加 6 点，每次安装核心增加 2 点。
- 丢弃遗物 -20，丢弃琥珀 -12；
- 风歌者安抚 -8。

阈值：

| 压力 | 阶段 | 发生什么 |
| --- | --- | --- |
| 35 | 1 风暴预警 | 记录预警并唤醒 Story |
| 65 | 2 风暴命中 | 首次命中扣船体，之后持续损伤。风衣解锁远航并抵御风暴暴露 |
| 90 | 3 风暴临界 | 首次跨入临界再扣船体，持续损伤加重 |

船体归零 → 坠落结局。玩家可以用木材/铜修船，或丢掉贵重品降压力。  
这比“第 7 天商队到”更好：**玩家自己决定什么时候把世界推向危险**。

两级船体加固降低风暴伤害。压力降低后当前阶段回落，持续伤害和航线限制随之解除。首次冲击的历史标记保留，反复跨过阈值只改变当前阶段。风衣由纤维和陶土制作，普通岛屿即可提供材料。

## 8. 船员

五个初始船员，全部是 Platform Agent，有自己的需求、目标、记忆和行为：

| 船员 | 身份 | 目标 | 营地行为 |
| --- | --- | --- | --- |
| 阿岚 `alan` | 机械师 | 让风脊号重新飞起来；害怕高度 | 工坊 1 后修船 |
| 砚秋 `yanqiu` | 制图师 | 画完裂天群岛的完整海图；隐瞒一张旧图 | 记录航线、观星 |
| 老陶 `laotao` | 厨师 | 让每个人在坠落前吃上一顿热饭 | 厨房 1 后做饭 |
| 白雀 `baique` | 医士 | 把每个伤员都带回去；收集死者名字 | 药棚 1 后治疗 |
| 小满 `xiaoman` | 孩子 | 找到会说话的鸟；其实在听风暴 | 花园 1 后照料、观星 |

船员不是任务板。他们会从营地口粮箱取食、从蓄水槽取水、吃饭、喝水、休息、工作、观星；阵营角色会在自己的岛上就地补给。他们的行为会产生事件，Story 从中选出下一拍。玩家可以通过建造模块改变他们的工作，也可以通过结局选择带走谁。

阿岚和砚秋使用 Agent 决策，未配置决策提供方时回退到 BT。其余角色用确定性 BT 维持岗位与生存。升级已有存档会合并新增行为、更新包声明的 NPC 控制策略和演化配置，并保留当前进度。

## 9. 阵营与结局

### 9.1 阵营互动

- **帮风筝人**：捐纤维 → `kite_rep`，解锁锚定结局。
- **帮熔炉派**：捐铜 → `foundry_rep`，获得透镜。
- **听风歌者**：花时间 → `choir_rep`，降低风暴压力。

### 9.2 结局

| 结局 | 条件 | 情绪 |
| --- | --- | --- |
| 离天 `escape` | 信标框架 + 3 核心 + 船体 > 20% | 你带走了能带走的人 |
| 锚定 `anchor` | 信标框架 + 3 核心 + 花园 2 + 风筝人声望 40% | 你把天空重新系在一起 |
| 风暴之子 `stormborn` | 风暴压力 ≥ 90 + 2 核心 + 风歌者声望 40% | 你不再离开，也不再留下 |
| 坠落 `crash` | 船体 ≤ 0 | 风脊号沉进云海 |
| 云海合拢 `long_drift` | 第 30 天仍未选择 | 天空替你做了决定 |

没有“完美结局”。每个结局都保留一些东西，也失去一些东西。

## 10. 为什么每一局不一样

- 出身不同：机械师/制图师/厨子/拾荒者，初始工具、知识、船员关系不同。
- 探索顺序不同：先铜还是先药，决定工具链和风暴压力。
- 建造顺序不同：工坊/厨房/花园/船体抢同一批材料。
- 阿岚和砚秋的 Agent 决策可读取需求、关系和记忆。确定性 BT 维持其余角色的日常活动。
- 风暴阈值不同：玩家的贪心决定哪一天进入第二阶段。
- Director/God 会在运行时长出新的打捞点；Story 会把探索和风暴写成下一拍。
- 结局选择不同：离开、锚定、成为风暴、坠落。

## 11. 与现有世界的关系

| 世界 | 核心 | 新游戏的不同 |
| --- | --- | --- |
| 河湾镇 | 七天后商队；五家店备货 | 风脊号没有固定商队；玩家自己决定何时离天 |
| 潮汐公社 | 风暴前修潮门；公共配给 | 风脊号是个人捞船；建造改变动词，不做公共投票 |
| 长夜当铺 | 21 夜审计；收当/归还/举报 | 风脊号是探索/建造/生存；没有债务和柜台 |
| Aster Relay | 中继站电力与档案 | 风脊号是群岛冒险；船员有个人目标 |
| 风脊号 | 探索 → 采集 → 建造 → 风暴 → 离天 | 新的核心循环；基地是船，不是镇 |

## 12. 已实现范围

当前垂直切片已经可以玩：

- **10 个地点**：灯岬、风蚀林、碎盐滩、旧灯塔、铜潮谷、雨背崖、浮鲸墓场、雷云工坊、断桥群岛、高环门；
- **8 名 Agent**：5 名船员 + 3 名阵营角色；
- **19 种资源、6 个配方、61 个 Action、85 个 Behavior**；
- **5 个结局**：离天、锚定、风暴之子、坠落、云海合拢；
- **4 个出身**：机械师、制图师、厨子、拾荒者；
- 风暴压力、船体、两级船体加固、昼夜、夜间暴露、修船、弃货；
- 工坊/厨房/花园/药棚/船体/信标六条建造线；
- 新 `sky` 场景美术主题：浮岛、云海、飞艇、鸟群；
- `rules.travel.player_route_mode: authored` 让地图、文本客户端和 Platform 共用通过门槛的航线。
- PlayerView 的附近投影优先显示 Agent 和资源，营地不会再出现“附近没有人”；
- focused test：配置完整性、采集→建造→工具链、探索解锁、风暴阈值、坠落、离天结局、动态打捞点。

不在本轮：

- 真正的 3D 建造、战斗、多人；
- 物品重量/货舱容量（用时间和风暴压力替代）；
- 完整的 God 岛生成器；
- 跨周目元进度。

## 13. 活世界循环（已实现）

### 13.1 Director → God → 船员

Director 的目标是为已有的 `dynamic_salvage` Action 增加绑定。每两天检查一次，最多保留八个打捞行为。验收需要新增绑定和该行为的完成事件，完成的计划会保存 `outcome_event_id`。

God 的任务允许读取证据、提交一个 Behavior 或等待。0.3 把新增行为分配给已在断梁桥据点驻扎的阿筝。Platform 校验归属、单一打捞 Action、许可的地点和材料、两份产出与 1440 分钟冷却，也会拒绝重复的地点与材料组合。动作在开始和完成时都校验角色实际所在岛。停滞事件可以唤醒同一个受约束的任务。0.2 存档里的玩家打捞行为仍可使用。

探索只记录一条完成事件。探索、风暴、营地和结局通过世界包配置唤醒 Story。

### 13.2 0.1 版远程记录

120 分钟 `remote` 自动运行：

- Director 打开 `verb` plan；
- God 会话 **1 个 proposal、0 次拒绝**，只创建 `dyn_salvage_1`；
- `dyn_salvage_1` 使用 `dynamic_salvage`，`item: wood`，`amount: 2`，`location_id: windworn_woods`；
- 玩家在风蚀林执行它：木材 **7 → 9**，风暴压力 **0 → 4**；
- 没有出现重复 Action、display-name 物品或错误地点打捞。

180 分钟 `remote` 自动运行：

- 玩家自动搭起工坊；
- God 创建一个打捞 Behavior；
- Story 激活 `first_workshop` beat；
- 0 个 `llm_errors`，0 个 `rejected_turns`（该轮）。

### 13.3 0.2 版验证

- 确定性运行验证了两轮独立打捞计划。第一轮产物通过正常玩家入口执行，第二轮拒绝重复绑定后提交了新地点。
- 聚焦回归覆盖航线准入、异地打捞、风暴降级与 Story 唤醒、日历和等天亮、结局只结算一次、船员离线回退，以及旧存档的配置升级。
- 机械师新档从建工坊推进到雷云工坊。中途复制临时存档继续推演，通过正常行动补采铜矿、打捞遗物、修船和安装三个核心，在第 10 天触发「离天」，船体约 53%。这次推演使用确定性世界刻。

### 13.4 0.3 公共补给

每 12 小时到货三份应急铜料。营地可在 250 分钟内决定拿铜料制备补船板，或把它运往断桥的配给铜料箱。两项行动竞争整批库存，提交完成时重新检查库存和决定状态。制备的船板会在船体受损时自动投入抢修。若当轮优先补船，下一批会先偿还民生配给。逾期会降低营地士气。阿岚能在常规决策中处理它，玩家在场时也可以先行动。

## 14. 存档升级

用 `scripts/upgrade_instance.py` 将包配置应用到停服存档。升级保留玩家进度和历史事件，补齐储备实体和新增世界属性。已启动的行动按启动时保存的定义继续执行，新行动使用 0.3 版规则。

### 13.5 0.4 首航抉择

灯岬开局为第 1 天 07:00。斥候和铜料挂在两根缆绳上，绞盘只能拉一次。两个行动各耗时六个世界分钟，须在 08:00 前完成。届时缆绳崩断，照常进入探索、建造和生存循环。

- **拉回斥候**会把风筝人声望从 10% 提升到 35%。再支援断桥一次可到 51%，达到锚定群岛的 40% 声望门槛。玩家还需要完成花园和信标。
- **吊起铜料**会获得两份铜矿和一块备用铜板。船体首次低于 90% 时，阿岚会消耗铜板修复 12 个百分点。
- **继续经营**时，航队每 720 个世界分钟送来一批铜料。保船会把断桥的请求带到下一批，届时界面会说明欠款并展示支援选项。玩家可以在营地参与，NPC 也可按原配给规则自行结算。

首航与配给使用同一套世界时间和 Platform 结算。世界包通过 `setting.ui.spotlights` 选择要展示的目标。新档从 07:00 开始。升级旧档后，首航抉择只会出现在第 1 天的有效时段。

<details>
<summary>插画与生成提示词</summary>

插画由内置 ImageGen 生成并压缩为 1360×907 的 WebP。选集和角色入口使用
[风脊号封面](../../worlds/windward_skyfall/assets/dawn.webp)，登船后显示
[灯岬甲板](../../worlds/windward_skyfall/assets/lantern-cape.webp)。插画随
世界包发布，在建档和升级时导入 SQLite。

> Use case: stylized-concept. Create an original cinematic key art illustration for the world selection screen of a Chinese text adventure about navigating a sinking sky archipelago aboard an antique salvage airship. Panoramic 3:2 or wider image with a strong silhouette: on the right, a weathered bronze-and-dark-wood flying salvage cutter seen in three-quarter side view banking into a rift amid towering slate blue clouds, warm amber lanterns, tattered pennants and cables, rigging and stacked round engine housings; a pinprick amber distress lantern flickers far below near fractured floating islands. Keep the left third dark navy, quite simple and unobstructed, open atmospheric mist for real text overlaid by the website, with the ship roughly 70 percent from the left and vertically centered so a short wide crop can still show it. Dramatic blue violet sky, fine etched painterly textures, muted gold glints, and a bright thin breaking dawn behind the distant islands. Hand-painted grounded adventure concept art with readable scale, elegant cinematic lighting and rich atmosphere. No text, letters, frames, watermarks, logos, or user interface.

> Original cinematic digital concept art for a fantasy survival game, 3:2 landscape. First-person view from the weathered wooden deck of an antique salvage airship at dawn. A bronze winch and its crank sit on the right third, two taut ropes leading down from the deck edge into a vast blue-violet ocean of clouds. Far below, a tiny orange distress lantern and a hanging square cargo crate. Petals of rosy sunrise catch the ragged clouds and distant floating islands. Strong foreground perspective, weathered brass, woven ropes and dark timber. Keep the lower left quiet and dark for a title added later. Concentrate the winch around mid-height so it remains visible in a wide crop. Grounded hand-painted game environment with amber accents, restrained contrast and a sense of altitude. No text, lettering, overlays, people in the foreground, symbols, or borders.

</details>
