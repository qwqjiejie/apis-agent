# ADR 0004: Runtime data and deployment topology

- Status: Accepted
- Date: 2026-07-17

## Context

Uploads, dynamic Skills and evaluation outputs were written below source folders.
This polluted worktrees and prevented operation from a read-only application image.

## Decision

All mutable files live below `DATA_DIR`: uploads, managed Skills, artifacts and
evaluation results. Bundled `app/skills` declarations are read-only. Local Compose
mounts a named volume at `/var/lib/apis-agent`; production uses the same path and
an immutable application image.

Business PostgreSQL access is synchronous SQLAlchemy executed off the async event
loop with one transaction per use case. LangGraph uses its native async pool.
Alembic is the only schema-change mechanism. PostgreSQL, Redis, MinIO and Milvus
are required in the full local topology; Neo4j remains an optional profile.

## Consequences

Source can be mounted read-only and runtime cleanup is isolated. Operators must
back up PostgreSQL, object/vector stores and the `DATA_DIR` volume separately.
