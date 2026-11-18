# Agent 上下文管理

本文描述 sim2 的通用上下文层。世界内容、阈值和角色规则来自世界包；框架只提供
预算、排序、压缩、记忆和可观测性，不包含任何世界名称或专用分支。

## 单一事实源

一个 ReAct 会话包含两部分：

1. **动态世界视图**：每一步由 `render_context` 重新渲染；
2. **协议尾部**：tool call 与 tool result 必须保持 provider 原生消息顺序。

`AgentEngine` 在发送前用最新渲染替换第一条 `user` 消息，同时保留后续 tool
消息；world-builder 等自定义多轮调用不受影响。这样模型看到的是当前世界、当前
压缩结果和最新平台反馈，而不是第一轮的旧快照。审计记录的 prompt 与实际发送的
动态上下文一致。

## 预算与 Pin

`rules.context_limits` 可以按角色配置：

```yaml
context_limits:
  default_chars: 16000
  default_tokens: 6000
  roles:
    agent_decision:
      max_chars: 15000
      max_tokens: 5500
    god:
      max_chars: 24000
      max_tokens: 9000
```

`render_context` 同时检查字符数和估算 token。没有配置时，世界级角色默认
20000 字符，角色级默认 16000。token 估算是语言感知的保守值，provider 返回的
真实 usage 仍是准绳。

以下 section 被 pin 住，不会因为压缩被整块删除：

- `decision`、`executable_behaviors`、`active_execution`、`outcome_review`；
- `interaction_history`（最新平台拒绝/修正）；
- `rule_condition_fields`（God 提交合法规则所需的精确字段）；
- `protocol`、`platform_rules`、`allowed_intents_and_tools`、`world_state`。

背景 section 按可检索性依次让位；世界趋势、研究来源、关系等可以省略，但不会
牺牲当前决策或精确机器字段。

## 上下文指标

每次渲染返回一份 metrics：

- `chars`、`estimated_tokens`、`max_chars`、`max_tokens`；
- `sections`（每个 section 的字符权重）；
- `omitted`（被压缩掉的 section）；
- `static_prefix_chars`、`dynamic_chars`。

`AgentTurn.context_metrics` 保留整轮列表；`llm_call` 持久化
`prompt_tokens_est` 和 `context_metrics_json`。`llm_cache_metrics` 额外报告
平均估算 token 和带 metrics 的调用数。静态前缀哈希继续用于观测 provider
缓存复用。

## 记忆 v2

`MemoryService` 的排序是配置驱动的：

```yaml
memory_quality:
  ranking:
    relevance: 0.62
    importance: 0.18
    recency: 0.12
    access: 0.08
    half_life_minutes: 1440
    diversity: 0.12
```

- relevance 来自可选 BGE/词法检索；
- importance、access_count 和 last_access_minute 来自记忆行；
- diversity 用轻量 token 重叠做去重，避免同一经验占满上下文；
- 标签只做软加分，不做硬过滤。

`compact` 不再把所有旧记忆拼成一个字符串，而是生成结构化摘要：
`facts`、`lessons`、`open_loops`、`other`，并保留来源数量与类型。

## 评估

`tests/test_prompt_render_sections.py` 覆盖：

- 角色 section 顺序与隔离；
- 压缩后仍保留 decision 和 executable behaviors；
- God 的精确 `world["..."]` 字段和 world trends 在压缩后仍存在；
- token 预算生效；
- AgentTurn 与 `llm_call` 持久化上下文指标。

`tests/test_context_memory_v2.py` 覆盖重要性/新近性排序和结构化记忆压缩。

## 原则

- 机制和阈值进 YAML；框架只实现通用能力。
- 固定内容只能做开局旁白或上手提示，并且必须有窗口；后续内容来自 Story/God。
- 任何压缩都必须证明：当前决策、可执行行为、精确字段和最新平台反馈不会丢。
