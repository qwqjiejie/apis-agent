# APIs Agent 架构强化总结

本文档用于固化本项目的 Agent 开发架构思想。它不是普通的目录说明，而是从现有代码中提炼出一套可复用的多智能体平台设计方法：如何接入用户请求，如何组织 Agent、Tool、SubAgent，如何承载长任务，如何治理模型调用，如何让知识、记忆和基础设施共同支撑 Agent 运行。

## 1. 项目定位：从聊天 Agent 到可工程化多智能体平台

本项目是一个 Python FastAPI + LangGraph/DeepAgents 的多智能体平台。它的目标不是只包装一个 LLM API，而是把 Agent 运行所需的工程能力系统化：

- 对外提供 HTTP API、SSE 流式输出、后台任务状态查询等接口。
- 对内维护 Triage Agent、Executor Agent、Specialist SubAgent 三层协作模型。
- 通过 Tool Registry 管理工具能力，通过 AGENT.md 管理子 Agent 能力。
- 通过 TaskExecutor 支持长任务、任务快照、执行日志、人工审批、恢复和取消。
- 通过 ModelGateway 对模型调用做健康探活、fallback、熔断和状态上报。
- 通过文档模块、RAG、GraphRAG 和语义记忆，让 Agent 具备文件知识与跨会话记忆。
- 通过 ApplicationContainer 在应用生命周期中统一装配运行时依赖。

因此，这个项目更适合被理解为一个 "Agent Runtime Platform"，而不是一个普通聊天后端。

核心思想是：Agent 不是一个函数，而是一套运行时系统。它需要入口、上下文、工具、模型、记忆、任务生命周期、事件、可观测性和失败补偿。

## 2. 总体架构：分层而不割裂

当前项目结构体现的是按职责分层，再通过运行时容器装配起来。

```text
app/
  api/              HTTP 接入层，负责路由、鉴权、SSE、错误响应
  bootstrap/        运行时容器，集中持有启动后创建的共享对象
  agent/            Agent 创建工厂与后台执行包装器
  subagents/        声明式 Specialist 子 Agent
  tool/             Agent 工具注册、发现与具体工具实现
  modules/          业务能力模块，如 chat、tasks、documents、skills、identity
  gateway/          模型网关，治理模型调用与降级
  infrastructure/   PostgreSQL、MinIO、Milvus、Redis、Neo4j 等基础设施适配
  memory/           跨会话语义记忆
  prompt/           Triage、Executor 等系统提示词
  harness/          工具和子 Agent 热加载、发现机制
  common/           日志、异常、流式事件、LLM 构造、追踪等通用能力
  config/           配置读取与环境变量校验
```

这套分层背后有几个关键边界：

- `api` 不直接创建复杂运行时对象，而是从 `request.app.state.container` 获取依赖。
- `bootstrap` 不承载业务逻辑，只负责运行时依赖所有权。
- `agent` 负责创建和包装 Agent，不把业务模块逻辑塞进 Agent 工厂。
- `tool` 是 Agent 能力入口，工具内部再调用 `modules` 或 `infrastructure`。
- `modules` 表示业务用例，如任务、文档、会话、技能管理。
- `infrastructure` 只处理外部系统适配，不主导业务流程。
- `gateway` 把模型供应商调用从业务中剥离出来，形成可替换、可降级的大模型访问层。

这不是严格的 Clean Architecture，但它已经具备清楚的方向：入口层薄，运行时集中，能力模块内聚，基础设施可降级。

## 3. 核心链路：用户消息如何进入 Agent 系统

实时聊天入口位于 `app/api/routes/chat_routes.py` 的 `/chat`。一次用户请求大致经过以下链路：

```text
用户请求 /api/v1/chat
  -> 校验 message、conversationId、用户身份
  -> 从 request.app.state.container 获取运行时依赖
  -> 注入语义记忆和历史后台任务结果
  -> 构造 ChatContext，绑定 user_id/session_id
  -> 调用 Triage Agent 的 astream_events
  -> 将模型输出、工具开始/结束、状态事件转换为 SSE
  -> 保存会话、异步写入语义记忆、记录在线评估
```

这里最重要的架构思想是：`/chat` 不是简单转发 LLM 输出，而是一个上下文装配器和事件翻译器。

它做了几件对 Agent 产品化很重要的事：

- 使用 `ChatContext` 贯穿 HTTP、Agent、Tool、后台任务。
- 使用 LangGraph `thread_id` 维持同一会话的 checkpoint 历史。
- 将语义长期记忆检索结果作为 system 上下文注入。
- 将已完成后台任务的结果重新注入当前对话。
- 将 Agent 内部事件转换成统一 SSE 事件协议。
- 对模型 fallback 状态、工具调用状态、最终文本输出做统一流式返回。

这说明项目把"用户一问一答"提升成了"可带状态、可带记忆、可带工具、可延伸到后台任务"的交互协议。

## 4. Agent 分层：Triage、Executor、Specialist

Agent 创建集中在 `app/agent/agent_factory.py`。

### Triage Agent

Triage Agent 是用户入口。它持有较完整的外部工具集合，负责判断用户请求该如何处理：

- 简单问题可以直接回答。
- 需要工具的问题可以直接调用工具。
- 需要专业能力的问题可以委托 Specialist SubAgent。
- 复杂、长周期、需要多步协作或审批的问题可以创建后台任务。

Triage 的价值是让主入口保持弹性，不让所有任务都挤在一个 Agent 执行上下文里。

### Executor Agent

Executor Agent 是后台任务执行者。它不是面向即时聊天，而是面向长任务编排。

它的工具更精简，主要保留：

- `request_approval`：触发人审中断。
- `read_task_journal`：读取任务执行日志，理解已完成步骤。
- deepagents 内置的 `task` 能力：委托 Specialist。

Executor 的关键不是"知道所有工具"，而是"会编排、会中断、会恢复"。这避免后台长任务变成一个不可控的超级 Agent。

### Specialist SubAgent

Specialist 通过 `app/subagents/*/AGENT.md` 声明式定义。目前包括：

- `research_specialist`：深度研究。
- `coding_specialist`：代码开发与调试。
- `code_review_specialist`：代码审查。
- `data_analysis_specialist`：数据分析。
- `file_analysis_specialist`：文件分析。
- `ppt_specialist`：PPT 生成。

每个 `AGENT.md` 声明：

- `name`
- `description`
- `allowed_tools`
- system prompt

这种方式的意义是把"角色能力"从 Python 代码中抽出来。新增专家时优先新增一个 `AGENT.md`，而不是修改主 Agent 逻辑。

## 5. 长任务链路：让 Agent 具备工程级生命周期

长任务体系集中在 `app/modules/tasks` 和 `app/agent/executor_agent.py`。

核心对象包括：

- `TaskExecutor`：任务提交、驱动、取消、恢复、关闭和启动恢复。
- `TaskSnapshot`：任务状态事实，包括 task_id、query、status、result、approval_id 等。
- `JournalEntry`：任务过程事实，记录 created、executing、approval_requested、decision、completed 等事件。
- `TaskStore`：任务仓储协议，支持内存和 PostgreSQL/LangGraph Store 两种实现。
- `EventBus`：任务状态广播，Redis 可用时跨进程广播，不可用时降级为内存模式。
- `DeadLetterQueue`：关键写入失败后的补偿队列。

复杂任务创建链路如下：

```text
Triage Agent 判断任务复杂
  -> 调用 create_background_task 工具
  -> TaskExecutor.submit 创建 TaskSnapshot
  -> 后台 asyncio.Task 启动 ExecutorAgent
  -> ExecutorAgent 调用 executor_agent.astream_events
  -> TaskExecutor 消费事件并更新快照、日志、状态
  -> 用户通过 /task/status、/task/stream 查询结果
```

人工审批链路如下：

```text
Executor 调用 request_approval
  -> LangGraph interrupt
  -> ExecutorAgent 序列化 interrupt 信息
  -> TaskExecutor 将任务标记为 waiting_human
  -> 用户调用 /task/resume
  -> ExecutorAgent 用 Command(resume=...) 恢复同一 checkpoint
```

这里值得固化的架构原则是：长任务不能只靠内存中的协程。它必须有状态快照、日志、恢复入口、取消信号、失败补偿和用户可查询接口。

## 6. 模型网关：把 LLM 调用纳入工程治理

模型网关位于 `app/gateway`，核心类是 `ModelGateway` 和 `GatewayModelWrapper`。

它承担的职责包括：

- 注册主模型和 fallback 模型。
- 维护当前活跃模型。
- 记录模型请求量、错误率、连续失败次数、延迟指标。
- 通过 CircuitBreaker 对异常模型进行熔断。
- 在模型失败或熔断时自动走 fallback 链。
- 将 fallback 状态通过事件返回给 SSE。
- 支持后台健康探活和模型热切换。

这部分的架构思想很重要：Agent 的模型层不是一个静态 client，而是一个可治理的运行时资源。

如果直接在各处 new `ChatOpenAI`，会带来几个问题：

- 模型切换困难。
- 失败后不能统一 fallback。
- 无法收集统一健康指标。
- 无法在 Agent 运行中把降级状态反馈给用户。
- 测试时难以替换。

当前做法是让 `ApplicationContainer` 持有 `ModelGateway`，Agent 工厂优先从 gateway 构建 LLM。这让模型层成为平台能力，而不是业务代码里的散点依赖。

## 7. 知识、文件与记忆：让 Agent 不只依赖上下文窗口

文档能力集中在 `app/modules/documents`。

上传文件后，`FileService` 会处理：

- 文件类型与 MIME 校验。
- 文件大小限制。
- SHA-256 去重检查。
- 本地临时保存。
- MinIO 存储，失败时可降级。
- PDF/Word/TXT 等内容解析。
- 文本分块。
- embedding 向量化。
- Milvus 向量写入。
- 文件记录持久化。
- 文件处理状态事件。

RAG 检索链路在 `app/modules/documents/retrieval.py` 中进一步增强：

- QueryRewriter：对问题做查询改写。
- MultiRecall：多路向量检索。
- RRF：融合多路召回结果。
- DynamicTopK：根据分数分布裁剪结果。
- LLM Relevance Filter：过滤高分但不相关的片段。

GraphRAG 位于 `app/modules/documents/graph.py`，在 Neo4j 可用时补充实体和关系上下文，不可用时自动降级。

长期记忆位于 `app/memory/semantic_memory.py`。每轮对话后，它可以把 QA 对写入语义记忆；新问题到来时按用户隔离检索相似记忆，再注入 Agent 上下文。

这部分可抽象为一个原则：Agent 的知识来源应该分层。

- 当前输入：用户本轮问题。
- 会话历史：LangGraph checkpoint。
- 文件上下文：文档解析、RAG、GraphRAG。
- 长期记忆：跨会话语义记忆。
- 工具事实：搜索、文件、命令、任务日志等工具返回。

## 8. 扩展规范：新增能力应该放在哪里

### 新增 HTTP 能力

放在 `app/api/routes` 中，新建或扩展 router。路由只负责请求校验、权限、响应格式和调用用例，不直接创建外部连接。

如果需要运行时对象，优先从 `request.app.state.container` 获取。

### 新增业务能力

放在 `app/modules/<domain>` 中。一个模块内部可以包含：

- `service.py`：用例编排。
- `ports.py`：协议或接口。
- `events.py`：领域事件。
- `status.py`：状态枚举。
- 其他与该领域强相关的对象。

### 新增基础设施适配

放在 `app/infrastructure/<system>` 中，例如 PostgreSQL、Redis、MinIO、Milvus、Neo4j。

基础设施代码应尽量只处理连接、读写和外部系统细节，不承载 Agent 决策逻辑。

### 新增工具

放在 `app/tool/<name>.py` 中，使用 `@register_tool` 和 LangChain `@tool` 注册。

工具应该是 Agent 调用业务能力的薄入口。工具内部可以调用 `modules`，必要时从 `get_application_container()` 获取运行时依赖。

新增工具后应注意：

- 工具名不能与其他模块冲突。
- 工具描述要让 LLM 明确知道何时使用。
- 有危险操作的工具要接入审批或权限控制。
- 测试至少覆盖工具是否能拿到正确运行时依赖。

### 新增 Specialist SubAgent

放在 `app/subagents/<name>/AGENT.md` 中，声明 `name`、`description`、`allowed_tools` 和系统提示词。

新增 Specialist 的基本原则：

- 一个 Specialist 只解决一类清晰任务。
- `description` 要适合 Triage 选择。
- `allowed_tools` 要最小化授权。
- system prompt 要写工作流程和边界条件。

### 新增 Skill

内置 Skill 放在 `app/skills`，运行时上传 Skill 放在配置的 managed skills 目录。`SkillManager` 负责扫描、同步、启用/禁用和安全上传。

Skill 更适合沉淀可复用工作流；SubAgent 更适合沉淀平台内的执行角色。

## 9. 工程治理：让 Agent 可控、可测、可恢复

这个项目已经形成了一些值得保留的治理机制：

- 配置集中在 `app/config/settings.py`，用 Pydantic Settings 校验关键环境变量。
- 启动装配集中在 FastAPI `lifespan`，避免模块导入时产生外部连接副作用。
- 运行时对象集中进 `ApplicationContainer`，测试可以替换容器对象。
- 模型调用统一经过 Gateway，支持健康状态、fallback、熔断。
- Agent middleware 支持模型调用次数限制、工具调用次数限制、模型重试和工具重试。
- SSE 事件格式集中在 `app/common/streaming.py`。
- 任务状态、结果和日志持久化，不把长任务绑定在一次 HTTP 请求生命周期里。
- PostgreSQL、Redis、MinIO、Milvus、Neo4j 等基础设施多数支持不可用时降级。
- 测试按 `unit`、`contract`、`integration`、`e2e` 标记分层。

尤其是测试中已经把这些能力固化为契约：

- 运行时容器能被注册、读取、清理。
- HTTP 路由从 request container 读取依赖。
- 工具能从 runtime container 获取任务执行器和上下文。
- 后台任务可以查询、持久化、恢复。
- LangGraph interrupt 可以持久化为 `waiting_human`。
- resume 接口可以恢复同一 checkpoint。
- 模型网关 fallback 会产出状态事件。
- 语义记忆持久化并按用户隔离。
- DeadLetterQueue 可以跨队列重启后重试。

这些测试说明项目已经把"能跑"推进到了"可依赖"。

## 10. 架构原则：从本项目提炼出的 Agent 开发方法论

### 原则一：Agent 是运行时系统，不是 Prompt 函数

一个可用的 Agent 平台至少需要模型、工具、上下文、记忆、状态、事件、错误处理和生命周期。只封装 Prompt 和 LLM 调用，不足以支撑复杂业务。

### 原则二：主 Agent 做分流，不做所有事

Triage 应该负责判断和委托，而不是无限膨胀。专业能力应该交给 Specialist，长任务应该交给 Executor。

### 原则三：工具是能力边界，不是随手函数

Tool 是 LLM 进入真实系统的边界。它应该有清晰名称、清晰描述、最小权限、可测试行为和必要的人审保护。

### 原则四：复杂任务必须后台化

复杂任务不应该绑死在一次 HTTP 请求里。后台任务要有 task_id、状态机、快照、日志、取消、恢复和查询接口。

### 原则五：人审是架构能力，不是对话礼貌

涉及危险操作、外部写入、审批决策时，Agent 应该能主动挂起，并等待人类明确 approve/reject。这个能力要进入任务状态机，而不是只靠模型回复"请确认"。

### 原则六：模型调用需要网关治理

LLM 是不稳定外部依赖。生产级 Agent 需要 fallback、熔断、探活、指标和状态上报。

### 原则七：上下文要分层管理

不要把所有信息都塞进 prompt。会话历史、语义记忆、文件 RAG、任务日志、工具返回都应该有独立来源和注入策略。

### 原则八：运行时依赖集中装配

数据库、模型网关、任务执行器、事件总线、文件服务等运行时对象应在应用生命周期创建，并由容器统一持有。模块导入时不应该产生重型副作用。

### 原则九：扩展能力应声明化

SubAgent 用 `AGENT.md` 声明，Tool 用装饰器注册，Skill 用 `SKILL.md` 描述。这让扩展点对人和 Agent 都可读。

### 原则十：所有关键状态都要可观测、可恢复

Agent 平台的复杂度来自不确定性。状态、日志、事件、评估、死信队列和测试契约，是把不确定性收束成工程系统的关键。

## 11. 推荐阅读路径

理解本项目时，建议按以下顺序阅读：

```text
1. app/api/main.py
   看应用如何启动、装配和关闭。

2. app/bootstrap/container.py
   看运行时依赖如何被集中持有。

3. app/api/routes/chat_routes.py
   看用户请求如何进入 Triage Agent。

4. app/agent/agent_factory.py
   看 Triage、Executor、Specialist 如何创建。

5. app/tool/registry.py 和 app/tool/task_tools.py
   看工具如何注册，以及复杂任务如何后台化。

6. app/modules/tasks/executor.py
   看长任务生命周期如何被工程化。

7. app/agent/executor_agent.py
   看后台 Agent 如何执行、挂起、恢复。

8. app/gateway/model_gateway.py 和 app/gateway/middleware.py
   看模型调用如何被治理。

9. app/modules/documents 和 app/memory
   看知识库、RAG 和长期记忆如何接入。

10. tests/contract 与 tests/unit
    看哪些架构能力已经被测试固化为契约。
```

## 12. 后续强化方向

后续可以继续把这份总结升级为团队级架构规范：

- 补一张用户请求到后台任务的时序图。
- 补一张运行时依赖装配图。
- 为 Tool、SubAgent、Skill 各写一份新增模板。
- 把危险工具和人审策略单独整理成安全规范。
- 把 TaskStatus 状态机画成图。
- 把模型网关治理总结成生产运维手册。
- 补充"新增一个完整 Agent 能力"的端到端示例。

最后更新：2026-07-18。
