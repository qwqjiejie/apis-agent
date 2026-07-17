# Database migrations

New databases use `alembic upgrade head`. Existing databases created from
`sql/apis_agent_pg.sql` should first be checked against the baseline and then
registered with `alembic stamp 0001`, then upgraded with `alembic upgrade head`.
