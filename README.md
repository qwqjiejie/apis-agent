# APIs-Agent — Python 多智能体平台

基于 **FastAPI + LangChain + LangGraph** 构建的多智能体平台，提供智能问答、文件 RAG、PPT 自动生成、深度研究、技能助手等 Agent 能力。

## 核心特性

- **六 Agent 统一架构** — ChatAgent / DeepResearchAgent / PptBuilderAgent / TriageAgent / ExecutorAgent 均继承 `BaseAgent`，共享分布式锁、对话历史、上下文压缩、SSE 流式输出；子代理通过 AGENT.md 声明式注册
- **Triage 分流 + 后台任务** — TriageAgent 统一入口，简单问题直接回答，复杂任务委托 TaskExecutor 后台异步执行，支持任务状态查询与取消
- **PPT 状态机自动生成** — INIT -> SCHEMA -> OUTLINE -> CONTENT -> RENDER -> SUCCESS，断点续传 + Tavily 自动搜图 + python-pptx 渲染
- **深度研究 Plan-Execute-Critique** — 需求澄清 -> 研究规划 -> 多轮并行子任务 -> 批判评估 -> 综合报告，全流程 SSE 实时推送
- **RAG 检索流水线** — 查询分类 + 自适应重写 -> 多路召回 + RRF 融合 -> 动态 TopK 截断 -> LLM 相关性过滤
- **文件 RAG 全链路** — 上传（MIME 校验 + SHA-256 去重）-> MinIO 存储 -> 文本解析（PDF/DOCX/TXT/图片 OCR）-> 分块 -> 向量化（Milvus）-> 语义检索
- **模型网关 + 熔断器** — 多模型注册、三态熔断器（CLOSED/OPEN/HALF_OPEN）、后台健康探活、故障自动切换
- **Shell 命令安全确认** — 30+ 读命令模式自动放行，40+ 危险模式（rm/kill/pip install）需用户通过 SSE 确认后执行，120s 超时默认拒绝
- **多层降级容错** — MySQL/Redis/Milvus/MinIO 不可用时自动降级为内存/本地模式，服务不中断
- **离线评估** — 基于 deepEval 的 RAG 质量评估（Faithfulness/AnswerRelevancy/ContextualRelevancy/ContextualRecall/ContextualPrecision）

## 技术栈

| 层级 | 技术 |
|------|------|
| 框架 | FastAPI + Uvicorn |
| Agent 编排 | LangChain + LangGraph |
| LLM | OpenAI 兼容 API（GPT-4o / DeepSeek / Qwen 等） |
| 搜索引擎 | Tavily Search API |
| 向量数据库 | Milvus 2.4+ |
| 关系数据库 | MySQL 8.0+（SQLAlchemy 2.0 ORM） |
| 对象存储 | MinIO |
| 缓存/锁 | Redis |
| 文档解析 | pdfplumber + python-docx |
| PPT 生成 | python-pptx |
| 可观测性 | Langfuse 追踪 + trace_id 全链路日志 |
| 评估 | deepEval |

## 快速开始

### 环境要求

- Python 3.11+
- MySQL 8.0+（可选，不配置则降级为内存存储）
- Redis（可选，不配置则降级为本地模式）
- MinIO（可选，不配置则降级为本地文件存储）
- Milvus 2.4+（可选，不配置则跳过向量检索）

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

# 可选：MySQL（不配置则降级为内存存储）
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASS=
MYSQL_DB=apis

# 可选：Redis（不配置则降级为本地模式）
REDIS_HOST=127.0.0.1
REDIS_PORT=6379

# 可选：MinIO（不配置则降级为本地文件存储）
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin

# 可选：Milvus（不配置则跳过向量检索）
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
```

> 完整配置项见 `.env.example`。MySQL/Redis/MinIO/Milvus 均为可选依赖，缺失时自动降级。

### 3. 启动服务

```bash
python src/apis_agent/main.py
```

服务启动后访问：
- API 文档：http://localhost:8080/docs
- 前端界面：http://localhost:8080/

## 架构总览

```
HTTP 请求（POST + JSON body）
       │
       ▼
┌──────────────────────────┐
│   CORS + RateLimit + 日志  │
└──────────────────────────┘
       │
       ▼
┌──────────────────────────┐
│   全局异常处理器            │
│   ValidationError -> 400   │
│   InfrastructureError->503 │
│   Exception -> 500         │
└──────────────────────────┘
       │
       ▼
┌──────────────────────────┐
│   API 路由层 (routes/)    │
│   /api/v1/agent/*         │
│   /api/v1/session/*       │
│   /api/v1/file/*          │
└───────┬──────────────────┘
        │
        ▼
┌──────────────────────────┐
│   Agent 层 (BaseAgent)    │
│   锁/历史/压缩/保存/清理    │
└───────┬──────────────────┘
        │
    ┌───┴──────────────────┐
    ▼                      ▼
┌─────────┐    ┌───────────────────┐
│ Service │    │  LangChain        │
│ 业务服务 │    │  ReAct Agent      │
└────┬────┘    └──────┬────────────┘
     │                │
     ▼                ▼
┌─────────┐    ┌───────────────────┐
│ Storage │    │  Gateway + Tool   │
│ MySQL/  │    │  模型网关/熔断器    │
│ Milvus/ │    │  bash/search/     │
│ MinIO/  │    │  filesystem/grep  │
│ Redis   │    │  skills/rag       │
└─────────┘    └───────────────────┘
```

### 核心数据流

**1. Triage 统一入口 + 后台任务**

```
POST /api/v1/agent/chat  {"query":"...","conversationId":"..."}
  -> TriageAgent 意图判断
    -> 简单问题: ReAct Agent 直接回答
    -> 复杂任务: TaskExecutor 后台执行
      -> POST /api/v1/agent/task/stream 获取 SSE 流
      -> POST /api/v1/agent/task/status 查询状态
      -> POST /api/v1/agent/task/cancel 取消任务
```

**2. 智能问答 / 文件问答**

```
POST /api/v1/agent/chat/stream  {"query":"...","conversationId":"...","fileId":""}
  -> ChatAgent.run() -> ReAct Agent (Tavily 搜索 + 文件系统工具)
  -> SSE 流式输出: thinking -> tool_start -> tool_end -> text -> reference -> complete
```

**3. PPT 生成**

```
POST /api/v1/agent/pptx/stream  {"query":"...","conversationId":"..."}
  -> PptBuilderAgent.run() -> 状态机驱动:
    INIT (需求分析) -> SCHEMA (结构规划) -> OUTLINE (大纲生成)
    -> CONTENT (内容填充 + Tavily 搜图) -> RENDER (python-pptx 渲染)
    -> SUCCESS (推送下载链接)
  -> 每步 _save_inst() 持久化，支持断点续传
```

**4. 深度研究**

```
POST /api/v1/agent/deep/stream  {"query":"...","conversationId":"..."}
  -> DeepResearchAgent.run() -> Plan-Execute-Critique:
    需求澄清 -> 研究规划 -> 并行子任务 (asyncio.Semaphore 限流)
    -> 批判评估 -> 综合报告
```

**5. 文件上传 + RAG 检索**

```
POST /api/v1/file/upload  (multipart/form-data)
  -> MIME 校验 + SHA-256 去重 -> MinIO 存储 (降级本地)
  -> 文本解析 (PDF/DOCX/TXT/图片OCR)
  -> 文本分块 -> Embedding -> Milvus 向量索引

查询时:
  -> 查询分类 (FACTUAL/COMPLEX/SPECIFIC) + 自适应重写
  -> 多路召回 + RRF 融合 -> 动态 TopK -> LLM 相关性过滤
  -> 检索结果注入 LLM 上下文
```

## API 端点

所有端点统一使用 `POST + JSON body`（文件上传除外），前缀 `/api/v1`。响应格式 `{code, data, message}`。

### Agent

| 端点 | 用途 |
|------|------|
| `/api/v1/agent/chat` | TriageAgent 统一入口 |
| `/api/v1/agent/chat/stream` | 智能问答 SSE 流 |
| `/api/v1/agent/file/stream` | 文件问答 SSE 流 |
| `/api/v1/agent/pptx/stream` | PPT 生成 SSE 流 |
| `/api/v1/agent/pptx/download` | PPT 文件下载 |
| `/api/v1/agent/deep/stream` | 深度研究 SSE 流 |
| `/api/v1/agent/skills/stream` | 技能助手 SSE 流 |
| `/api/v1/agent/stop` | 停止运行中的 Agent |
| `/api/v1/agent/shell/confirm` | Shell 命令安全确认 |
| `/api/v1/agent/task/status` | 后台任务状态查询 |
| `/api/v1/agent/task/stream` | 后台任务 SSE 流 |
| `/api/v1/agent/task/cancel` | 取消后台任务 |
| `/api/v1/agent/task/list` | 后台任务列表 |
| `/api/v1/agent/admin/gateway` | 网关状态与模型切换 |

### 会话

| 端点 | 用途 |
|------|------|
| `/api/v1/session` | 创建新会话 |
| `/api/v1/session/list` | 会话列表（分页，pageSize <= 100） |
| `/api/v1/session/detail` | 会话详情（含历史消息） |
| `/api/v1/session/delete` | 删除会话 |

### 文件

| 端点 | 用途 |
|------|------|
| `/api/v1/file/list` | 文件列表（分页，pageSize <= 100） |
| `/api/v1/file/upload` | 文件上传（multipart） |
| `/api/v1/file/info` | 文件元数据 |
| `/api/v1/file/content` | 文件提取文本内容 |
| `/api/v1/file/delete` | 删除文件 |
| `/api/v1/file/exists` | 检查文件是否存在 |

## 项目结构

```
apis-agent/
├── pyproject.toml                     # 项目依赖与工具配置
├── .env.example                       # 环境变量模板
│
├── src/apis_agent/
│   ├── main.py                        # Uvicorn 启动入口
│   │
│   ├── api/                           # HTTP 传输层
│   │   ├── main.py                    # FastAPI app + CORS + 中间件 + 异常处理器
│   │   ├── routes/
│   │   │   ├── agent.py               # Agent SSE 流 + Triage 入口 + 后台任务
│   │   │   ├── session.py             # 会话 CRUD
│   │   │   └── file.py                # 文件 CRUD
│   │   └── middleware/
│   │       └── rate_limit.py          # 滑动窗口限流（IP 级 + 会话级）
│   │
│   ├── agent/                         # Agent 编排层
│   │   ├── base_agent.py              # BaseAgent 抽象基类（锁/历史/压缩/清理）
│   │   ├── chat_agent.py              # ChatAgent — 聊天/文件/技能
│   │   ├── deep_research_agent.py     # DeepResearchAgent — Plan-Execute-Critique
│   │   ├── ppt_builder_agent.py       # PptBuilderAgent — 状态机 PPT 生成
│   │   ├── triage_agent.py            # TriageAgent — 统一入口分流
│   │   ├── executor_agent.py          # ExecutorAgent — 后台任务执行
│   │   └── specialist/                # 子代理声明式定义
│   │       └── research/
│   │           └── AGENT.md           # research_specialist 声明
│   │
│   ├── gateway/                       # 模型网关
│   │   ├── model_gateway.py           # 多模型注册 + 熔断路由 + 热切换
│   │   ├── circuit_breaker.py         # 三态熔断器 (CLOSED/OPEN/HALF_OPEN)
│   │   ├── health_probe.py            # 后台健康探活 (30s 间隔)
│   │   └── types.py                   # CircuitState 枚举 + HealthRecord
│   │
│   ├── harness/                       # 子代理编排
│   │   ├── task_executor.py           # 后台 Agent 生命周期管理
│   │   ├── task_context.py            # TaskSnapshot + TaskStatus 枚举
│   │   ├── subagent_discovery.py      # 从 AGENT.md 扫描注册子代理
│   │   └── tool_hot_reloader.py       # 基于 watchfiles 的工具热重载
│   │
│   ├── rag/                           # RAG 检索流水线
│   │   └── retrieval_pipeline.py      # 查询重写 -> 多路召回 + RRF -> 动态 TopK -> LLM 过滤
│   │
│   ├── document/                      # 文档处理
│   │   ├── document_status.py         # 8 状态枚举 (UPLOADING -> ... -> READY)
│   │   └── document_event_bus.py      # 处理进度 SSE 推送
│   │
│   ├── evaluation/                    # 离线评估
│   │   ├── offline_eval_rag.py        # RAG 质量评估 (5 项 deepEval 指标)
│   │   ├── offline_eval_agent.py      # Agent 行为评估
│   │   └── datasets/                  # 评估测试用例
│   │       ├── rag_datasets.json
│   │       └── agent_datasets.json
│   │
│   ├── service/                       # 业务服务层
│   │   ├── session_service.py         # 会话服务（MySQL + 内存 fallback）
│   │   ├── file_service.py            # 文件服务（上传/解析/去重/向量化）
│   │   ├── rag_service.py             # RAG 检索服务
│   │   └── embedding_service.py       # Embedding 服务
│   │
│   ├── storage/                       # 数据访问层
│   │   ├── base.py                    # BaseRepository[M] 泛型 CRUD 基类
│   │   ├── db.py                      # 数据库连接管理
│   │   ├── vector_store.py            # Milvus 向量存储
│   │   └── models/
│   │       ├── ai_session.py          # 会话模型 + Repository
│   │       ├── ai_file_info.py        # 文件信息模型 + Repository
│   │       └── ai_ppt_inst.py         # PPT 实例模型 + PptStatus 枚举 + Repository
│   │
│   ├── tool/                          # LangChain Agent 工具
│   │   ├── registry.py                # TOOL_REGISTRY 全局注册 + @register_tool 装饰器
│   │   ├── tavily_search.py           # Tavily 联网搜索
│   │   ├── bash_tool.py               # Shell 命令执行（安全确认机制）
│   │   ├── file_system_tools.py       # 文件系统读写 (read/write/edit/list/glob)
│   │   ├── grep_tool.py               # 全文搜索
│   │   └── skills_tool.py             # 技能发现 + 动态加载
│   │
│   ├── common/                        # 通用组件
│   │   ├── llm.py                     # LLM 构建工厂 (GatewayLLM + ChatOpenAIWithReasoning)
│   │   ├── streaming.py               # SSE 事件工具 + StreamEventType 枚举
│   │   ├── tag_parser.py              # 流式标签解析器 (thinking/recommend)
│   │   ├── exceptions.py              # 统一异常层次
│   │   ├── response.py                # 统一响应格式 (ok/ok_paged/error)
│   │   ├── redis.py                   # Redis 发布订阅 + 分布式锁
│   │   ├── logger.py                  # trace_id 注入日志
│   │   ├── trace_context.py           # contextvars 跨异步边界追踪
│   │   └── langfuse_client.py         # Langfuse 追踪集成
│   │
│   ├── context/                       # 上下文管理
│   │   ├── compressor.py              # 对话历史压缩（两层策略）
│   │   └── token_counter.py           # Token 计数 (tiktoken)
│   │
│   ├── config/
│   │   └── settings.py                # pydantic-settings 配置（懒加载 + 启动校验）
│   │
│   ├── utils/                         # 纯工具函数
│   │   ├── file_parser.py             # 文件解析 + MIME 类型校验
│   │   ├── image_recognition.py       # 多模态图片识别
│   │   └── text_splitter.py           # 文本分块
│   │
│   ├── skills/                        # 用户可安装技能
│   │   └── code-reviewer/
│   │       └── SKILL.md               # code-reviewer 技能声明
│   │
│   ├── static/                        # 前端静态文件 (SPA)
│   │   ├── index.html
│   │   ├── css/style.css
│   │   └── js/
│   │       ├── api.js
│   │       ├── app.js
│   │       ├── config.js
│   │       ├── constants.js
│   │       └── utils.js
│   │
│   └── templates/                     # PPT 模板
│       └── ai.pptx
│
├── sql/                               # 数据库 DDL
│   └── ai_db.sql                      # ai_session / ai_file_info / ai_ppt_inst / ai_ppt_template
│
└── tests/                             # 测试 (70 test cases)
    ├── test_file_parser.py            # 文件解析 (26 tests)
    ├── test_text_splitter.py          # 文本分块 (10 tests)
    ├── test_token_counter.py          # Token 计数 (10 tests)
    ├── test_base_repository.py        # 存储层 CRUD (15 tests)
    └── test_session_api.py            # 会话 API 集成测试 (9 tests)
```

## 开发指南

### 运行测试

```bash
pytest tests/ -v
```

### 代码检查

```bash
ruff format . && ruff check .
mypy src/
```

### 新增 Agent

1. 在 `agent/` 下创建 `xxx_agent.py`
2. 继承 `BaseAgent`，实现 `run()` 方法
3. `run()` 返回 `AsyncGenerator[dict, None]`，使用 `make_event()` / `make_sse()` 产出 SSE 事件
4. 在 `api/routes/agent.py` 中添加路由

```python
from src.apis_agent.agent.base_agent import BaseAgent

class MyAgent(BaseAgent):
    async def run(self):
        ok, error_events = await self._try_start()
        if not ok:
            for evt in error_events:
                yield evt
            return
        try:
            yield make_event("text", content="Hello")
        finally:
            await self._cleanup()
```

### 新增 Tool

在 `tool/` 下创建文件，使用 LangChain `@tool` 装饰器：

```python
from langchain_core.tools import tool

@tool
async def my_search(query: str) -> str:
    """搜索互联网获取信息。"""
    return f"搜索结果: {query}"
```

### 新增子代理

在 `agent/specialist/<name>/` 下创建 `AGENT.md`：

```markdown
---
name: my_specialist
description: 专门处理某类任务的子代理
allowed_tools: [tavily_search, read_file]
---
## 系统提示词
...
```

## 关键设计决策

- **BaseAgent 抽象基类** — 所有 Agent 共享分布式锁（Redis SETNX + 本地降级）、对话历史加载、两层上下文压缩、状态持久化、资源清理
- **Triage 分流模式** — TriageAgent 统一入口判断意图，简单问题即时回答，复杂任务后台异步执行，避免长时间阻塞 SSE 连接
- **模型网关 + 熔断器** — 多模型注册、三态熔断（连续 5 次失败切换 OPEN，30s 冷却进入 HALF_OPEN）、后台健康探活、故障自动切换备用模型
- **RAG 检索流水线** — 查询自适应重写 -> 多路并行召回 + RRF 融合 -> 动态 TopK 截断 -> LLM 相关性过滤，减少噪声注入
- **状态机断点续传** — PPT 生成每步完成后 `_save_inst()` 持久化到 MySQL，中断后可从中断状态恢复，避免重复消耗 LLM Token
- **多层降级容错** — MySQL/Redis/MinIO/Milvus 均为可选依赖，不可用时自动降级为内存/本地模式，不因基础设施故障中断核心服务
- **Shell 安全确认** — LLM 生成的 Shell 命令按模式分类（30+ 读命令 / 40+ 危险命令），危险命令通过 SSE 推送用户确认后执行，120s 超时默认拒绝
- **统一异常处理** — 全局异常处理器按类型分级：ValidationError(400) / InfrastructureError(503) / 未知异常(500)，内部细节对客户端隐藏
- **全 POST + JSON body** — 所有 API 端点统一使用 POST 方法 + JSON 请求体（文件上传除外），Pydantic 模型自动校验
- **配置懒加载 + 启动校验** — `get_settings()` 使用 `@lru_cache` 懒加载，`model_validator` 在启动时校验 `llm_api_key` 和 `llm_base_url`
- **技能可插拔** — 技能通过 SKILL.md 文件声明式定义，`skills_tool` 动态发现加载，无需修改代码

## License

Internal use.
