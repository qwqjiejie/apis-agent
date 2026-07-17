# APIs Agent Project Structure Blueprint

> Last updated: 2026-07-17

## Architecture

This repository is a Python modular monolith with a bundled static frontend. It is
organized by business capability above explicit infrastructure adapters. It is not
a monorepo and does not contain independently deployed microservices.

```text
apis-agent/
|-- app/
|   |-- bootstrap/             # composition root and process-owned dependencies
|   |-- api/                   # HTTP, SSE, middleware and protocol schemas
|   |-- modules/
|   |   |-- chat/              # sessions, chat persistence and feedback
|   |   |-- documents/         # upload, parsing, indexing and retrieval
|   |   |-- identity/          # tokens, users and anonymous-data migration
|   |   |-- skills/            # bundled and managed Skill lifecycle
|   |   `-- tasks/             # snapshots, Journal, execution and events
|   |-- infrastructure/
|   |   |-- postgres/          # SQLAlchemy and LangGraph PostgreSQL adapters
|   |   |-- redis/             # signals, locks and event transport
|   |   |-- milvus/            # vector storage
|   |   |-- minio/             # object storage
|   |   `-- neo4j/             # optional graph storage
|   |-- agent/                 # Triage and Executor construction
|   |-- tool/                  # Agent-facing tool adapters
|   |-- subagents/             # declarative Specialist definitions
|   |-- prompt/                # version-controlled prompt assets
|   |-- memory/                # cross-session semantic memory
|   |-- evaluation/            # evaluation code and static datasets
|   |-- config/                # typed settings and path resolution
|   |-- common/                # errors, logging, tracing and protocol helpers
|   `-- static/                # bundled read-only frontend
|-- tests/
|   |-- unit/                  # no external services
|   |-- contract/              # API, SSE and compatibility contracts
|   |-- integration/           # real infrastructure
|   `-- e2e/                   # deployed-stack smoke tests
|-- migrations/                # Alembic version history
|-- deploy/                    # runtime entrypoint and production overrides
|-- docs/adr/                  # architecture decisions
|-- Dockerfile
|-- compose.yaml
|-- pyproject.toml
`-- uv.lock
```

## Dependency Rules

```text
api -> modules -> ports
                  ^
                  |
            infrastructure

bootstrap -> all layers
```

- API code performs authentication, validation and response mapping only.
- A business module may use another module's public use case, but never its API.
- Infrastructure cannot import `app.service`, route modules or concrete business
  services. Configuration and shared error types are allowed.
- Network clients, pools, scanners and reloaders start and stop in lifespan.
- Old namespaces may only contain documented re-exports; new code never imports them.

## File Placement

| Change | Location |
| --- | --- |
| New business use case | `app/modules/<capability>/` |
| HTTP/SSE endpoint | `app/api/routes/` |
| External-system client | `app/infrastructure/<system>/` |
| Agent-callable adapter | `app/tool/` |
| Static Skill/Specialist | `app/skills/` or `app/subagents/` |
| Mutable upload/Skill/artifact | configured `DATA_DIR`, never `app/` |
| Schema change | new `migrations/versions/*.py` revision |
| Architecture decision | `docs/adr/NNNN-*.md` |

Python modules and folders use `snake_case`; classes use `PascalCase`; constants
use `UPPER_SNAKE_CASE`. Tests mirror the risk layer rather than source directory.

## Extension Template

```text
app/modules/new_capability/
|-- __init__.py
|-- models.py                  # capability-owned data structures
|-- ports.py                   # protocols required from infrastructure
|-- service.py                 # application use cases
`-- events.py                  # only when the capability publishes events

tests/unit/test_new_capability.py
tests/contract/test_new_capability_api.py
```

Register concrete dependencies in `ApplicationContainer`, adapt them in an API
route, and add migration or runtime-path changes separately from behavior changes.

## Build And Enforcement

- `uv sync --frozen --extra dev` restores the locked environment.
- Ruff checks imports and code quality; mypy checks the typed architecture core.
- Pytest markers separate unit, contract, integration and e2e suites.
- CI renders the Alembic upgrade SQL and runs PostgreSQL integration tests.
- Docker builds an immutable image; Compose supplies mutable services and volumes.

Update this blueprint whenever a top-level ownership boundary, dependency rule,
test tier or runtime-data location changes. Record the reason in an ADR first.
