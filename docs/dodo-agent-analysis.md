# Dodo-Agent 项目功能需求与架构设计分析

> 源项目: Java Spring AI + Spring Boot 3.2.x 多智能体平台
> 源路径: `LLMentor/agent/dodo-agent`

---

## 一、功能需求清单

### 1. 核心对话功能

| 编号 | 功能 | 状态 | 描述 |
|------|------|------|------|
| F-01 | 智能问答 (WebSearch) | 已完成 | 基于 ReAct 模式的联网搜索 + 推理，实时获取互联网信息后回答用户问题；调用 Tavily MCP 搜索引擎 |
| F-02 | 文件问答 (File RAG) | 已完成 | 上传 PDF/DOC/DOCX/TXT/图片文件后进行语义问答；支持全文直读和 Milvus 向量检索两种模式 |
| F-03 | PPT 生成 (PPT Builder) | 未实现 | 基于模板驱动的 PPT 自动生成，含意图识别(CREATE/MODIFY/RESUME)、Schema 生成、大纲生成、内容填充、AI 配图、多格式渲染(Python-pptx / PptxGenJS) |
| F-04 | 深度研究 (Deep Research) | 已完成 | Plan-Execute-Critique 多轮自主研究：需求澄清 -> 研究主题生成 -> 多轮执行 (并行子任务 + 依赖传递) -> 批判评估 -> 综合报告。前端实时展示各阶段进展、子任务搜索过程和研究报告流式输出 |
| F-05 | 技能助手 (Skills) | 已完成 | 通用型 ReAct Agent，集成搜索/文件/Skills/文件系统/Bash/Grep 等所有工具，LLM 自动判断使用哪个工具 |

### 2. 会话管理功能

| 编号 | 功能 | 状态 | 描述 |
|------|------|------|------|
| F-06 | 会话 CRUD | 已完成 | 分页查询会话列表、查询会话详情、删除会话（级联删除关联的 file_info 和 ppt_inst） |
| F-07 | 对话历史持久化 | 已完成 | 用户问题和 AI 回答（含 thinking/工具调用/参考来源/推荐问题）存储到 MySQL |
| F-08 | 跨会话记忆恢复 | 已完成 | 通过 ChatMemory 从数据库加载历史消息，支持上下文延续 |

### 3. 文件管理功能

| 编号 | 功能 | 状态 | 描述 |
|------|------|------|------|
| F-09 | 文件上传 | 已完成 | 支持 PDF/DOC/DOCX/TXT/PNG/JPG，上传到 MinIO 对象存储。本地文件系统 + MinIO 双存储，上传时自动解析文本内容 |
| F-10 | 文件解析 | 已完成 | 文本文件解析提取内容（PDFBox + POI）；大文件自动切分向量化（500 字符块 + 50 字符重叠）。PDF/DOCX/TXT 解析和向量化均已完成 |
| F-11 | 图片识别 | 已完成 | 调用多模态模型 (vision_model 可配置) 识别图片内容，图片上传时自动调用多模态 API 生成文字描述，存入 extracted_text 参与 RAG 检索 |
| F-12 | 文件 RAG 检索 | 已完成 | 查询压缩重写 -> 多查询扩展 -> Milvus 语义检索 -> 去重合并 |
| F-13 | 文件删除/查询 | 已完成 | 删除文件和 MinIO 对象、检查文件存在、获取文件信息。支持列表/详情/内容/删除/存在检查 |

### 4. Agent 控制功能

| 编号 | 功能 | 状态 | 描述 |
|------|------|------|------|
| F-14 | 任务停止 | 已完成 | 用户可随时停止正在执行的 Agent，支持 asyncio.Event 本地直停 + Redis Pub/Sub 跨实例广播。Redis 不可用时自动降级为纯本地模式 |
| F-15 | 任务并发控制 | 已完成 | 基于 Redis 分布式锁 (SETNX) 的会话级任务去重，同一会话不允许同时执行多个 Agent。Redis 不可用时降级为本地字典判断 |
| F-16 | 优雅中断 | 已完成 | asyncio task 封装 agent 执行，取消时 CancelledError 传播到 langgraph 内部自动清理子任务。bash_tool 改用 asyncio subprocess，取消时 kill 子进程 |

### 5. 流式输出功能

| 编号 | 功能 | 状态 | 描述 |
|------|------|------|------|
| F-17 | SSE 流式响应 | 已完成 | 所有 Agent 统一使用 SSE (text/event-stream) 输出 |
| F-18 | 思考过程展示 | 已完成 | `reasoning_content` 字段（DeepSeek 等推理模型）+ `<think>` 标签双重解析，思考内容（thinking）和最终答案（text）分离输出。通过 `ChatOpenAIWithReasoning` 子类注入 `additional_kwargs`，解决 langchain-openai 丢弃非标准字段的问题 |
| F-19 | 工具调用追踪 | 已完成 | tool_start / tool_end 事件实时展示工具调用状态 |
| F-20 | 参考来源 | 已完成 | reference 类型消息，展示搜索结果的 URL/标题 |
| F-21 | 推荐问题 | 已完成 | 每轮对话结束自动生成 3 个推荐问题 |

### 6. 上下文管理功能

| 编号 | 功能 | 状态 | 描述 |
|------|------|------|------|
| F-22 | 上下文压缩 (Layer 1) | 已完成 | 保留最近 N 轮完整内容，旧搜索工具结果替换为占位符，长回答截断。仅对搜索类工具(SEARCH_RESULTS/SOURCES)做占位符，文件 RAG 场景保留 |
| F-23 | 上下文压缩 (Layer 2) | 已完成 | Token 超过 max_context_tokens * 75% 时触发 LLM 摘要压缩。后台异步执行（`asyncio.create_task`），不阻塞主流程首 token 延迟 |
| F-24 | Token 估算 | 已完成 | 基于 tiktoken o200k_base 编码估算消息列表 token 数，支持 OpenAI 格式消息和自定义模型 |

### 7. 工具集功能

| 编号 | 功能 | 状态 | 描述 |
|------|------|------|------|
| F-25 | Tavily 搜索 | 已完成 | 通过 MCP 协议接入 Tavily 搜索引擎，自动解析搜索结果 |
| F-26 | Bash 工具 | 已完成 | 持久化 Shell 会话，执行系统命令，支持超时和输出限制 |
| F-27 | 文件系统工具 | 已完成 | read_file / write_file / edit_file / list_files / glob_files |
| F-28 | Grep 工具 | 已完成 | 正则表达式内容搜索，优先使用 ripgrep，回退到 Python 原生 |
| F-29 | Skills 工具 | 已完成 | 从本地目录加载 SKILL.md，注册为可调用的工具 |

---

## 二、架构设计总结

### 2.1 分层架构

```
┌─────────────────────────────────────────────────────┐
│                   Controller 层                      │
│  AgentController / FileController / SessionController│
├─────────────────────────────────────────────────────┤
│                     Agent 层                          │
│  BaseAgent(抽象基类)                                  │
│  ├── WebSearchReactAgent   (ReAct + 搜索)            │
│  ├── FileReactAgent        (ReAct + RAG)             │
│  ├── PPTBuilderAgent       (状态机模式)              │
│  ├── PlanExecuteAgent      (Plan-Execute-Critique)   │
│  └── SkillsReactAgent      (通用 ReAct + 全工具)     │
├─────────────────────────────────────────────────────┤
│                    Service 层                         │
│  AiSessionService / FileManageService /              │
│  EmbeddingService / PptPythonRenderService /         │
│  AgentTaskManager / FileParserService                │
├─────────────────────────────────────────────────────┤
│                     Tool 层                           │
│  TavilyMCP / BashTool / FileSystemTools /            │
│  GrepTool / FileContentService / SkillsTool          │
├─────────────────────────────────────────────────────┤
│                 Infrastructure 层                     │
│  MySQL / PgVector / MinIO / Redis / Tavily           │
│  OpenAI/通义千问 / MCP                              │
└─────────────────────────────────────────────────────┘
```

### 2.2 核心设计模式

#### 2.2.1 BaseAgent 抽象基类模式

所有 Agent 继承 `BaseAgent`，统一提供：
- **ChatMemory 管理**: 持久化记忆加载/保存
- **任务控制**: 注册/停止/并发检查 (AgentTaskManager)
- **响应协议**: 统一的 SSE JSON 格式 (AgentResponse)
- **计时器**: 首次响应时间/总响应时间
- **工具追踪**: 使用工具集合记录
- **推荐问题**: 基于对话上下文的自动推荐生成

```java
public abstract class BaseAgent {
    protected final ChatModel chatModel;
    protected ChatMemory chatMemory;
    protected AiSessionService sessionService;
    protected AgentTaskManager taskManager;

    public abstract Flux<String> execute(String conversationId, String question);
}
```

#### 2.2.2 ReAct 模式 (WebSearch / File / Skills)

思想链 + 行动循环：
1. LLM 接收 system prompt + 历史消息 + 当前问题
2. LLM 可选择输出文本(最终答案)或触发 ToolCall
3. 工具并行执行，结果回传消息列表
4. 下一轮 LLM 调用基于新上下文继续
5. 达到最大轮次时强制总结输出

关键技术点：
- **ThinkTagParser**: 实时解析 `<think>` 标签，分离思考内容和最终回答。Python 版通过 `ChatOpenAIWithReasoning` 子类额外支持 `reasoning_content` 字段（DeepSeek 等推理模型原生输出），覆盖更多模型格式
- **并发工具执行**: 使用 Schedulers.boundedElastic() 并行调度多工具
- **工具响应排序**: ConcurrentHashMap + 原始顺序重组，保证工具响应顺序正确
- **流式错误恢复**: `onErrorResume` 实现 LLM 调用失败重试（最多 3 次）

#### 2.2.3 Plan-Execute-Critique 模式 (Deep Research)

四阶段深度研究流程：

```
Phase 1: 需求澄清 → Phase 2: 研究主题生成 → Phase 3: 循环执行 → Phase 4: 综合报告
                                              ├── Plan (任务规划)
                                              ├── Execute (并行/串行执行)
                                              └── Critique (自我批判)
```

关键技术点：
- **Semaphore 并发控制**: 默认 3 个并发，防止工具调用过载
- **依赖传递**: 按 order 分组，同 order 并行，不同 order 串行，依赖上下文按 order-1 传递
- **CountDownLatch 同步**: 等待同 order 所有任务完成后再进入下一批
- **SimpleReactAgent 委托**: 每个子任务内部又是一个独立的 ReAct Agent
- **Double-Layer Compaction**: 上下文超阈值触发 LLM 摘要压缩

#### 2.2.4 状态机模式 (PPT Builder)

```
INIT(意图识别) → SCHEMA(结构生成) → OUTLINE(大纲生成) → SEARCH(搜索配图)
    → CONTENT(内容填充) → IMAGE(AI配图) → RENDER(渲染) → SUCCESS(完成)
```

关键技术点：
- **策略模式**: 每个状态对应一个 PptStateStrategy 实现
- **断点重连**: 状态持久化到 MySQL (ai_ppt_inst 表)，中断后可从任意状态恢复
- **意图识别**: CREATE_PPT / MODIFY_PPT / RESUME_PPT 三意图路由
- **双渲染引擎**: Python-pptx + PptxGenJS

### 2.3 项目文件结构

```
src/main/java/cn/hollis/llm/mentor/agent/
├── DodoAgentApplication.java          # Spring Boot 入口
├── agent/                             # Agent 层
│   ├── BaseAgent.java                 # 抽象基类
│   ├── websearch/WebSearchReactAgent.java   # 联网搜索 Agent
│   ├── file/FileReactAgent.java       # 文件问答 Agent
│   ├── deepresearch/
│   │   ├── PlanExecuteAgent.java      # 深度研究 Agent
│   │   └── SimpleReactAgent.java      # 简单 React Agent (子任务委托)
│   ├── pptx/
│   │   ├── PPTBuilderAgent.java       # PPT 生成 Agent
│   │   ├── PptIntentRecognizer.java   # 意图识别器
│   │   └── strategy/                  # 状态策略 (7个策略类)
│   └── skills/
│       ├── SkillsReactAgent.java      # 技能助手 Agent
│       └── manual/                    # 手动 Skills 实现
├── controller/                        # REST 控制器
│   ├── AgentController.java           # Agent API (SSE流式)
│   ├── FileController.java            # 文件管理 API
│   └── SessionController.java         # 会话管理 API
├── service/                           # 业务服务层
│   ├── AgentTaskManager.java          # 任务管理 (Redis分布式锁)
│   ├── AiSessionService.java          # 会话服务
│   ├── FileManageService.java         # 文件管理 (上传/解析/向量化)
│   ├── EmbeddingService.java          # RAG 检索 (查询压缩+扩展+语义搜索)
│   ├── FileParserService.java         # 文件解析 (PDF/Word)
│   ├── MinioService.java              # MinIO 对象存储
│   ├── PptPythonRenderService.java    # PPT Python 渲染
│   └── impl/                          # 服务实现
├── tool/                              # 工具层
│   ├── BashTool.java                  # Shell 命令执行
│   ├── FileSystemTools.java           # 文件系统 (read/write/edit/list/glob)
│   ├── GrepTool.java                  # 正则搜索
│   ├── FileContentService.java        # 文件内容加载 (RAG/全文)
│   ├── SkillsTool.java                # 技能加载
│   ├── ToolMergeUtils.java            # 工具合并
│   └── ShellSessionManager.java       # Shell 会话管理
├── context/                           # 上下文管理
│   ├── ContextPolicy.java             # 压缩策略配置
│   ├── ContextCompactor.java          # 上下文压缩器 (双层)
│   └── TokenEstimator.java            # Token 估算
├── entity/                            # 实体和VO
│   ├── AiSession.java                 # 会话实体
│   ├── AiFileInfo.java                # 文件信息实体
│   ├── vo/                            # 视图对象
│   └── record/                        # 记录类型 (AgentState/RoundState/等)
├── prompts/                           # Prompt 模板
│   ├── ReactAgentPrompts.java         # ReAct Agent 提示词
│   ├── PlanExecutePrompts.java        # Plan-Execute 提示词
│   └── PptBuilderPrompts.java         # PPT 生成提示词
├── common/                            # 通用组件
│   ├── BaseResult.java                # 统一响应结果
│   ├── AgentResponse.java             # Agent SSE 响应格式
│   └── AgentStreamEvent.java          # 流式事件模型
├── config/                            # 配置类
│   ├── CorsConfig.java
│   ├── RedisConfig.java
│   ├── VectorStoreConfig.java
│   └── MinioClientConfiguration.java
├── utils/                             # 工具类
│   ├── ThinkTagParser.java            # <think> 标签解析
│   ├── HtmlRenderService.java         # HTML 渲染
│   ├── ImageGenerationService.java    # AI 图片生成
│   └── DynamicPgVectorStoreFactory.java
└── mapper/                            # MyBatis 数据访问层
```

### 2.4 API 接口汇总

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/agent/chat/stream` | 智能问答 (SSE流式) |
| GET | `/agent/file/stream` | 文件问答 (SSE流式) |
| GET | `/agent/pptx/stream` | PPT 生成 (SSE流式) |
| GET | `/agent/deep/stream` | 深度研究 (SSE流式) |
| GET | `/agent/skills/stream` | 技能助手 (SSE流式) |
| GET | `/agent/stop` | 停止 Agent 执行 |
| POST | `/file/upload` | 上传文件 |
| GET | `/file/info/{fileId}` | 获取文件信息 |
| GET | `/file/content/{fileId}` | 获取文件内容 |
| DELETE | `/file/{fileId}` | 删除文件 |
| GET | `/file/list` | 获取所有文件列表 |
| GET | `/file/exists/{fileId}` | 检查文件是否存在 |
| GET | `/session/{conversationId}` | 查询会话详情 |
| GET | `/session/list` | 分页查询会话列表 |
| DELETE | `/session/{conversationId}` | 删除会话 |

### 2.5 SSE 流式响应协议

```json
{"type":"thinking","content":"正在分析问题..."}
{"type":"text","content":"这是最终回答内容"}
{"type":"reference","content":"[{\"title\":\"来源1\",\"url\":\"...\"}]","count":3}
{"type":"recommend","content":"[\"相关问题1\",\"相关问题2\"]"}
{"type":"error","content":"错误信息"}
```

### 2.6 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 基础框架 | Spring Boot + Spring AI | 3.2.x |
| Agent 框架 | Spring AI Alibaba Agent Framework | 1.1.0.0 |
| 响应式 | Project Reactor (Flux/Mono) | 3.6.x |
| 大模型 | OpenAI API / 通义千问 | Compatible Mode |
| 向量数据库 | PostgreSQL + pgvector | - |
| 关系数据库 | MySQL | 8.0 |
| ORM | MyBatis-Plus | 3.5.5 |
| 对象存储 | MinIO | 8.5.1 |
| 缓存/分布式锁 | Redis + Redisson | 3.24.3 |
| 工具协议 | MCP (Model Context Protocol) | 1.0 |
| 文件解析 | PDFBox 2.0.30 / POI 5.2.5 | - |
| 浏览器引擎 | Playwright | 1.45.0 |
| JSON | FastJSON2 | 2.0.43 |

### 2.7 关键设计特点

1. **多 Agent 架构**: 5 种 Agent 模式覆盖不同场景（ReAct / RAG+ReAct / StateMachine / Plan-Execute / General ReAct）
2. **分层抽象**: Controller -> Agent -> Service -> Tool -> Infrastructure，职责清晰
3. **统一协议**: 所有 Agent 共享 SSE 流式响应格式和 BaseAgent 基类
4. **分布式就绪**: Redis 分布式锁实现跨实例任务互斥和 Pub/Sub 停止广播
5. **上下文管理**: 双层压缩策略（Layer 1 占位符截断 + Layer 2 LLM 摘要），Layer 2 后台异步执行不阻塞首 token。
6. **断点续传**: PPT 状态机支持任意状态中断后恢复
7. **并发控制**: Semaphore 控制深度研究的工具调用并发度
8. **插件化工具**: MCP 协议实现工具热插拔，Skills 体系支持动态加载

---

## 三、H5 前端页面

H5 前端页面已复制到 `src/main/resources/static/` 目录下：

- `index.html` - Vue 3 单页应用，含会话列表、聊天界面、智能体选择器、文件上传预览等
- `css/style.css` - 深色科技感主题，渐变色彩、发光效果、响应式布局
- `js/config.js` - 后端 API 地址配置
- `js/constants.js` - 常量定义（智能体列表、文件类型、流式消息类型）
- `js/utils.js` - 工具函数（Markdown 渲染、参考来源处理、推荐问题处理）
- `js/api.js` - API 调用封装（文件上传、会话管理、SSE 流式请求、停止执行）
- `js/app.js` - Vue 3 主应用逻辑（消息处理、SSE 解析、DOM 更新、智能体切换）

### 前端技术栈

- Vue 3 CDN (Options API)
- Marked.js (Markdown 渲染)
- Highlight.js (代码语法高亮)
- DOMPurify (XSS 防护)
- Font Awesome 6 (图标库)

---

## 四、实现进度汇总

| 分类 | 已完成 | 部分实现 | 未实现 | 合计 |
|------|--------|----------|--------|------|
| 核心对话功能 | 4 (F-01~02, F-04~05) | 0 | 1 (F-03) | 5 |
| 会话管理功能 | 3 (F-06~08) | 0 | 0 | 3 |
| 文件管理功能 | 5 (F-09~13) | 0 | 0 | 5 |
| Agent 控制功能 | 3 (F-14~16) | 0 | 0 | 3 |
| 流式输出功能 | 5 (F-17~21) | 0 | 0 | 5 |
| 上下文管理功能 | 3 (F-22~24) | 0 | 0 | 3 |
| 工具集功能 | 5 (F-25~29) | 0 | 0 | 5 |
| **合计** | **28** | **0** | **1** | **29** |

**进度: 28/29 已完成 (97%)**
