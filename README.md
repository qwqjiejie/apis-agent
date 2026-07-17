# APIs-Agent

基于 **FastAPI + deepagents + LangGraph** 构建的多智能体协作平台。系统采用
Triage、Executor、Specialist 三层协作模型：Triage 处理同步对话与任务分流，
Executor 驱动长周期后台任务，Specialist 通过声明式 `AGENT.md` 提供领域能力。

当前仓库同时包含后端 API、轻量前端、任务可靠性组件、文档 RAG、Skills 管理和
在线/离线评估工具，是一个单体部署、模块化组织的 Python 应用。

架构边界、实施记录和验收结果见
[架构优化实施计划](docs/architecture-optimization-plan.md)，目录放置规则见
[项目结构蓝图](docs/project-structure-blueprint.md)。

## 核心能力

- **三层 Agent 协作**：Triage DeepAgent -> Executor DeepAgent -> Specialist SubAgent
- **LLM 自主路由**：通过 Function Calling 决定直接回答、调用工具、委托 Specialist 或创建后台任务
- **声明式 Specialist**：扫描 `app/subagents/*/AGENT.md`，按 `allowed_tools` 注入最小工具集
- **统一 Agent 工厂**：Triage 与 Executor 复用 `create_deep_agent`、中间件和模型网关
- **模型可靠性**：动态模型链、健康探活、三态熔断、调用级降级、热切换和 SSE 状态事件
- **后台任务引擎**：任务快照、用户归属校验、HITL 挂起/恢复、Journal、EventBus、DeadLetter
- **持久化运行时**：LangGraph AsyncPostgresSaver、AsyncPostgresStore 和 PG TaskStore
- **文档处理**：类型/MIME 校验、SHA-256 去重、MinIO/本地暂存、文本解析、分块和向量索引
- **增强 RAG**：查询重写、多路并行召回、RRF、动态 TopK、LLM 相关性过滤
- **跨会话记忆**：按用户保存语义记忆，并在新请求中召回相关历史问答
- **Skills 管理**：运行时 Skill 扫描、数据库状态、zip 上传、启用/禁用和删除
- **轻量身份体系**：匿名身份、JWT 注册登录、匿名会话迁移，以及会话/文件/任务归属校验
- **可观测性**：trace ID、结构化运行状态、Langfuse、在线指标采集和 deepEval 离线评估
- **热加载**：工具模块和 Specialist 定义变更后重建 Triage/Executor，并原子替换应用引用

## 已内置 Specialist

| Specialist | 目录 | 主要职责 |
|---|---|---|
| `ppt_specialist` | `app/subagents/ppt` | PPT 生成 |
| `research_specialist` | `app/subagents/research` | 深度研究和信息综合 |
| `file_analysis_specialist` | `app/subagents/file_analysis` | 上传文档分析 |
| `data_analysis_specialist` | `app/subagents/data_analysis` | 数据分析和报告 |
| `code_review_specialist` | `app/subagents/code_review` | 代码质量、安全和性能审查 |
| `coding_specialist` | `app/subagents/coding` | 代码编写、修改和调试 |

## 技术栈

| 层级 | 技术 |
|---|---|
| Web/API | FastAPI、Uvicorn、SSE Starlette |
| Agent 编排 | deepagents、LangChain、LangGraph |
| 模型接口 | OpenAI-compatible Chat API |
| 状态持久化 | AsyncPostgresSaver、AsyncPostgresStore |
| 业务数据 | PostgreSQL、SQLAlchemy、psycopg2 |
| 缓存与事件 | Redis Pub/Sub、进程内降级 EventBus |
| 向量检索 | Milvus、Embedding API |
| 文件存储 | MinIO，本地临时文件兜底 |
| 文档解析 | pdfplumber、python-docx、MinerU 可选 |
| 图数据 | Neo4j 可选 |
| 搜索 | Tavily |
| 可观测性 | Langfuse、trace ID、在线评估 |
| 质量评估 | pytest、deepEval |

## 架构总览

```text
Client / Static Web
        |
        v
FastAPI: CORS -> RateLimit -> Trace/Logging -> Routes
        |
        v
ApplicationContainer (唯一运行时依赖入口)
        |
        v
Triage DeepAgent (container.agent)
        |
        +-- 直接回答 / 调用工具
        |
        +-- task tool -> Specialist SubAgent
        |
        `-- create_background_task
                    |
                    v
              TaskExecutor
                    |
                    v
         Executor DeepAgent (container.executor_agent)
                    |
                    +-- Specialist SubAgent
                    +-- request_approval -> HITL interrupt/resume
                    `-- TaskSnapshot + Journal + EventBus
                                |
                                v
                  AsyncPostgresStore / Memory fallback
```

### 分层职责

| 层级 | 目录 | 职责 |
|---|---|---|
| 启动与传输 | `app/main.py`、`app/bootstrap`、`app/api` | 基础设施预检、运行时依赖装配、FastAPI lifespan、中间件、路由和 SSE |
| Agent 编排 | `app/agent`、`app/subagents`、`app/prompt` | Agent 构建、Executor 包装、领域代理和系统提示词 |
| 业务模块 | `app/modules` | chat、tasks、documents、identity、skills 的用例、模型和端口 |
| 可靠性 Harness | `app/harness` | 工具/SubAgent 热加载和旧任务路径兼容导出 |
| 模型网关 | `app/gateway` | 模型注册、健康状态、熔断、动态路由和状态事件 |
| 基础设施 | `app/infrastructure` | PostgreSQL、Redis、Milvus、MinIO 和 Neo4j 适配器 |
| 文档解析 | `app/readers` | MinerU 等外部解析器适配 |
| 记忆与上下文 | `app/memory`、`app/context` | 语义记忆、token 统计和上下文压缩工具 |
| 扩展能力 | `app/tool`、`app/subagents`、`app/skills` | Agent 工具和只读内置声明 |
| 评估 | `app/evaluation` | 在线采集、离线 Agent/RAG 评估和数据集 |

## 核心运行流程

### 对话流程

1. `POST /api/v1/chat` 接收 `message`、`conversationId` 和 `fileIds`。
2. 限流中间件按 IP 和会话执行滑动窗口检查，并在 Redis 不可用时使用进程内计数。
3. 认证层从 JWT 或 `X-Anonymous-Id` 得到用户身份，并校验已有会话归属。
4. 路由解析能力前缀，将文件 ID、跨会话语义记忆和已完成后台任务结果注入上下文。
5. Triage DeepAgent 自主选择直接回答、调用工具、委托 Specialist 或创建后台任务。
6. Agent 事件转换为 SSE，输出 `thinking`、`text`、`tool_start`、`tool_end`、`status` 和完成事件。
7. 流结束后保存会话、记录在线评估、异步写入语义记忆，并为新会话生成标题。

### 后台任务与 HITL

1. `create_background_task` 从当前 `ChatContext` 继承 `user_id` 和 `session_id`。
2. TaskExecutor 创建 TaskSnapshot，持久化后立即返回任务 ID。
3. Executor DeepAgent 编排 Specialist 执行长任务，并持续写入快照和 Journal。
4. `request_approval` 触发 LangGraph interrupt，任务进入 `waiting_human`。
5. `/api/v1/agent/task/resume` 将 `approved` 或 `rejected` 映射为 DeepAgents HITL decision，并从原 checkpoint 继续执行。
6. 快照或 Journal 写入失败时进入 DeadLetter，后台扫描器每 120 秒重试。
7. 优雅关闭时等待运行任务，超时任务保存恢复提示后取消。

启动恢复的当前语义：

- `waiting_human` 任务保留挂起状态，可在重启后继续审批。
- `created` 或 `executing` 任务因缺少完整运行时恢复能力，会被标记为 `cancelled`。

### 文档与 RAG

```text
Upload
  -> 文件类型/MIME/大小校验
  -> 用户范围内同名检测
  -> 本地临时文件 + 可选 MinIO
  -> MinerU(PDF 可用时) / 默认解析器
  -> 文本分块
  -> Embedding
  -> Milvus file_chunks

Query
  -> QueryRewriter
  -> 多路并行向量检索
  -> RRF 融合
  -> file_id 过滤
  -> Dynamic TopK
  -> LLM Relevance Filter
  -> RAG context
```

`app/modules/documents/graph.py` 和 Neo4j 适配器已经提供图谱检索基础组件，但当前默认
`rag_service` 尚未把 GraphRAG 接入主检索链路。

### 模型网关

每次模型调用都通过 `GatewayModelWrapper` 动态获取可用模型链，而不是在 Agent
创建时固定单一模型。调用流程包括：

1. 跳过处于 OPEN 状态的模型。
2. 按主模型和 fallback 顺序尝试调用。
3. 记录成功率、连续错误和延迟分位数。
4. 更新三态熔断器 `CLOSED -> OPEN -> HALF_OPEN`。
5. 降级时通过 SSE `status` 事件通知前端。
6. 管理接口热切换活跃模型，后续调用立即使用新模型。

## 启动与关闭

### 推荐启动路径

```text
python app/main.py
  -> PostgreSQL / Redis / 已配置 Milvus 预检
  -> 启动 app.api.main:app
  -> FastAPI lifespan 初始化运行时组件
```

lifespan 初始化顺序：

1. ModelGateway、主模型、可选 fallback 和健康探活。
2. LangGraph PostgreSQL checkpointer/store。
3. 可选 MinIO。
4. Specialist 发现，创建 Triage 和 Executor DeepAgent。
5. TaskStore、TaskExecutor、TaskContextManager。
6. DeadLetter 及快照/Journal 重试处理器。
7. SemanticMemory 和 SkillManager。
8. 可选 Neo4j、Redis EventBus。
9. DeadLetter 扫描器、Tool/SubAgent 热加载器。
10. 处理持久化的未完成任务。

关闭时依次排干后台任务、停止热加载和 DLQ 扫描、停止模型探活，并关闭 PG 和
Neo4j 连接。

### 基础设施策略

| 组件 | 推荐入口行为 | lifespan 内部行为 |
|---|---|---|
| PostgreSQL | 预检失败则拒绝启动 | LangGraph Store 初始化失败时任务层可退回内存 |
| Redis | 预检失败则拒绝启动 | EventBus/RateLimit 支持进程内降级 |
| Milvus | 配置 `MILVUS_HOST` 后预检 | 不可用时跳过向量检索 |
| MinIO | 不参与启动预检 | 不可用时保留本地解析流程 |
| Neo4j | 不参与启动预检 | 不可用时跳过图谱能力 |
| Langfuse | 不参与启动预检 | 未配置时关闭追踪集成 |

如直接运行 `uvicorn app.api.main:app`，会绕过 `app/main.py` 的基础设施预检，主要
用于测试或降级调试，不建议作为生产启动命令。

## 快速开始

### 环境要求

- Python 3.11-3.14
- uv 0.8+
- PostgreSQL 16+
- Redis
- 可选：Milvus 2.4+、MinIO、Neo4j、Langfuse、MinerU
- 可访问的 OpenAI-compatible LLM API

### 1. 安装

```bash
uv sync --frozen --extra dev
```

### 2. 配置

```bash
cp .env.example .env
```

最小生产配置示例：

```dotenv
APP_ENV=production
DATA_DIR=/var/lib/apis-agent
LLM_API_KEY=sk-your-api-key-here
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
JWT_SECRET=replace-with-a-long-random-secret

PG_HOST=127.0.0.1
PG_PORT=5432
PG_USER=postgres
PG_PASSWORD=replace-me
PG_DB=apis_agent

REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

TAVILY_API_KEY=tvly-your-tavily-key-here
```

完整配置见 `.env.example`。`LLM_API_KEY` 必填；生产环境必须设置稳定的高强度
`JWT_SECRET`，否则服务重启后已有令牌会失效。

### 3. 初始化业务表

```bash
uv run alembic upgrade head
```

已有 `sql/apis_agent_pg.sql` 数据库完成结构核对后，执行
`uv run alembic stamp 0001` 纳入迁移版本，再执行 `uv run alembic upgrade head`。
LangGraph 表仍由框架 setup 创建。

### 4. 启动

```bash
uv run python -m app.main
```

- 前端：<http://localhost:8080/>
- OpenAPI：<http://localhost:8080/docs>
- OpenAPI JSON：<http://localhost:8080/openapi.json>
- Liveness：<http://localhost:8080/health/live>
- Readiness：<http://localhost:8080/health/ready>

完整本地基础设施可直接启动：

```bash
docker compose up --build
```

生产覆盖示例位于 `deploy/compose.production.yaml`，敏感配置模板位于
`deploy/.env.production.example`。

## API

除 Skills 和认证路由已经包含完整前缀外，其余业务 Router 在应用装配时统一挂载到
`/api/v1`。接口以 POST JSON 为主，上传使用 multipart，流式接口返回 SSE。

### 对话、任务与管理

| Method | Endpoint | 说明 |
|---|---|---|
| POST | `/api/v1/chat` | 推荐的统一对话入口 |
| POST | `/api/v1/agent/chat` | 兼容入口，已标记 deprecated |
| POST | `/api/v1/agent/pptx/download` | 下载生成的 PPT |
| POST | `/api/v1/agent/stop` | 通过 Redis 发布停止信号 |
| POST | `/api/v1/agent/shell/confirm` | 确认或拒绝待执行 Shell 命令 |
| POST | `/api/v1/agent/task/status` | 查询用户所属任务状态 |
| POST | `/api/v1/agent/task/stream` | 获取任务状态 SSE 快照 |
| POST | `/api/v1/agent/task/list` | 列出当前用户任务 |
| POST | `/api/v1/agent/task/cancel` | 取消当前用户任务 |
| POST | `/api/v1/agent/task/resume` | 恢复 `waiting_human` 任务 |
| POST | `/api/v1/agent/feedback` | 提交会话反馈 |
| POST | `/api/v1/agent/admin/gateway` | 查询模型网关状态 |
| POST | `/api/v1/agent/admin/gateway/switch` | 切换活跃模型 |

### 会话

| Method | Endpoint | 说明 |
|---|---|---|
| POST | `/api/v1/session` | 创建会话 ID |
| POST | `/api/v1/session/list` | 当前用户会话分页列表 |
| POST | `/api/v1/session/detail` | 会话详情和消息 |
| POST | `/api/v1/session/delete` | 删除当前用户会话 |

### 文件

| Method | Endpoint | 说明 |
|---|---|---|
| POST | `/api/v1/file/list` | 当前用户文件分页列表 |
| POST | `/api/v1/file/upload` | multipart 文件上传 |
| POST | `/api/v1/file/info` | 文件元数据 |
| POST | `/api/v1/file/content` | 已解析文本 |
| POST | `/api/v1/file/delete` | 删除文件、对象和向量块 |
| POST | `/api/v1/file/exists` | 检查当前用户文件是否存在 |
| POST | `/api/v1/file/progress` | 文档处理进度 SSE |

### 认证

| Method | Endpoint | 说明 |
|---|---|---|
| POST | `/api/v1/auth/register` | 注册并签发 JWT |
| POST | `/api/v1/auth/login` | 登录并签发 JWT |
| POST | `/api/v1/auth/sync` | 将匿名会话和文件迁移到登录用户 |
| GET | `/api/v1/auth/me` | 返回当前身份和匿名状态 |

身份解析优先级：`Authorization: Bearer <token>` -> `X-Anonymous-Id` -> 请求级临时匿名 ID。

### Skills

| Method | Endpoint | 说明 |
|---|---|---|
| GET | `/api/v1/skills` | 列出数据库中的 Skills |
| POST | `/api/v1/skills/upload` | 上传包含 `SKILL.md` 的 zip |
| PUT | `/api/v1/skills/{name}/toggle` | 启用或禁用 Skill |
| DELETE | `/api/v1/skills/{name}` | 删除数据库记录和 Skill 目录 |

`app/skills` 只保存版本控制内的只读内置 Skills；上传内容写入
`DATA_DIR/MANAGED_SKILLS_DIR`。它们都不是 Codex 用户级 `~/.agents/skills`。

## 项目结构

```text
apis-agent/
|-- pyproject.toml                  # 项目元数据、运行和开发依赖
|-- uv.lock                         # 跨环境依赖锁定
|-- .env.example                   # 环境变量模板
|-- Dockerfile                     # 只读生产镜像
|-- compose.yaml                   # 完整本地基础设施
|-- migrations/                    # Alembic schema 版本
|-- deploy/                        # 入口脚本和生产覆盖示例
|-- docs/adr/                      # 架构决策记录
|-- app/
|   |-- main.py                    # 正式启动入口和基础设施预检
|   |-- api/
|   |   |-- main.py                # FastAPI app、lifespan、中间件和路由装配
|   |   |-- middleware/
|   |   |   `-- rate_limit.py      # Redis/内存滑动窗口限流
|   |   `-- routes/
|   |       |-- agent.py           # Agent 子路由聚合和兼容导出
|   |       |-- chat_routes.py     # 同步聊天和 SSE 输出
|   |       |-- task_routes.py     # 后台任务查询、取消和恢复
|   |       |-- gateway_routes.py  # 模型状态和热切换
|   |       |-- artifact_routes.py # PPT、停止和 Shell 确认
|   |       |-- feedback_routes.py # 用户反馈
|   |       |-- session.py         # 会话接口
|   |       |-- file.py            # 文件接口和处理进度
|   |       |-- auth_routes.py     # 注册、登录和匿名数据迁移
|   |       `-- skill_routes.py    # 运行时 Skills 管理
|   |-- agent/
|   |   |-- agent_factory.py       # Triage/Executor 统一 DeepAgent 工厂
|   |   `-- executor_agent.py      # 后台执行和 checkpoint resume 适配
|   |-- subagents/                 # 6 个声明式 Specialist AGENT.md
|   |-- bootstrap/                 # ApplicationContainer 和应用依赖装配
|   |-- modules/
|   |   |-- chat/                  # 会话、标题、反馈和持久化端口
|   |   |-- documents/             # 上传、解析、索引、检索和 GraphRAG
|   |   |-- identity/              # JWT、用户和匿名数据迁移
|   |   |-- skills/                # 内置/托管 Skill 生命周期
|   |   `-- tasks/                 # 任务上下文、执行、事件和失败重试
|   |-- infrastructure/
|   |   |-- postgres/              # 业务 ORM、事务和 LangGraph Store
|   |   |-- redis/                 # 锁、停止信号和 Pub/Sub
|   |   |-- milvus/                # file_chunks 向量存储
|   |   |-- minio/                 # 对象存储客户端
|   |   `-- neo4j/                 # 可选图存储
|   |-- prompt/                    # Triage/Executor system prompts
|   |-- tool/
|   |   |-- registry.py            # TOOL_REGISTRY 和冲突检测
|   |   |-- task_tools.py          # 后台任务创建和查询
|   |   |-- approval_tools.py      # HITL 审批和 Journal 工具
|   |   `-- ...                    # 搜索、文件、Shell、grep、tool_search
|   |-- harness/                   # 热加载能力和旧任务路径兼容导出
|   |   |-- tool_hot_reloader.py   # 工具热加载
|   |   `-- subagent_hot_reloader.py
|   |-- gateway/
|   |   |-- model_gateway.py       # 模型链、健康指标和热切换
|   |   |-- middleware.py          # 动态 GatewayModelWrapper
|   |   |-- circuit_breaker.py     # 三态熔断器
|   |   |-- health_probe.py        # 周期探活
|   |   `-- status_events.py       # SSE 降级状态桥接
|   |-- service/                   # 旧业务路径兼容导出
|   |-- storage/                   # 旧基础设施路径兼容导出
|   |-- stores/                    # 旧 Store 路径兼容导出
|   |-- rag/                       # 旧 RAG 路径兼容导出
|   |-- document/                  # 旧文档路径兼容导出
|   |-- readers/                   # MinerU 等文档解析器
|   |-- memory/                    # 跨会话语义记忆
|   |-- context/                   # token 统计和上下文压缩工具
|   |-- skill/                     # 旧 SkillManager 路径兼容导出
|   |-- skills/                    # 只读内置 SKILL.md
|   |-- evaluation/                # 在线/离线 Agent 和 RAG 评估
|   |-- common/                    # LLM、Redis、日志、异常、SSE、Langfuse
|   |-- config/settings.py         # Pydantic Settings
|   |-- auth.py                    # 匿名身份和 JWT
|   |-- utils/                     # 图片识别、通用工具及旧文档工具兼容层
|   `-- static/                    # 由 FastAPI 托管的前端静态资源
`-- tests/
    |-- unit/                      # 无外部系统
    |-- contract/                  # API、SSE 和兼容契约
    |-- integration/               # PostgreSQL 等真实基础设施
    `-- e2e/                       # 部署级冒烟测试
```

## 扩展开发

### 新增 Specialist

在 `app/subagents/<directory>/AGENT.md` 创建定义：

```markdown
---
name: my_specialist
description: 专门处理某类任务的领域代理
allowed_tools: tavily_search, read_file
---

# 工作流程

1. 分析任务。
2. 调用获准工具执行。
3. 返回可复核结果。
```

`allowed_tools` 使用逗号分隔名称，不要使用 YAML 数组语法。保存后
`SubAgentHotReloader` 会触发 Triage/Executor 重建。

### 新增 Tool

在 `app/tool/` 创建 Python 模块：

```python
from langchain_core.tools import tool

from app.tool.registry import register_tool


@register_tool
@tool
async def my_search(query: str) -> str:
    return f"搜索结果: {query}"
```

模块会在 `app.tool` 初始化时自动发现。运行中修改文件后，ToolHotReloader 会更新
注册表并重建 Agent；跨模块工具重名会被拒绝。

### 新增运行时 Skill

内置 Skill 在 `app/skills/<name>/SKILL.md` 创建带 `name` 和 `description` 的
frontmatter；它随镜像发布并保持只读。用户上传的 zip 写入
`DATA_DIR/MANAGED_SKILLS_DIR`，SkillManager 将两类定义同步到 `agentx_skill`。

### 测试

```bash
uv run pytest -m unit
uv run pytest -m contract
uv run pytest -m integration       # 需要先执行 Alembic 并启动 PostgreSQL
```

当前测试集共收集 115 项：无外部依赖的 unit + contract 共 106 项，PostgreSQL
integration 共 9 项。最终本地回归为 115 项全部通过；CI 会创建独立 PostgreSQL、
执行 Alembic 后分别运行两组测试。

质量与迁移检查：

```bash
uv run ruff check app tests migrations
uv run mypy app/config app/bootstrap app/infrastructure/reliability.py app/modules/identity
uv run alembic upgrade head --sql
```

离线评估入口：

```bash
python -m app.evaluation.offline_eval_agent
python -m app.evaluation.offline_eval_rag
```

## 当前边界

- `GraphRAGService` 已实现，但尚未接入默认 RAG 主链路。
- 上下文压缩工具已存在，但尚未注入当前 DeepAgent 中间件链。
- 任务重启只支持恢复 `waiting_human`；运行中的任务会标记取消。
- Milvus 当前使用共享 `file_chunks` Collection，并按文件 ID 做逻辑过滤，不是物理多租户隔离。
- 业务 Repository 使用用例级同步 SQLAlchemy 事务，异步 API 统一在线程池调用；LangGraph 使用独立异步连接池。
- Skills 管理和网关管理接口尚无独立管理员鉴权。
- 当前密码哈希是轻量 SHA-256 方案，生产部署应迁移到 Argon2id 等密码哈希算法。

## License

Internal use.
