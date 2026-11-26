# Architecture

Proposed MVP, not a claim of verified integration behavior.

## Components

Herdr split panes (manual setup is acceptable): CLI/navigation, Markdown viewer (Glow candidate), Claude Code discussion. Python + Typer core uses SQLite for personal state, local Markdown/JSON for snapshots, glab for GitLab access, native OS scheduler for weekday preparation. Existing local Git clones support on-demand investigation. Use uv and pytest as suggested development tooling.

The core owns discovery, event detection, queue, follow-up, state transitions, delta/diff, session mapping, publishing records and completion metrics. Claude Code owns semantic analysis, discussion and drafting. Agents invoke explicit CLI contracts rather than mutating SQLite directly.

## Persistence

- tracked_issues: unique host/project ID/IID, personal state, follow-up date/reason, last reviewed reference, activity time.
- agent_sessions: issue key, Claude session ID, stable working directory, preparation status.
- review_sessions: snapshot/start/completion timestamps and status.
- queue_items: unique session/issue key, order, outcome, reviewed timestamp.
- review_records: actual reviewed snapshot, decision summary.
- operation_logs: operation, target, result, remote reference, reconciliation status.

Store current and reviewed description/note snapshots with IDs, updated timestamps and hashes; do not overwrite last-reviewed data during sync. Persist event watermarks or event identities for idempotency. Use transactions for state changes; prevent overlapping preparation jobs and concurrent use of the same Claude session. Schema details are implementation decisions.

## Agent integration

Use Claude Code headless preparation, then interactive resume with the stored session ID and stable working directory. Verify installed CLI version and resume behavior in a spike before committing to command flags. All candidate sessions are prepared before review where possible; preparation failure leaves candidate visible with raw data/failure reason. Reanalysis uses current source even when prior session history exists.

## CLI contract (proposed)

inbox init / sync / prepare / start / status / list / current / open ISSUE / next / done ISSUE / follow-up ISSUE DATE / brief ISSUE / chat ISSUE / comment ISSUE / summary

Mutations use explicit issue identity, not a global current pane. Navigation never marks done. Validate inputs and report errors. Native scheduler invokes prepare on weekdays; manual prepare recovers missed runs. Start can reconcile candidates not prepared yet.

## Local configuration

Use an allowlist of generic project paths, host, local clone paths, timezone, weekday preparation time and bounded preparation concurrency. Reuse glab authentication. Keep runtime state under a user-owned local directory outside source control. No credentials in config examples or logs.

## Security and risks

Local storage is not local inference. Use only approved model endpoints for confidential data; protect local transcripts and snapshots. Headless preparation should have read-only tools, no write credentials/authorization path. Treat issue text as untrusted. Restrict interactive writes to explicit user intent; avoid bypass permissions. Check target and current context before write; reconcile ambiguous network results without blind retries. Do not publish real internal fixtures in this public repository.

Verify GitLab API coverage for subscription, mentions, participation and description authorship, plus pagination and permissions. Herdr automation is stretch; manual panes are enough. No FastAPI, Redis, Kubernetes, custom agent runtime or central service required.
