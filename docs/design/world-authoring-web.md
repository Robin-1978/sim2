# World Authoring Web 工作台

## 目标

世界包目前已经能通过 `sim2 world-schema`、`build-world`、`validate-world`
和 `describe-world` 生成、检查和初始化，但直接维护多份 YAML 仍然要求
作者理解较多的字段关系。网页工作台应降低配置门槛，同时保持一个事实：
它是现有 Definition Compiler 的用户界面，不是另一套 DSL，也不直接修改
运行中的世界。当前仓库已经提供一个无外部前端依赖的 MVP：主游戏页的
“世界编辑器”按钮内嵌 `/admin.html#view=authoring-view`，也可以直接打开
运营台的“设计世界”页；两条入口使用同一组接口：
`/authoring/schema`、`/authoring/catalog`、`/authoring/open`、
`/authoring/compile`、`/authoring/smoke`、`/authoring/export`、
`/authoring/create`、`/instances/init` 和显式的 `/authoring/build`。

## 统一入口与会话

- 主游戏页 HUD、设置抽屉和世界大厅都有“世界编辑器”入口。编辑器以全屏
  overlay + iframe 打开，仍显示游戏上下文；关闭时若发现活动实例已变化，
  会重载主页面，否则只刷新入口和角色视图。
- 编辑器 URL 通过 fragment 携带 `view` 和一次性 Operator session：例如
  `/admin.html#view=authoring-view&operator=<session>`。运营台读取后立即
  `history.replaceState` 去掉 token，只保留 `#view=...`，所以刷新或创建
  实例后仍回到编辑器，而令牌不会进入服务器日志。
- 主游戏页和运营台共享 `sessionStorage` 中的 Operator session，兼容旧的
  `sim2_operator_token` 键；在独立标签打开时由 fragment 完成交接。
- 运营台顶栏提供“返回游戏”。在 iframe 中通过 `postMessage` 请求父页面关闭
  编辑器；独立标签中直接回到 `/`。

## 单一契约

工作台只从以下接口生成编辑器和校验结果：

```text
world-schema / canonical Definition IR
        ↓
表单、关系选择器、BT 图编辑器、条件编辑器
        ↓
统一 YAML 模板 / 世界包文件
        ↓
结构编译 + Expression AST 编译
        ↓
引用、权限、可达性、资源守恒和执行性检查
        ↓
临时 SQLite smoke world
```

Action、Recipe、BT、Attribute、Capability、Entity、Rule 和 Evolution 的
字段说明、枚举、默认值、必填关系和错误路径都由 Schema/IR 提供。网页
代码不应重新复制 `success`、`failure`、效果原语或条件语法的规则。

## 页面和工作流

运营台“设计世界”页与主游戏页内嵌编辑器使用同一个三段式布局，视觉语言与
主游戏一致（深青底、琥珀主按钮、衬线标题）：

1. **写作清单（左）**：5 项可玩性条件、完成进度、推荐下一步按钮，以及折叠的
   “起点与高级工具”（从已验证模板复制、空白草稿、打开当前包、受限路径、
   AI 起草、Schema 参考）。
2. **草稿正文（中）**：按 01–05 排列的世界概览、地点、人物、物品与组织、
   行动与配方。默认每类只展示前 6 条，其余可展开；每条默认折叠，点“编辑”
   展开常用字段，复杂字段收在“更多字段（不常用）”里。顶部可在“表单 / JSON /
   格式化”之间切换。
3. **体检（右）**：8 项就绪检查、完整检查、3 分钟 smoke、导出，以及
   “发布并试玩”和“从已有模板创建实例”。

新手路径：

```text
选择一个已验证模板 → 改世界名字和一句话设定 → 改地点 / 人物 / 行动
    → 右侧体检补齐缺项 → 完整检查 → 试玩 3 分钟 → 生成并打开游戏
```

- 模板复制只替换浏览器草稿，不修改来源世界包；发布时如果目录名等于来源
  世界包，客户端会要求换名，避免覆盖正在编辑的来源。
- 空白草稿会显示“先从一个能运行的世界开始”的提示；行动、配方和行为树
  没有可复制样本时，添加按钮会禁用，避免生成不完整的必填结构。
- 玩家入口和出身直接放在世界概览内：入口地点来自地点列表，至少需要一个
  出身才能通过体检。
- “发布并试玩”调用 `/authoring/build-world` 写入世界包，再调用
  `/instances/activate` 打开新实例；页面随后重载到新世界。发布失败不会
  覆盖已有目录，勾选“覆盖同名世界包”才会覆盖。

### 网页工作台的交互状态

工作台的每个页面都显示同一个草稿版本和编译状态，避免作者把旧诊断误认为
当前结果：

```text
draft → compiling → diagnostics
                 ├── errors → edit draft → compiling
                 └── valid → smoke running → smoke result
                                      ├── failed → inspect event → edit draft
                                      └── passed → review diff → export / publish
```

浏览器保存的是草稿和 `draft_revision`，不是运行时 SQLite。表单输入直接写入
内存草稿；切换到 JSON 模式时从同一份草稿序列化。编译或 smoke 返回时必须
带回该 revision；如果用户在任务完成前继续编辑，旧结果标记为 `stale`，
不能覆盖新的错误列表或绿色状态。

字段、标签、单位、枚举和默认值来自 `/authoring/schema` 与模板本身；前端
只为常用字段提供中文标签，不维护世界专用的属性名或 Action 名。复杂结构
（规则、演化、故事、提示词、行为树节点、效果列表）保留完整 JSON 入口，
不会被表单悄悄丢弃。

## 校验层次

每次保存都可以执行便宜的增量检查；发布前执行完整检查：

| 层次 | 检查 | 权威实现 |
|---|---|---|
| 语法 | YAML/JSON 结构和字段类型 | Schema loader |
| 表达式 | 条件、公式的受限语法、括号和路径 | `sim2.dsl.expressions` |
| 定义 | Action/BT/Recipe 的形状、结果契约和禁止代码字段 | Definition Compiler |
| 实例语义 | 实体引用、地点可达性、目标、权限、资源守恒 | Platform definition runtime |
| 执行性 | 新 Action 是否绑定可执行 BT，BT 是否能由至少一个角色执行 | Platform + AgentView |
| 运行 smoke | 初始化临时 SQLite，启动一个初始行为并检查结果事件 | Bootstrap/Platform |

错误必须返回 `path`、`code`、`message`、`severity` 和可选的相关定义；
例如 `behavior_trees[2].nodes[1].action_id` 未注册，而不是只返回“配置无效”。
条件错误应指出表达式位置和期望语法，不能让 Agent 或作者猜测。

## 后端接口计划

现有 HTTP API 主要服务运行中的世界。authoring 工作台需要单独的草稿
资源，不把草稿混进运行时 SQLite：

- `GET /authoring/schema`：返回当前 World Template 和 Definition Schema；
- `GET /authoring/catalog`：列出 `--authoring-root` 下通过包校验的模板；
- `POST /authoring/compile`：编译内存中的模板，返回规范化 IR 和错误；
- `POST /authoring/smoke`：在临时目录创建 SQLite 并运行有限分钟；
- `POST /authoring/export`：导出 YAML 世界包和校验报告；
- `POST /authoring/create`：从选定模板创建独立 SQLite 实例；
- `GET /authoring/diff`：比较草稿与上一次编译产物；
- `GET /authoring/jobs/{id}`：读取 smoke/compile 的结构化结果。

这些接口应调用现有 bootstrap、Definition Compiler 和 Platform 服务，
不能在 HTTP handler 中复制校验或直接写数据库。草稿文件由工作区管理；
运行中世界仍只能通过 Platform 的状态变更入口操作。

接口返回的任务 ID 和草稿版本必须可轮询。建议的最小任务响应为：

```json
{
  "status": "running",
  "job_id": "compile-123",
  "draft_revision": 7,
  "phase": "references",
  "errors": [],
  "warnings": []
}
```

`status=passed` 只表示当前 revision 已完成相应检查；它不代表已经创建
SQLite。只有作者明确调用 CLI `sim2 init`（或后续受控的初始化接口）才会
实例化世界。

## 实现顺序

基础服务和网页 MVP 已落地；后续按以下顺序增强：

1. 将当前编译/导出响应继续收敛成带字段路径的结构化诊断，并增加 diff；
2. 用少量原生 HTML/JavaScript 页面扩展表单和 BT 图，不引入新的运行时
   领域模型；
3. 添加 unrelated world、动态 God 定义、非法条件和未绑定 Action 的
   端到端测试；
4. 再增加地图画布、版本历史和多人协作等功能。

首版不做任意代码编辑器、在线修改运行中 SQLite、自动发布或让网页绕过
Platform。世界演化产生的运行时定义仍通过 God Intent → Definition
Compiler → Platform → SQLite 进入实例；工作台只帮助人创建和审查初始包。
