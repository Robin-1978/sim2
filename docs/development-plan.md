# 沙盘 Platform 开发计划

## 实施状态（2026-11-07）

`M0`–`M8` 的核心纵向切片已经落地并分阶段提交：统一 ReAct、通用配置创世、SQLite BT/Action 执行、条件/效果、探索与沟通知识、低频演化、God/WorldBuilder、玩家入口和控制/回放均有代码与测试。当前 ReAct 会话默认最多 6 次模型调用，长时 BT 不在会话内推进。剩余工作集中在并行 BT、完整目标/资源语义、记忆摘要与遗忘、势力规则演化、性能基准和完整场景验收。

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

Platform 不替 Agent 规划剧情，也不替 Agent 决定目标；它只执行世界规则、资源约束、时间推进、事务和安全边界。首个可运行切片必须包含一个 Agent、一个 LLM Decision Agent、一个 SQLite 世界、一个通用 Action、一个 BT、一次 Intent 校验和一次结果反馈。

## 模块清单

| 模块 | 目录 | 首要交付 |
|---|---|---|
| 配置与初始化 | `src/sandbox/bootstrap` | Manifest、Schema、引用校验、新 SQLite 创建、初始导入 |
| SQLite 持久化 | `src/sandbox/persistence` | Schema、Repository、事务、事件日志、状态投影、快照、备份 |
| 世界领域 | `src/sandbox/world` | Entity、组件、资源、地点、关系、社会规则和领域约束 |
| Platform 运行时 | `src/sandbox/platform` | 世界分钟、调度队列、暂停、恢复、单步和事务协调 |
| Action 与效果 | `src/sandbox/actions` | ActionDefinition、目标解析、条件引擎、成本和通用 Effect Pipeline |
| 行为树 | `src/sandbox/behavior` | BT 定义、实例、顺序、选择、并行、条件、结果和中断 |
| Intent 网关 | `src/sandbox/intents` | Intent 生命周期、权限、资源、幂等、拒绝反馈 |
| 感知与知识 | `src/sandbox/perception` | 视角过滤、局部观察、探索结果、知识事实和信息渠道 |
| 记忆与社会认知 | `src/sandbox/memory` | 情节记忆、信念、关系证据、摘要、遗忘和 FTS5 |
| AgentView 编译器 | `src/sandbox/agent_view` | 小型上下文切片、英文 Action/BT 描述、Token 预算和缓存分层 |
| LLM 与 Agent | `src/sandbox/llm`, `src/sandbox/agents` | Decision、Reflection/Evolution、God、WorldBuilder 和 ReAct |
| 控制、回放与观测 | `src/sandbox/api`, `src/sandbox/observability` | 启停、单步、查询、导出、指标、审计和确定性回放 |

LLM Agent 不直接依赖 SQLite。所有 Agent 输出经过 Intent 网关；所有状态变化由 Platform 的领域和效果模块执行。

## 里程碑

### M0：Agent 契约与最小世界

冻结 AgentView JSON、Intent JSON、工具描述、System Prompt 分层、Action/Recipe/BT 边界、错误反馈和 SQLite 最小表。完成一个 Agent 的最小世界状态和一个 LLM 可读的英语上下文。

验收：LLM 能看懂一个 Agent 当前状态、一个候选 BT、一个 Action 的条件和效果，并返回可解析的 Intent。

### M1：LLM Decision Agent 纵向闭环

实现 LLM Gateway、Decision Agent、AgentViewCompiler、工具调用、Intent Gateway 和最小 Platform Effect Pipeline。先支持观察、记忆查询、规则预览、选择 BT 和提交 Intent。

验收：一个 Agent 可以读取局部世界、选择 BT、提交 Intent、收到 Platform 结果，并根据拒绝原因继续 ReAct。已完成：`AgentToolExecutor`、原生/文本工具调用归一化、Schema 子集校验、重复调用保护、Intent 角色路由、Platform 拒绝反馈和 `llm_call` 审计；测试覆盖工具后决策、拒绝后重试和反思记忆提交。

### M2：SQLite 与配置创世

实现新实例创建、固定 `schema.sql`、配置导入、初始知识、实体、BT、Action、Recipe、事件和调度任务导入。每次初始化创建新的 SQLite 文件，已有实例 ID 拒绝覆盖。

验收：删除原始配置后实例仍可启动；两个实例互不共享数据；初始化失败整批回滚。当前已完成通用配置引用校验、manifest 文件检查、地点/实体/关系/资源/知识种子/提示词导入、配置版本与哈希记录、非法引用的创建前失败和单实例隔离；BT/Action 运行实例表和完整投影会在 M3/M4 补齐。

### M3：世界执行内核

实现世界分钟、单写入队列、世界基础分钟效果、需求更新、资源锁、事务提交、事件日志、状态投影和回放。

验收：冻结种子和冻结 LLM 响应后重复运行得到相同事件序列；暂停、继续、单步和重启恢复有效。当前已完成通用 `bt_instance`/`action_instance` 持久化、分钟推进、每分钟成本与基础效果、资源预留、动作完成/失败事件、BT 完成状态、硬/软中断接口和重启可恢复的当前游标；条件引擎、并行节点和完整效果原语在 M4 继续扩展。

### M4：通用 Action 与 BT

实现 `target_resolver`、`condition_engine`、`effect_pipeline`、成本结算、ActionInstance、BTInstance、长时执行、成功/失败、暂停/取消和硬中断。Platform 不实现剧情专用 Action 分支。

验收：采集、制作、攻击、治疗、探索和沟通使用同一套通用执行器；1,000 分钟 Action 期间 Agent 不重复决策；非法目标和资源不足事务回滚。当前已完成通用 `ConditionEngine`（布尔组合、比较、`in/not_in`、角色能力和可见路线）、`EffectPipeline`、动作参数解析、前置条件检查、每分钟效果、配方输出与能力经验入口；并行节点、完整目标解析和全部资源类型仍需继续补齐。

### M5：感知、知识和记忆驱动

实现视角过滤、局部观察、探索完成事件、沟通证词、`knowledge_fact`、`memory_item`、信念更新、摘要和 FTS5 检索。

验收：Agent 只能看到权限范围内的信息；Agent 通过工具按需检索而不是接收完整世界；探索和沟通结果可回放；同一事实可以在不同 Agent 中拥有不同置信度。当前已完成按位置/知识种子过滤的实体读取、关系摘要、知识事实表、探索结果表、对话交换表、行动结果写入知识和证词记忆、反思记忆提交、SQLite FTS5 检索、确定性摘要和有界遗忘；更细的视听渠道与置信度合并仍需扩展。

### M6：Reflection/Evolution Agent

实现日级反思、周级个体创新、记忆摘要、信念和目标更新、Capability 熟练度以及 `BehaviorTreeProposalIntent`。AgentView 只保留当前地点、直接关系、相关知识、最高驱动、最多 5 个候选和最近结果，使用简明英语描述。

验收：反思 Agent 能根据行动结果更新记忆和目标；新 BT 校验失败后能根据 Platform 的英语错误修正并重新提交。当前已完成 Reflection 角色会话、经验/信念/目标/BT 提案 Intent、Platform 注册校验、低频日/周任务表和到期事件、动作/配方能力经验入口、能力历史表、最近经验上下文和确定性记忆摘要。

### M7：God Agent 与 WorldBuilder Agent

实现周/月世界创新、危机触发器、实体创建、规则候选、势力演化，以及 LLM 生成新世界模板的分阶段校验流程。God Agent 和 WorldBuilder Agent 使用更高层的世界摘要，但仍只提交 Intent。

验收：God Agent 能提交合法的新实体或事件；非法提案不会改变世界；WorldBuilder Agent 能生成可导入的新世界模板。当前已完成 God/WorldBuilder 共用 Agent Engine、世界摘要视图、实体创建与世界变更 Intent、模板 Schema 校验、模板规范化和独立 SQLite 实例创建；更完整的规则/势力演化策略仍需扩展。

### M8：控制、观测和规模验证

实现 API、实例管理、暂停/继续、单步推进、外部事件注入、审计导出、快照恢复、指标、Prompt 缓存命中率和 Agent 成本分析。运行雾溪村完整场景，覆盖玩家进入、探索、沟通、制作、战斗、反思和 God Agent 演化。

验收：配置创世、分钟推进、LLM 驱动的 Agent 自动演化、玩家进入、知识传播、战斗中断、BT 演化、世界实体创建和确定性回放全部通过。当前已完成 transport-neutral `ControlService` 和 CLI 控制命令、本地 HTTP 控制适配器、暂停/继续/单步、外部事件注入、玩家模板入口、SQLite 一致性快照、快照恢复、审计导出、基础运行指标和相同配置/Intent 的事件序列回放测试；性能基准和完整玩家场景仍需补齐。

性能测量入口为 `PYTHONPATH=src python scripts/benchmark_platform.py --agents 1000`；它只测非 LLM 的一个模拟分钟，不把网络模型延迟混入 Platform 指标。

## 测试层次

- 领域单测：条件、目标、成本、效果、需求曲线、关系更新和伤势。
- BT 单测：顺序、选择、失败、暂停、恢复、并行和中断。
- 配置测试：Schema、引用、能力、Recipe、Action 和 BT 引用完整性。
- SQLite 集成测试：事务回滚、事件投影、备份恢复、单实例隔离。
- Agent 集成测试：固定 LLM JSON、Intent 拒绝修正、上下文切片和权限过滤。
- 回放测试：固定配置、随机种子和 LLM 响应后事件序列一致。

核心 Platform 测试使用 Mock LLM；真实模型只用于 AgentView、Prompt 和成本回归测试。
