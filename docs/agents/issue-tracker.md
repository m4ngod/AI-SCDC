# Issue tracker: GitHub

Issues and specifications for this repository live in GitHub Issues at `m4ngod/AI-SCDC`. Use the authenticated `gh` CLI for issue and pull-request operations.

## Conventions

- Read an issue together with its complete body, comments, labels, and state.
- Create multiline issues and comments through a body file or another encoding-safe mechanism.
- Apply and remove labels through GitHub rather than maintaining local issue-state files.
- Treat `ready-for-agent` as permission to claim a fully specified ticket only after all blockers are complete.
- Do not close or rewrite a parent specification while implementing a child ticket.
- Resolve a bare issue or pull-request number against GitHub before acting.

## Blocking relationships

Use GitHub native issue dependencies when available. Otherwise, record each blocking issue under a `Blocked by` section using explicit `#<number>` references.

A ticket is on the frontier only when:

- it is open;
- every referenced blocker is closed and its implementation is present in the selected baseline;
- it is not already assigned or being implemented elsewhere.

## Implementation workflow

For each implementation ticket:

1. Read the ticket, comments, parent specification, domain context, and relevant ADRs.
2. Verify every blocker is complete and present in the selected baseline.
3. Create a dedicated `codex/<issue-number>-<slug>` branch from that baseline.
4. Implement only the ticket's acceptance criteria.
5. Validate and review the branch.
6. Commit, push, create a pull request, and merge only when the active task explicitly authorizes those actions.

## Pull requests as a triage surface

PRs as a request surface: no.

External pull requests are not treated as feature requests by the triage workflow.
