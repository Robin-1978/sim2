# 行业资料与设计取舍

本页记录本项目外部核查过的论文、引擎文档和工程文章，以及它们对设计的实际影响。资料用于形成设计依据，不替代本项目的 Platform 校验和数据模型。

## Agent 架构与记忆

- [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629)，Yao 等，2022/2023。结论：推理、工具调用和环境反馈应交替进行，工具结果用于修正计划。本项目采用有限轮次 ReAct，LLM 只提交 Intent，Platform 负责副作用和错误反馈。
- [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)，Park 等，2023。结论：观察、记忆检索、反思和计划是不同阶段；记忆和反思对可观察的社会行为有贡献。本项目把知识事实、情节记忆、反思和计划分开持久化，并采用日级反思和事件触发重规划。
- [MemoryBank: Enhancing Large Language Models with Long-Term Memory](https://arxiv.org/abs/2305.10250)，Zhong 等，2023。结论：长期记忆需要写入、检索、更新和遗忘策略；时间只是记忆可用性的一个因素，重要性和重复访问会改变保留价值。本项目不按时间自动删除经验，而是记录重要性、来源和激活次数，把相似检查作为反思阶段的质量提示。
- [Voyager: An Open-Ended Embodied Agent with Large Language Models](https://arxiv.org/abs/2305.16291)，Wang 等，2023。结论：持续探索需要可检索的技能库、环境反馈、执行错误和自我验证。本项目采用版本化 BT 与元动作注册表，错误通过 `IntentValidationError` 回传；技能只能是 Platform 允许的声明式能力，不执行任意生成代码。
- [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560)，Packer 等，2023。结论：有限上下文需要分层记忆、按需换入和中断控制，而不是把全部历史塞进一次调用。本项目把当前场景、相关知识、短期记忆和长期摘要分层，并用 Platform 触发反思和检索。
- [Route to Remember: Adaptive Memory Selection for LLM Agents](https://arxiv.org/search/?query=%22Route+to+Remember%22&searchtype=all)，2025。结论：记忆选择应随当前任务改变，固定的全量历史或单一时间衰减都不可靠。本项目只把任务相关的少量记忆放入 AgentView，结合激活次数、重要性、来源可信度和语义相似度排序。
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)，Anthropic Engineering，2025。结论：上下文是有限的注意力资源；应使用最小高信号集合、刚需检索、工具结果压缩、结构化笔记和渐进式披露。项目因此禁止把整个世界 JSON 传给 Agent，使用角色视角、局部观察、相关关系、少量候选行为和按需查询。
- [Building effective agents](https://www.anthropic.com/research/building-effective-agents)，Anthropic，2024。结论：先使用简单、可组合的 Workflow，只有需要动态决策时才引入 Agent；工具接口应清晰且易于调试。本项目采用模块化单体、确定性分钟推进 和长时 BT，把 LLM 限制在计划边界、反思窗口和世界创新窗口。
- [Function calling guide](https://platform.openai.com/docs/guides/function-calling)，OpenAI Platform 文档（通过项目网络代理核查，2026-11-07）。结论：工具定义使用结构化参数 Schema；模型请求工具后，应用执行工具并把结果作为下一条消息返回；应用必须决定工具是否真正执行。本项目把工具分成只读查询和 Intent 提交两类，统一由 `AgentToolExecutor` 校验参数并调用 Platform。
- [Implement tool use](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/implement-tool-use)，Anthropic API 文档（通过项目网络代理核查，2026-11-07）。结论：工具调用、工具结果和错误应保持明确的消息边界；工具错误要作为可恢复反馈返回模型，不能把异常当成成功结果。本项目保留每轮 ReAct transcript，使用 `retryable`、`failed_check` 和短错误消息继续会话。
- [Using tools](https://developers.openai.com/api/docs/guides/tools)，OpenAI API 文档（通过项目网络代理核查，2026-11-08）。结论：工具使用独立的 JSON Schema/`tools` 契约，应用执行工具后必须把结果作为后续消息提供给模型；工具定义不应依赖自然语言猜测。本项目用 `tool_schemas_for_role` 生成 JSON Schema，Prompt 只显示简短参数说明。
- [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)，OpenAI API 文档（通过项目网络代理核查，2026-11-08）。结论：一次交互要保留完整的 assistant/tool/result 消息序列；会话外的状态应通过持久化摘要或新的上下文重新构建。本项目 `AgentEngine` 每次唤醒新建消息列表，只有当前 ReAct 循环追加原生消息。
- [A generalist AI agent for 3D virtual environments (SIMA)](https://deepmind.google/discover/blog/sima-generalist-ai-agent-for-3d-virtual-environments/)，Google DeepMind，2024。结论：游戏 Agent 需要把高层语言目标映射为可执行环境动作，并通过环境反馈闭环；本项目让 LLM 选择已注册 BT，Platform 执行原子 Action 并回传结果。
- [Scaling Instructable Agents Across Many Simulated Worlds](https://arxiv.org/abs/2404.10179)，2024。结论：跨世界 Agent 需要环境接口、可迁移技能和明确反馈，不能把世界规则写进单个角色 Prompt；本项目把世界规则和 BT 保存在实例 SQLite/配置定义中。
- [Behavior Tree in Unreal Engine 5.8](https://dev.epicgames.com/documentation/en-us/unreal-engine/behavior-tree-in-unreal-engine---overview)，Epic Games 文档（2026-11-08）。结论：Blackboard、行为树和事件中断分层，长任务由执行器推进；本项目把 BT 实例/Action 实例持久化，只有边界或中断才重新唤醒 Agent。

## 游戏 Agent 与行为执行

- [Behavior Tree in Unreal Engine](https://dev.epicgames.com/documentation/en-us/unreal-engine/behavior-tree-in-unreal-engine---overview)，Epic Games 官方文档。结论：行为树与 Blackboard 分离，事件驱动可以避免每帧无效遍历；高优先级事件可以中止低优先级任务。本项目使用 `bt_def`、`bt_instance` 和 Agent 动态上下文，普通分钟只推进 Action，事件才触发重新决策。
- 游戏 AI 的常见组合是 Behavior Tree、GOAP 和 Utility AI。项目采用混合方案：BT 表达可审计的长时计划，Utility/需求曲线提供候选排序，LLM 处理开放式语义选择，Platform 处理硬约束和资源结算。LLM 不替代 BT 执行器，也不直接决定物理状态。

## 需求、社会驱动与动机

- [A Theory of Human Motivation](https://psychclassics.yorku.ca/Maslow/motivation.htm)，A. H. Maslow，1943。原文强调多种需求可以同时作用，行为通常由多重动机和情境共同决定。项目不把五层需求硬编码为严格阶梯，而是使用可配置需求、缺口曲线、临界阈值和跨需求门控。
- [Needs and Subjective Well-Being Around the World](https://doi.org/10.1037/a0023779)，Tay 与 Diener，2011。结论：多类需求的满足都与幸福感相关，需求之间存在相对独立性和情境差异。项目保留生理、安全、归属、尊重和自我实现五类默认需求，同时允许世界配置增加需求和调整权重。
- [Self-Determination Theory](https://selfdeterminationtheory.org/SDT/documents/2000_RyanDeci_SDT.pdf)，Ryan 与 Deci，2000。结论：自主、胜任和联结是重要的心理需要，社会环境、反馈和奖励会影响内在动机。项目把自主感、能力熟练度、关系信任、声誉和社会规范作为驱动项，而不是只把奖励金额作为外驱。

## 本项目明确采用的原则

1. 世界真相由 Platform 和 SQLite 持有，Agent 只拥有受权限约束的知识和记忆。
2. 世界分钟推进确定性世界与长时 Action，LLM 只在决策边界和低频演化窗口介入。
3. 所有 Agent 输出都是 Intent；BT 注册、实体创建、记忆更新和世界事件都必须经 Platform 校验。
4. 初始故事、Agent、元动作、BT、提示词和规则来自配置包；每次初始化创建新的 SQLite 实例，实例只记录来源配置版本和哈希。
5. Prompt 使用共享静态前缀、角色策略、稳定身份和动态上下文分层，保证缓存命中并维持 Agent 的独立视角。
6. 反思、探索、沟通和技能演化都保存来源、时间、置信度、重要性和激活记录，支持回放和审计；长期记忆不会因为时间单独衰减，常用记忆通过激活得到巩固。
7. ReAct 是一个有限时长的会话状态机，不由世界模拟分钟驱动：模型可以在一次会话中多次调用只读工具、接收结果、修正请求，直到 Intent 被接受、主动等待或达到预算；长时 BT 的分钟进度由 Platform 单独推进。
