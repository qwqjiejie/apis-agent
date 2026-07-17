#!/bin/sh
set -eu

uv run alembic upgrade head
exec uv run python -m app.main
