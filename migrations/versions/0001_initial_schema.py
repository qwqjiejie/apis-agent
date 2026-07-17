"""Establish the current APIs Agent PostgreSQL schema baseline."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agentx_session",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), server_default="", nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("thinking", sa.Text(), nullable=True),
        sa.Column("tools", sa.String(500), nullable=True),
        sa.Column("reference", sa.Text(), nullable=True),
        sa.Column("recommend", sa.String(1000), nullable=True),
        sa.Column("agent_type", sa.String(64), nullable=True),
        sa.Column("fileid", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_session_session_id", "agentx_session", ["session_id"])
    op.create_index("idx_session_user_id", "agentx_session", ["user_id"])
    op.create_index("idx_session_created_at", "agentx_session", ["created_at"])

    op.create_table(
        "agentx_file",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("file_id", sa.String(64), nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("file_type", sa.String(50), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("minio_path", sa.String(1000), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="PENDING", nullable=False),
        sa.Column("embed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("user_id", sa.String(64), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uk_file_file_id", "agentx_file", ["file_id"], unique=True)
    op.create_index("idx_file_session_id", "agentx_file", ["session_id"])
    op.create_index("idx_file_status", "agentx_file", ["status"])
    op.create_index("idx_file_user_id", "agentx_file", ["user_id", "created_at"])

    op.create_table(
        "agentx_ppt_inst",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("inst_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("user_id", sa.String(64), server_default="", nullable=False),
        sa.Column("template_code", sa.String(50), nullable=True),
        sa.Column("status", sa.String(32), server_default="INIT", nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("requirement", sa.Text(), nullable=True),
        sa.Column("search_info", sa.Text(), nullable=True),
        sa.Column("outline", sa.Text(), nullable=True),
        sa.Column("ppt_schema", postgresql.JSONB(), server_default="{}", nullable=True),
        sa.Column("file_url", sa.String(1000), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("snapshot_json", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uk_ppt_inst_id", "agentx_ppt_inst", ["inst_id"], unique=True)
    op.create_index("idx_ppt_session_id", "agentx_ppt_inst", ["session_id"])
    op.create_index("idx_ppt_status", "agentx_ppt_inst", ["status"])

    op.create_table(
        "agentx_ppt_template",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("template_code", sa.String(50), nullable=False),
        sa.Column("template_name", sa.String(100), nullable=False),
        sa.Column("template_desc", sa.Text(), nullable=True),
        sa.Column("template_schema", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("style_tags", sa.String(200), nullable=True),
        sa.Column("slide_count", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uk_ppt_tpl_code", "agentx_ppt_template", ["template_code"], unique=True)

    op.create_table(
        "agentx_skill",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("skill_path", sa.String(500), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("version", sa.String(20), server_default="1.0.0", nullable=True),
        sa.Column("author", sa.String(100), nullable=True),
        sa.Column("file_name", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uk_skill_name", "agentx_skill", ["name"], unique=True)

    op.create_table(
        "agentx_feedback",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), server_default="", nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_feedback_rating"),
    )
    op.create_index("idx_feedback_session_id", "agentx_feedback", ["session_id"])
    op.create_index("idx_feedback_user_id", "agentx_feedback", ["user_id", "created_at"])

    op.create_table(
        "agentx_user",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uk_user_user_id", "agentx_user", ["user_id"], unique=True)
    op.create_index("uk_user_username", "agentx_user", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("uk_user_username", table_name="agentx_user")
    op.drop_index("uk_user_user_id", table_name="agentx_user")
    op.drop_table("agentx_user")
    op.drop_index("idx_feedback_user_id", table_name="agentx_feedback")
    op.drop_index("idx_feedback_session_id", table_name="agentx_feedback")
    op.drop_table("agentx_feedback")
    op.drop_index("uk_skill_name", table_name="agentx_skill")
    op.drop_table("agentx_skill")
    op.drop_index("uk_ppt_tpl_code", table_name="agentx_ppt_template")
    op.drop_table("agentx_ppt_template")
    op.drop_index("idx_ppt_status", table_name="agentx_ppt_inst")
    op.drop_index("idx_ppt_session_id", table_name="agentx_ppt_inst")
    op.drop_index("uk_ppt_inst_id", table_name="agentx_ppt_inst")
    op.drop_table("agentx_ppt_inst")
    op.drop_index("idx_file_user_id", table_name="agentx_file")
    op.drop_index("idx_file_status", table_name="agentx_file")
    op.drop_index("idx_file_session_id", table_name="agentx_file")
    op.drop_index("uk_file_file_id", table_name="agentx_file")
    op.drop_table("agentx_file")
    op.drop_index("idx_session_created_at", table_name="agentx_session")
    op.drop_index("idx_session_user_id", table_name="agentx_session")
    op.drop_index("idx_session_session_id", table_name="agentx_session")
    op.drop_table("agentx_session")
