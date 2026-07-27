# Domain Docs

AI-SCDC uses a single domain context.

## Before exploring or changing code

Read:

- root `CONTEXT.md`;
- the ADRs under `docs/adr/` that affect the work;
- the originating GitHub specification and ticket.

If these documents conflict, stop and surface the conflict rather than silently choosing one.

## Vocabulary

Use the terms defined in `CONTEXT.md` in issue titles, tests, APIs, implementation notes, and reviews.

Do not replace glossary terms with synonyms that `CONTEXT.md` explicitly marks as avoided.

If implementation requires a domain concept that is missing or ambiguous, update the domain model through the appropriate design workflow before inventing terminology in code.

## Architectural decisions

Treat relevant ADRs as active constraints. If a ticket appears to contradict an ADR, stop and request confirmation before implementation.

## Testing seams

Use only the public test seams confirmed by the applicable specification. Tests should verify observable behavior and must not bind themselves to private helpers, internal service-call order, or physical storage layout.
