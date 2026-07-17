# APIs-Agent — 企业级多智能体协作系统

基于 **FastAPI + deepagents + LangGraph** 构建的多智能体平台。采用 Triage/Executor 双层 DeepAgent 架构，SubAgent 声明式注册，LLM 自路由分流。

## 核心特性

- **双层 DeepAgent 架构** — TriageAgent（分流）+ ExecutorAgent（后台执行），启动时创建单例，热加载原子替换
- **原生 SubAgent 机制** — deepagents 框架内置 `SubAgentMiddleware`，LLM 通过 `task` 工具原生 spawn 子代理，独立 tools + 独立上下文
- **声明式扩展** — 在 `agent/specialist/<name>/AGENT.md` 中声明 name/description/allowed_tools，系统自动发现注入
- **LLM 自路由** — 不依赖外部分类器，LLM 通过 Function Calling 自主判断：直接回答 / spawn Specialist / 创建后台任务
- **能力前缀** — 前端通过 `生成ppt:` / `深度研究:` / `分析文档:` 等前缀传递能力选择
- **模型网关 + 熔断器** — 多模型注册、三态熔断、后台健康探活、零停机热切换
- **后台任务引擎** — 完整的 TaskExecutor：submit/cancel/HITL 挂起恢复/审批兜底/死信重试
- **RAG 检索流水线** — 查询重写 → 多路召回 + RRF 融合 → 动态 TopK → LLM 相关性过滤
- **语义长期记忆** — PgVector 跨会话记忆，自动向量化检索历史偏好
- **Skills 管理体系** — DB 生命周期管理 + zip 上传 + 启用/禁用 + 定时同步
- **知识图谱增强** — Neo4j Graph RAG（可选，未配置自动降级）
- **多层降级容错** — PostgreSQL/Redis/MinIO/Milvus/Neo4j 不可用时自动降级
- **轻量多租户** — 匿名可用 + 注册登录 + JWT 认证 + 匿名会话迁移

## 技术栈

| 层级 | 技术 |
|------|------|
| 框架 | FastAPI + Uvicorn |
| Agent 编排 | deepagents (create_deep_agent + SubAgentMiddleware) |
| 状态持久化 | LangGraph (AsyncPostgresSaver + AsyncPostgresStore) |
| LLM | OpenAI 兼容 API (DeepSeek V4 / GPT-4o / Qwen 等) |
| 搜索引擎 | Tavily Search API (MCP 集成) |
| 向量数据库 | Milvus 2.4+ |
| 关系数据库 | PostgreSQL 16+ (SQLAlchemy 2.0 + asyncpg + psycopg) |
| 知识图谱 | Neo4j (可选) |
| 对象存储 | MinIO |
| 缓存/锁 | Redis |
| 文档解析 | pdfplumber + python-docx + MinerU (可选) |
| 可观测性 | Langfuse 追踪 + trace_id 全链路日志 |
| 评估 | deepEval |

## 快速开始

### 环境要求

- Python 3.11+
- PostgreSQL 16+（必需）
- Redis（可选，不配置则降级为本地模式）
- MinIO（可选，不配置则降级为本地文件存储）
- Milvus 2.4+（可选，不配置则跳过向量检索）
- Neo4j（可选，不配置则跳过知识图谱）

### 1. 安装依赖

```bash
git clone <repo-url>
cd apis-agent

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

```bash
# 必需：LLM API Key
LLM_API_KEY="sk-your-api-key-here"
LLM_BASE_URL="https://api.openai.com/v1"
LLM_MODEL="gpt-4o"

# 必需：Tavily 搜索
TAVILY_API_KEY="tvly-your-tavily-key-here"

# 必需：PostgreSQL
PG_HOST=127.0.0.1
PG_PORT=5432
PG_USER=postgres
PG_PASSWORD=123456
PG_DB=apis_agent

# 可选：Redis
REDIS_HOST=127.0.0.1
REDIS_PORT=6379

# 可选：MinIO
MINIO_HOST=127.0.0.1
MINIO_PORT=9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=apis

# 可选：Milvus
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530

# 可选：Neo4j 知识图谱
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
```

> 完整配置项见 `.env.example`。

### 3. 初始化数据库

```bash
psql -d apis_agent -f sql/apis_agent_pg.sql
```

### 4. 启动服务

```bash
python app/main.py
```

- API 文档：http://localhost:8080/docs
- 前端界面：http://localhost:8080/

## 架构总览

```
POST /api/v1/agent/chat  {"message":"生成ppt: AI趋势"}
       │
       ▼
┌──────────────────────────────┐
│  CORS + RateLimit + 日志 + 追踪 │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  request.app.state.agent      │  ← 启动时创建的单例 Triage DeepAgent
│  (CompiledStateGraph)         │
└──────────────┬───────────────┘
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
  直接回答   spawn子代理   后台任务
  (工具调用)  (task工具)   (create_background_task)
               │              │
               ▼              ▼
        SubAgentMiddleware   TaskExecutor
        (独立 tools+prompt)  (Executor DeepAgent)
                                 │
                                 ▼
                            HITL 审批
                         (interrupt/resume)
```

### 启动流程 (lifespan)

```
1. 构建 ModelGateway → 注册主模型 + 降级模型 + 健康探活
2. PG Store 初始化 → checkpointer + store (AsyncPostgresSaver + AsyncPostgresStore)
3. MinIO 初始化
4. 扫描 agent/specialist/*/AGENT.md → 构建 SubAgent 列表
5. create_triage_agent() → app.state.agent (单例)
6. create_executor_agent() → app.state.executor_agent (单例)
7. TaskExecutor 注入 executor_agent
8. 启动 EventBus / DeadLetter / 热加载 / Skills 同步
```

### 请求处理流程

```
1. RateLimit 中间件检查
2. 解析 ChatRequest → message, conversationId, fileIds, online
3. 解析能力前缀 (生成ppt:/深度研究:/等)
4. 获取 app.state.agent (单例)
5. 构造 config = {thread_id, recursion_limit}
6. agent.astream_events() → SSE 流式输出
7. 保存会话 → agentx_session
8. 首次对话 → LLM 生成标题
9. 异步存储语义记忆
```

### 热加载流程

```
tool/*.py 变更
  → ToolHotReloader → importlib.reload() → TOOL_REGISTRY 更新
  → _rebuild_agents() → 重新构建 Agent → app.state.agent = new_agent

specialist/*/AGENT.md 变更
  → SubAgentHotReloader → _rebuild_agents() → 原子替换

进行中的请求不受影响（已持有旧 Agent 引用）
```

## API 端点

所有 API 前缀 `/api/v1`，统一使用 POST + JSON body（文件上传除外）。

### Agent

| 端点 | 用途 |
|------|------|
| `/api/v1/agent/chat` | 统一对话入口（唯一） |
| `/api/v1/agent/pptx/download` | PPT 文件下载 |
| `/api/v1/agent/stop` | 停止运行中的 Agent |
| `/api/v1/agent/shell/confirm` | Shell 命令安全确认 |
| `/api/v1/agent/task/status` | 后台任务状态查询 |
| `/api/v1/agent/task/stream` | 后台任务 SSE 进度 |
| `/api/v1/agent/task/cancel` | 取消后台任务 |
| `/api/v1/agent/task/list` | 后台任务列表 |
| `/api/v1/agent/feedback` | 用户反馈 |
| `/api/v1/agent/admin/gateway` | 网关状态查询 |
| `/api/v1/agent/admin/gateway/switch` | 模型热切换 |

### 会话

| 端点 | 用途 |
|------|------|
| `/api/v1/session` | 创建新会话 |
| `/api/v1/session/list` | 会话列表（分页） |
| `/api/v1/session/detail` | 会话详情（含消息） |
| `/api/v1/session/delete` | 删除会话 |

### 文件

| 端点 | 用途 |
|------|------|
| `/api/v1/file/list` | 文件列表 |
| `/api/v1/file/upload` | 文件上传（multipart） |
| `/api/v1/file/info` | 文件元数据 |
| `/api/v1/file/content` | 文件文本内容 |
| `/api/v1/file/delete` | 删除文件 |

### 认证

| 端点 | 用途 |
|------|------|
| `/api/v1/auth/register` | 注册 |
| `/api/v1/auth/login` | 登录 |
| `/api/v1/auth/sync` | 匿名会话迁移 |
| `/api/v1/auth/me` | 当前用户信息 |

### Skills

| 端点 | 用途 |
|------|------|
| `/api/v1/skills` | Skills 列表 |
| `/api/v1/skills/upload` | 上传 Skill zip |
| `/api/v1/skills/{name}/toggle` | 启用/禁用 |
| `/api/v1/skills/{name}` | 删除 Skill |

## 项目结构

```
apis-agent/
├── pyproject.toml
├── .env.example
│
├── app/
│   ├── main.py                     # Uvicorn 启动入口
│   │
│   ├── api/                        # HTTP 传输层
│   │   ├── main.py                 # FastAPI app + lifespan + 中间件
│   │   └── routes/
│   │       ├── agent.py            # 统一对话入口 + 任务/反馈/网关
│   │       ├── session.py          # 会话 CRUD
│   │       ├── file.py             # 文件 CRUD
│   │       ├── auth_routes.py      # 注册/登录/同步
│   │       ├── skill_routes.py     # Skills 管理
│   │       └── middleware/
│   │           └── rate_limit.py   # 滑动窗口限流
│   │
│   ├── agent/                      # Agent 编排层
│   │   ├── agent_factory.py        # create_triage_agent / create_executor_agent
│   │   ├── middleware.py           # ToolRetry / ToolCallLimit / ModelRetry
│   │   ├── base_agent.py           # BaseAgent (锁/历史/压缩)
│   │   ├── triage_agent.py         # TriageAgent (后台任务包装)
│   │   ├── executor_agent.py       # ExecutorAgent (后台任务执行)
│   │   └── specialist/             # SubAgent 声明式定义 (AGENT.md)
│   │       ├── ppt/
│   │       ├── research/
│   │       ├── file_analysis/
│   │       ├── data_analysis/
│   │       ├── code_review/
│   │       └── coding/
│   │
│   ├── gateway/                    # 模型网关
│   │   ├── model_gateway.py        # 注册/路由/熔断/探活/热切换
│   │   ├── middleware.py           # GatewayModelWrapper
│   │   ├── circuit_breaker.py      # 三态熔断器
│   │   ├── health_probe.py         # 后台健康探活
│   │   └── types.py                # CircuitState / HealthRecord / ModelRole
│   │
│   ├── harness/                    # 子代理编排
│   │   ├── task_executor.py        # 后台任务执行引擎
│   │   ├── task_context.py         # TaskSnapshot / TaskStatus / JournalEntry
│   │   ├── event_bus.py            # EventBus (Redis Pub/Sub + 内存降级)
│   │   ├── dead_letter.py          # DeadLetterQueue
│   │   ├── subagent_discovery.py   # AGENT.md 扫描解析
│   │   ├── subagent_hot_reloader.py # SubAgent 热加载
│   │   └── tool_hot_reloader.py    # 工具热加载
│   │
│   ├── prompt/                     # System Prompts
│   │   ├── triage_prompt.py        # Triage prompt + 能力前缀
│   │   └── executor_prompt.py      # Executor prompt
│   │
│   ├── rag/                        # RAG 检索
│   │   ├── retrieval_pipeline.py
│   │   └── graph_rag.py            # 知识图谱增强
│   │
│   ├── tool/                       # LangChain Agent 工具
│   │   ├── registry.py             # TOOL_REGISTRY + @register_tool
│   │   ├── tavily_search.py
│   │   ├── bash_tool.py
│   │   ├── file_system_tools.py
│   │   ├── grep_tool.py
│   │   ├── skills_tool.py
│   │   ├── task_tools.py           # create_background_task / get_task_status
│   │   ├── approval_tools.py       # request_approval / read_task_journal
│   │   └── tool_search.py          # tool_search 元工具
│   │
│   ├── service/                    # 业务服务
│   │   ├── session_service.py
│   │   ├── file_service.py
│   │   ├── rag_service.py
│   │   └── embedding_service.py
│   │
│   ├── storage/                    # 数据访问层
│   │   ├── db.py                   # PostgreSQL 连接 (SQLAlchemy)
│   │   ├── base.py                 # BaseRepository[M] 泛型 CRUD
│   │   ├── vector_store.py         # Milvus 向量存储
│   │   └── models/
│   │       ├── ai_session.py
│   │       ├── ai_file_info.py
│   │       └── ai_ppt_inst.py
│   │
│   ├── stores/                     # 存储管理器
│   │   ├── pg_store.py             # PG checkpointer + store
│   │   └── neo4j_manager.py        # Neo4j 连接管理
│   │
│   ├── memory/                     # 记忆系统
│   │   └── semantic_memory.py      # 语义长期记忆 (PgVector)
│   │
│   ├── skill/                      # Skills 管理
│   │   └── skill_manager.py        # DB 生命周期 + zip 上传
│   │
│   ├── common/                     # 通用组件
│   │   ├── llm.py
│   │   ├── streaming.py
│   │   ├── exceptions.py
│   │   ├── response.py
│   │   ├── redis.py
│   │   ├── logger.py
│   │   ├── trace_context.py
│   │   └── langfuse_client.py
│   │
│   ├── readers/                    # 文档解析
│   │   └── mineru_reader.py        # MinerU PDF 解析
│   │
│   ├── evaluation/                 # 评估
│   │   ├── online_eval.py          # 在线评估
│   │   └── offline_eval_rag.py
│   │
│   ├── auth.py                     # JWT + 匿名用户识别
│   ├── config/settings.py
│   ├── utils/
│   └── static/                     # 前端 SPA
│
├── sql/
│   └── apis_agent_pg.sql           # PostgreSQL DDL
│
└── tests/
```

## 开发指南

### 运行测试

```bash
pytest tests/ -v
```

### 新增 Specialist 子代理

在 `app/agent/specialist/<name>/` 下创建 `AGENT.md`——无需写代码，系统自动发现：

```markdown
---
name: my_specialist
description: 专门处理某类任务的子代理
allowed_tools: [tavily_search, read_file]
---

# 工作流程
1. 分析任务需求
2. 逐步执行
3. 汇报结果

## 约束
- 中文输出
```

### 新增 Tool

在 `app/tool/` 下创建文件，使用 `@register_tool` + `@tool` 双装饰器：

```python
from langchain_core.tools import tool
from app.tool.registry import register_tool

@register_tool
@tool
async def my_search(query: str) -> str:
    """搜索互联网获取信息。"""
    return f"搜索结果: {query}"
```

热加载自动生效，无需重启。

## 关键设计决策

- **启动时单例 Agent** — Triage/Executor DeepAgent 在 lifespan 中创建，存 `app.state`，请求直接复用，不每次 new
- **deepagents 原生 SubAgent** — 通过 `SubAgentMiddleware` 注入，LLM 调用 `task` 工具时框架原生 spawn 独立子代理
- **LLM 自路由** — 不依赖规则引擎或外部分类器，LLM 通过 Function Calling 自主判断分流
- **能力前缀** — 前端拼接 `生成ppt:` / `深度研究:` 等前缀传入，后端注入对应 Specialist 的 system_prompt
- **PG 双表体系** — 业务表 (agentx_session/file/ppt_inst) + LangGraph 框架表 (checkpointer/store)
- **热加载原子替换** — 工具/Specialist 变更时重建 Agent，`app.state.agent = new`，进行中请求不受影响
- **多层降级容错** — PostgreSQL/Redis/MinIO/Milvus/Neo4j 不可用时自动降级
- **可选认证** — 匿名可用（X-Anonymous-Id），登录后 JWT 认证 + 匿名会话迁移

## License

Internal use.
