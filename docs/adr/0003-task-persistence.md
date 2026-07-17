# ADR 0003: Task snapshots, Journal and recovery

- Status: Accepted
- Date: 2026-07-17

## Context

Background tasks cross request lifetimes and may pause for human approval. A
single result row cannot explain progress or recover interrupted execution.

## Decision

`app/modules/tasks` owns task snapshots, lifecycle transitions, Journal entries,
events and dead-letter retry. LangGraph PostgreSQL Store is the durable backend;
memory storage is an explicit degraded mode. Every critical snapshot or Journal
write can enter the dead-letter queue. Startup recovers non-terminal tasks and
shutdown drains before cancellation.

## Consequences

Task state is auditable and resumable. Memory degradation preserves availability
but not restart durability, so logs must clearly expose that mode.
