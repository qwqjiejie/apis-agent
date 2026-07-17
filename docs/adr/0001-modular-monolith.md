# ADR 0001: Modular monolith and dependency direction

- Status: Accepted
- Date: 2026-07-17

## Context

The application combines HTTP/SSE APIs, Agent orchestration, background tasks,
document retrieval and several infrastructure adapters. Splitting services before
these boundaries stabilize would turn local calls into distributed coupling.

## Decision

Keep one deployable Python application and organize business behavior under
`app/modules`. API modules adapt protocols, modules own use cases and ports, and
`app/infrastructure` implements external-system adapters. `app/bootstrap` is the
only composition root and may depend on every layer. Reverse imports from
infrastructure into business services are forbidden.

Compatibility modules may re-export moved public symbols during migration. They
must contain no behavior and are covered by identity tests.

## Consequences

Features can be found and tested within one module, while deployment remains
simple. A future service extraction requires evidence of an operational boundary,
not only a large directory.
