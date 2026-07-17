# ADR 0002: Process-owned model gateway

- Status: Accepted
- Date: 2026-07-17

## Context

Agent execution and administration previously risked observing different model
gateway instances, making health and circuit-breaker state unreliable.

## Decision

The FastAPI lifespan creates one `ModelGateway` and stores it in
`ApplicationContainer`. Triage, Executor, health probes and administration APIs
all receive that instance. Model wrappers resolve routing dynamically so a switch
does not require rebuilding callers.

## Consequences

Health counters and active-model state are process consistent. Multi-process
coordination remains an infrastructure concern and must use an external control
plane if introduced later.
