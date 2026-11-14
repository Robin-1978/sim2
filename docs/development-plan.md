# 沙盘 Platform 开发计划

## 实施状态（2026-11-07）

`M0`–`M8` 的计划内容已经落地并分阶段提交：统一 ReAct、JSON Schema 工具调用、原生 assistant/tool/result 消息、通用配置创世、SQLite BT/Action 执行、条件/目标/资源/效果、探索与沟通知识、低频演化、God/WorldBuilder、玩家入口、控制/回放和规模基准均有代码与测试。当前 ReAct 会话默认最多 6 次模型调用，长时 BT 不在会话内推进；每个 Agent 唤醒都重新构建 AgentView，只有当前会话保留互动消息。Tick/Commit、Activity、Outcome/Reflection、World Evolution 四份协议已固化在 [runtime-protocols.md](design/runtime-protocols.md)，并有修订冲突、定义快照、提案匹配和记忆来源回归测试。

下一阶段不再横向增加功能，进入架构收敛阶段。具体拆分顺序、删除的旁路和验收条件见 [架构收敛计划](design/architecture-convergence-plan.md)。

## 开发目标

首版交付一个以 LLM Agent 为演化核心的沙盘 Platform。Agent 负责目标、选择、探索、沟通、学习、BT 生成和世界创新；Platform 负责把 Intent 安全地转换成状态变化。系统从配置包创建新世界，使用单实例 SQLite 运行，按世界分钟推进长时 BT，并持久化探索和知识。第一验收世界为 `worlds/starter_village/`。

## 核心闭环

```text
SQLite 世界状态
  → AgentViewCompiler 生成当前视角
  → LLM Agent 观察、推理、检索和选择
  → Intent JSON
  → Platform 校验、执行和写入事件
  → Action/BT 结果、知识和记忆
  → 下一次 Agent 上下文
```

Platform 不替 Agent 规划剧情，也不替 Agent 决定目标；它只执行世界规则、资源约束、时间推进、事务和安全边界。首个可运行切片必须包含一个个体 Agent 的决策阶段、一个 SQLite 世界、一个通用 Action、一个 BT、一次 Intent 校验和一次结果反馈。

当前新增的演化制度路径遵循：人物提议 → 世界审核 → SQLite 注册制度 → 未来验证事件产生账本记录。制度、账本和人物属性都不是 Platform 的新手村分支；它们由世界配置或已接受的声明式数据定义。实现边界和剩余属性清理项见 [动态世界 Schema](design/dynamic-world-schema.md)。

## 模块清单

| 模块 | 目录 | 首要交付 |
|---|---|---|
| 配置与初始化 | `src/sim2/bootstrap` | Manifest、Schema、引用校验、新 SQLite 创建、初始导入 |
| SQLite 持久化 | `src/sim2/persistence` | Schema、Repository、事务、事件日志、状态投影、快照、备份 |
| 世界领域 | `src/sim2/world` | Entity、组件、资源、地点、关系、社会规则和领域约束 |
| Platform 运行时 | `src/sim2/platform` | `runtime.py` 协调时间和事务，`activity.py` 推进长时活动，`learning.py` 处理经验与进度，`social.py` 处理制度与互动，`definition_runtime.py`、`entity_proposals.py`、`story_runtime.py`、`world_changes.py` 分别校验定义、提交实体/Story/世界变更提案；`src/sim2/evolution` 只负责唤醒、Story 资格和提案评估策略 |
| 演化调度 | `src/sim2/evolution` | 通用事件消费者唤醒、累计证据门控和世界提案结果评估 |
| Action 与效果 | `src/sim2/actions` | ActionDefinition、目标解析、条件引擎、成本和通用 Effect Pipeline |
| 行为树 | `src/sim2/behavior` | BT 定义、实例、顺序、选择、并行、条件、结果和中断 |
| Intent 网关 | `src/sim2/intents` | Intent 生命周期、权限、资源、幂等、拒绝反馈 |
| 感知与知识 | `src/sim2/perception` | 视角过滤、局部观察、探索结果、知识事实和信息渠道 |
| 记忆与社会认知 | `src/sim2/memory` | 情节记忆、信念、关系证据、摘要、遗忘和 FTS5 |
| AgentView 编译器 | `src/sim2/agent_view` | 小型上下文切片、英文 Action/BT 描述、Token 预算和缓存分层 |
| LLM 与 Agent | `src/sim2/llm`, `src/sim2/agents` | Decision、Reflection/Evolution、God、WorldBuilder 和 ReAct |
| 控制、回放与观测 | `src/sim2/api`, `src/sim2/observability` | 启停、单步、查询、导出、指标、审计和确定性回放 |

LLM Agent 不直接依赖 SQLite。所有 Agent 输出经过 Intent 网关；所有状态变化由 Platform 的领域和效果模块执行。

## 里程碑

### M0：Agent 契约与最小世界

冻结 AgentView JSON、Intent JSON、工具描述、System Prompt 分层、Action/Recipe/BT 边界、错误反馈和 SQLite 最小表。完成一个 Agent 的最小世界状态和一个 LLM 可读的英语上下文。

验收：LLM 能看懂一个 Agent 当前状态、一个候选 BT、一个 Action 的条件和效果，并返回可解析的 Intent。

### M1：LLM Decision Agent 纵向闭环

实现 LLM Gateway、Decision Agent、AgentViewCompiler、工具调用、Intent Gateway 和最小 Platform Effect Pipeline。先支持观察、记忆查询、规则预览、选择 BT 和提交 Intent。

验收：一个 Agent 可以读取局部世界、选择 BT、提交 Intent、收到 Platform 结果，并根据拒绝原因继续 ReAct。已完成：`AgentToolExecutor`、角色 JSON Schema、原生/文本工具调用归一化、原生消息历史、Schema 校验、重复调用保护、Intent 角色路由、Platform 拒绝反馈、AgentView 契约验证和 `llm_call` 审计；测试覆盖工具后决策、原生 tool result 消息、拒绝后重试和反思记忆提交。Decision 只接受已验证的 `executable_behaviors`，无可执行工作时使用已验证的 fallback BT 或 `wait`。

### M2：SQLite 与配置创世

实现新实例创建、固定 `schema.sql`、配置导入、初始知识、实体、BT、Action、Recipe、事件和调度任务导入。每次初始化创建新的 SQLite 文件，已有实例 ID 拒绝覆盖。

验收：删除原始配置后实例仍可启动；两个实例互不共享数据；初始化失败整批回滚。已完成通用配置引用校验、manifest 文件检查、地点/实体/关系/资源/知识种子/提示词导入、配置版本与哈希记录、非法引用的创建前失败、调度任务导入和单实例隔离；BT/Action 运行实例由后续阶段写入同一 SQLite。

### M3：世界执行内核

实现世界分钟、单写入队列、世界基础分钟效果、需求更新、资源锁、事务提交、事件日志、状态投影和回放。

验收：冻结种子和冻结 LLM 响应后重复运行得到相同事件序列；暂停、继续、单步和重启恢复有效。已完成通用 `bt_instance`/`action_instance` 持久化、分钟推进、每分钟成本与基础效果、资源预留、动作完成/失败事件、BT 完成状态、硬/软中断接口、暂停恢复和重启可恢复的当前游标；条件、目标、效果和并行执行在 M4 接入。

### M4：通用 Action 与 BT

实现 `target_resolver`、`condition_engine`、`effect_pipeline`、成本结算、ActionInstance、BTInstance、长时执行、成功/失败、暂停/取消和硬中断。Platform 不实现剧情专用 Action 分支。

验收：采集、制作、攻击、治疗、探索和沟通使用同一套通用执行器；1,000 分钟 Action 期间 Agent 不重复决策；非法目标和资源不足事务回滚。当前已完成通用 `ConditionEngine`（布尔组合、比较、`in/not_in`、角色能力和可见路线）、`EffectPipeline`（安全公式）、`TargetResolver`、动作参数解析、前置条件检查、每分钟效果、配方输出与能力经验入口、并行 BT ActionInstance、暂停恢复和 Agent/共享世界资源账本；资源预留与状态更新在同一 SQLite 事务中完成。

### M5：感知、知识和记忆驱动

实现视角过滤、局部观察、探索完成事件、沟通证词、`knowledge_fact`、`memory_item`、信念更新、摘要和 FTS5 检索。

验收：Agent 只能看到权限范围内的信息；Agent 通过工具按需检索而不是接收完整世界；探索和沟通结果可回放；同一事实可以在不同 Agent 中拥有不同置信度。当前已完成按位置/知识种子过滤的实体读取、`listen`/公开记录/已知事实查询、关系摘要、知识事实表、探索结果表、对话交换表、行动结果写入知识和证词记忆、反思记忆提交、SQLite FTS5 检索、来源置信度合并、确定性摘要和有界遗忘。

### M6：Reflection/Evolution Agent

实现日级反思、周级个体创新、记忆摘要、信念和目标更新、Capability 熟练度以及 `BehaviorTreeProposalIntent`。AgentView 只保留当前地点、直接关系、相关知识、最高驱动、最多 5 个候选和最近结果，使用简明英语描述。

验收：反思 Agent 能根据行动结果更新记忆和目标；新 BT 校验失败后能根据 Platform 的英语错误修正并重新提交。当前已完成 Reflection 角色会话、经验/信念/目标/BT 提案 Intent、Platform 注册校验、低频日/周任务表和到期事件、动作/配方能力经验入口、能力历史表、最近经验上下文和确定性记忆摘要。

### M7：God Agent 与 WorldBuilder Agent

实现周/月世界创新、危机触发器、实体创建、规则候选、势力演化，以及 LLM 生成新世界模板的分阶段校验流程。God Agent 和 WorldBuilder Agent 使用更高层的世界摘要，但仍只提交 Intent。

验收：God Agent 能提交合法的新实体或事件；非法提案不会改变世界；WorldBuilder Agent 能生成可导入的新世界模板。当前已完成 God/WorldBuilder 共用 Agent Engine、世界摘要视图、实体创建与世界属性/规则/关系/势力策略/事件变更 Intent、模板 Schema 校验、模板规范化和独立 SQLite 实例创建、配置危机触发器到 God/Reflection 任务的调度。

### M8：控制、观测和规模验证

实现 API、实例管理、暂停/继续、单步推进、外部事件注入、审计导出、快照恢复、指标、Prompt 缓存命中率和 Agent 成本分析。运行雾溪村完整场景，覆盖玩家进入、探索、沟通、制作、战斗、反思和 God Agent 演化。

验收：配置创世、分钟推进、LLM 驱动的 Agent 自动演化、玩家进入、知识传播、战斗中断、BT 演化、世界实体创建和确定性回放全部通过。当前已完成 transport-neutral `ControlService` 和 CLI 控制命令、本地 HTTP 控制适配器、暂停/继续/单步、外部事件注入、玩家模板入口、SQLite 一致性快照、快照恢复、审计导出、基础运行指标、相同配置/Intent 的事件序列回放、Starter Village 端到端场景和 1000 Agent 五次稳态模拟分钟 p95 基准（最近一次为 159.073ms）。基准单独报告第一个初始化分钟；分钟效果会按角色合并并批量写入，阈值状态每分钟只持久化一次。

性能测量入口为 `python scripts/benchmark_platform.py --agents 1000`；它只测非 LLM 的一个模拟分钟，不把网络模型延迟混入 Platform 指标。

## 测试层次

- 领域单测：条件、目标、成本、效果、需求曲线、关系更新和伤势。
- BT 单测：顺序、选择、失败、暂停、恢复、并行和中断。
- 配置测试：Schema、引用、能力、Recipe、Action 和 BT 引用完整性。
- SQLite 集成测试：事务回滚、事件投影、备份恢复、单实例隔离。
- Agent 集成测试：固定 LLM JSON、Intent 拒绝修正、上下文切片和权限过滤。
- 回放测试：固定配置、随机种子和 LLM 响应后事件序列一致。

核心 Platform 测试使用 Mock LLM；真实模型只用于 AgentView、Prompt 和成本回归测试。

## 验收证据

在当前提交上执行：

```bash
pytest -q
python -m compileall -q src scripts
python scripts/benchmark_platform.py --agents 1000 --runs 5
python scripts/verify_agent_models.py
```

最近一次结果为 `219 passed`；测试覆盖 ReAct 工具反馈与拒绝重试、SQLite 创世与回滚、长 Action/并行 BT/共享资源、中断恢复、探索沟通知识、聊天与两阶段交易、文本及 TUI 玩家/旁观者入口、地图可见性、短期/长期记忆与任务条件召回、BGE 适配、嵌套 BT 多参与者目标校验、周/月 God 趋势、反思目标和能力历史、执行 outcome 复盘上下文、God 世界前后值复盘、God 事件语义质量门、HTTP 控制、健康探针、快照恢复、确定性回放、可读名称映射、重复 BT 提案质量门、Provider 短暂断连重试与限流 cooldown、Tick 快照与提交屏障、活动定义版本快照、提案按类型结果匹配、记忆事件来源校验、文明进度证据归属与唯一计数、对象与研究来源的空间可达性、研究事实门控、事件组准入与冷却、报警响应闭环、Story 来源范围一致性以及 Starter Village 端到端场景。

`verify_agent_models.py` 是真实模型验收入口：它不打印原始 reasoning 或密钥，只报告 Decision/Reflection/God 的会话步骤、最终 Intent、Platform 校验结果和质量布尔值；远程 Provider 使用 `~/.sim2/llm.json` 中的显式代理和环境变量引用。

上下文缓存指标同时记录 Provider 返回的 `cached_tokens/prompt_tokens` 和结构化 Prompt 的稳定前缀复用率。最近的确定性周跑没有 Provider token usage，但 1,932 次调用的稳定前缀复用率为 `99%` 以上；稳定协议和工具定义位于动态世界状态之前，说明缓存边界保持稳定。

`python scripts/run_long_simulation.py --minutes 10080` 的当前一周确定性闭环完成 1,810 次决策、72 次反思、1 次 God 周期会话、3 次 Story 会话和 1,932 次 provider 调用；选出 17 种行为、产生 20 种事件类型，世界分钟完整推进到 10,080，未触发单一行为支配、健康耗尽、拒绝风暴或无限重规划标记。该 smoke provider 的反思固定返回 `wait`，所以不会伪造记忆增长；真实模型验收由 `verify_core_interaction.py` 和 `verify_agent_models.py` 完成。

真实 DeepSeek `deepseek-v4.1-flash` 核心闭环已通过：Decision、Reflection、下一次 Decision 和 God 的语义质量均为通过，Platform 结果分别为有效行为、有效经验或等待、有效下一步行为和有效世界提案。使用 `/disk` 中的 `bge-small-zh-v1.5` ONNX 缓存和 PyTorch 环境运行时，记忆 backend 为 `embedding_cosine_onnx`，同一闭环仍全部通过；未安装模型时自动回退到词项检索。BGE 由 `onnxruntime` 和 `tokenizers` 批量编码，记忆标签只用于召回和重排，不进入模型可见的内部 ID 字段。

真实模型验证使用配置中的远程代理和 `MY_MY_API` 环境变量，不把密钥写入仓库或输出。God 上下文包含当前资源、周/月资源净变化、关系变化和目标标签趋势；只记录叙事 event、没有结构化状态效果的提案会被质量门标为 `accepted_needs_world_followup`。

最新本地协议回归在可读名称 schema 上通过：Decision 的 `request_behavior` 使用 `choice: "Explore Greywood"`，质量为 `comparative_grounded_choice`；Reflection 的短经验提交质量为 `grounded`。一次冗长经验会被 Platform 的 500 字符限制拒绝，重复的等价 BT 也会被 Platform 拒绝，随后 Agent 可以提交有效信念或等待。

多 Agent 审查脚本还验证了 `liang_he` 的 3 个候选：本地模型选择 `behavior:craft_meal`，理由与 food/winter 目标及 safety/belonging 驱动标签一致，质量等级为 `comparative_grounded_choice`；`lin_bo` 的唯一治理候选为 `grounded_forced_choice`。完整 Prompt、原生 tool call、Platform 结果和质量检查保存在被 `.gitignore` 排除的 `artifacts/agent-session-review.json`。

多参与者审查覆盖顶层和嵌套 BT：当前可见参与者超过一个时，AgentView 要求使用可读目标名称，Platform 对遗漏、歧义和隐藏目标统一拒绝；单一可见参与者仍可由 Platform 安全推断。God 趋势测试同时验证了周窗口和月窗口的边界，世界属性和资源名称在 Prompt 中显示为可读文本。
