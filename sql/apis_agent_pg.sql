-- =============================================================
-- apis-agent PostgreSQL Schema
-- 数据库: apis_agent
-- =============================================================

-- LangGraph 表由框架自动创建，执行以下命令即可：
--   await checkpointer.setup()  → 创建 langgraph_checkpoints 等表
--   await store.setup()         → 创建 langgraph_store 等表

-- =============================================================
-- 1. 会话消息表
--    完整对话历史由 LangGraph checkpointer 管理
--    此表用于快速列表/搜索/导出
-- =============================================================
CREATE TABLE IF NOT EXISTS agentx_session (
    id              BIGSERIAL PRIMARY KEY,
    session_id      VARCHAR(64)  NOT NULL,
    user_id         VARCHAR(64)  NOT NULL DEFAULT '',
    question        TEXT,
    answer          TEXT,
    thinking        TEXT,
    tools           VARCHAR(500),
    reference       TEXT,
    recommend       VARCHAR(1000),
    agent_type      VARCHAR(64),
    fileid          VARCHAR(255),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_session_session_id ON agentx_session(session_id);
CREATE INDEX idx_session_user_id ON agentx_session(user_id);
CREATE INDEX idx_session_created_at ON agentx_session(created_at DESC);

COMMENT ON TABLE agentx_session IS '会话消息表。完整对话历史由 LangGraph checkpointer 管理';
COMMENT ON COLUMN agentx_session.session_id IS '会话唯一标识，对应 LangGraph thread_id';
COMMENT ON COLUMN agentx_session.user_id IS '用户 ID，多租户预留';


-- =============================================================
-- 2. 文件信息表
-- =============================================================
CREATE TABLE IF NOT EXISTS agentx_file (
    id              BIGSERIAL PRIMARY KEY,
    file_id         VARCHAR(64)  NOT NULL,
    file_name       VARCHAR(500) NOT NULL,
    file_type       VARCHAR(50),
    file_size       BIGINT,
    file_hash       VARCHAR(64),
    minio_path      VARCHAR(1000),
    extracted_text  TEXT,
    status          VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
    embed           BOOLEAN      NOT NULL DEFAULT FALSE,
    error_msg       TEXT,
    chunk_count     INT          NOT NULL DEFAULT 0,
    session_id      VARCHAR(64),
    user_id         VARCHAR(64)  NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uk_file_file_id ON agentx_file(file_id);
CREATE INDEX idx_file_session_id ON agentx_file(session_id);
CREATE INDEX idx_file_status ON agentx_file(status);
CREATE INDEX idx_file_user_id ON agentx_file(user_id, created_at DESC);

COMMENT ON TABLE agentx_file IS '文件元数据表';


-- =============================================================
-- 3. PPT 实例表
-- =============================================================
CREATE TABLE IF NOT EXISTS agentx_ppt_inst (
    id              BIGSERIAL PRIMARY KEY,
    inst_id         VARCHAR(64)  NOT NULL,
    session_id      VARCHAR(64),
    user_id         VARCHAR(64)  NOT NULL DEFAULT '',
    template_code   VARCHAR(50),
    status          VARCHAR(32)  NOT NULL DEFAULT 'INIT',
    query           TEXT,
    requirement     TEXT,
    search_info     TEXT,
    outline         TEXT,
    ppt_schema      JSONB        DEFAULT '{}',
    file_url        VARCHAR(1000),
    error_msg       TEXT,
    snapshot_json   JSONB        DEFAULT '{}',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uk_ppt_inst_id ON agentx_ppt_inst(inst_id);
CREATE INDEX idx_ppt_session_id ON agentx_ppt_inst(session_id);
CREATE INDEX idx_ppt_status ON agentx_ppt_inst(status);

COMMENT ON TABLE agentx_ppt_inst IS 'PPT 生成实例表';


-- =============================================================
-- 4. PPT 模板表
-- =============================================================
CREATE TABLE IF NOT EXISTS agentx_ppt_template (
    id              BIGSERIAL PRIMARY KEY,
    template_code   VARCHAR(50)  NOT NULL,
    template_name   VARCHAR(100) NOT NULL,
    template_desc   TEXT,
    template_schema JSONB        NOT NULL DEFAULT '{}',
    file_path       VARCHAR(500) NOT NULL,
    style_tags      VARCHAR(200),
    slide_count     INT,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uk_ppt_tpl_code ON agentx_ppt_template(template_code);

COMMENT ON TABLE agentx_ppt_template IS 'PPT 模板表';


-- =============================================================
-- 5. Skills 技能注册表
-- =============================================================
CREATE TABLE IF NOT EXISTS agentx_skill (
    id              BIGSERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    skill_path      VARCHAR(500) NOT NULL,
    description     VARCHAR(500),
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    version         VARCHAR(20)  DEFAULT '1.0.0',
    author          VARCHAR(100),
    file_name       VARCHAR(200),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uk_skill_name ON agentx_skill(name);

COMMENT ON TABLE agentx_skill IS 'Skills 技能注册表';


-- =============================================================
-- 6. 用户反馈表
-- =============================================================
CREATE TABLE IF NOT EXISTS agentx_feedback (
    id              BIGSERIAL PRIMARY KEY,
    session_id      VARCHAR(64)  NOT NULL,
    user_id         VARCHAR(64)  NOT NULL DEFAULT '',
    rating          SMALLINT     NOT NULL,
    comment         TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_feedback_session_id ON agentx_feedback(session_id);
CREATE INDEX idx_feedback_user_id ON agentx_feedback(user_id, created_at DESC);

COMMENT ON TABLE agentx_feedback IS '用户反馈表';
