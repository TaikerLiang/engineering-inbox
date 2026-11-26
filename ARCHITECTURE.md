# Architecture

Implemented MVP. Behavior rules are in [WORKFLOW.md](WORKFLOW.md); this file maps them to code.

## Components

Python 3.11+ with Typer, SQLite (stdlib), glab for GitLab access, Claude Code for analysis and discussion, the native OS scheduler for weekday preparation. Tooling: uv, pytest. Herdr panes are set up by hand. No server, no custom agent runtime.

| Module (`src/engineering_inbox/`) | Owns |
|---|---|
| `config.py` | TOML configuration, home directory, clock |
| `db.py` | Schema, connection, `tx` transaction helper |
| `gitlab.py` | `GlabAdapter` (reads + one write), `FixtureAdapter` (synthetic), error classification |
| `sync.py` | Discovery, relevance, snapshot ingestion, re-entry events, due follow-ups |
| `delta.py` | Snapshot hashing, reviewed-versus-current delta, Markdown rendering |
| `review.py` | Issue identity, state transitions, daily queue |
| `agent.py` | Session mapping, locks, headless preparation, briefs, chat command |
| `publish.py` | Drafts, authorised publishing, reconciliation |
| `summary.py` | Daily summary, usage records |
| `schedule.py` | launchd / cron / systemd definitions |
| `cli.py` | `inbox` commands |

The core decides everything deterministic. Claude Code only analyses and drafts, and changes state only by calling the CLI.

## Persistence

SQLite at `<home>/inbox.db`; per-issue files at `<home>/issues/<host>/<project>/<iid>/` (`brief.md`, `issue.md`, `delta.md`, `CLAUDE.md`). Home is `~/.engineering-inbox` or `$ENGINEERING_INBOX_HOME`, outside source control.

| Table | Content |
|---|---|
| `tracked_issues` | Unique host/project ID/IID, personal state, follow-up, reasons, current/observed/reviewed snapshot pointers, sync error |
| `snapshots` | Immutable description + notes per content change |
| `events` | Consumed event identities, unique per issue |
| `review_sessions`, `queue_items` | One session per local date, fixed membership, outcomes, cursor, usage record |
| `review_records` | Outcome, reviewed snapshot, decision, action items, follow-up history |
| `operation_logs` | Comment drafts and publish attempts with status and URL |
| `agent_sessions` | Claude session ID, working directory, preparation status and the snapshots it was built from |
| `project_sync`, `sync_runs`, `meta` | Watermarks, sync completeness, last preparation |

Constraints: valid states only, Deferred if and only if a follow-up date exists, one active session. Every state change is one `BEGIN IMMEDIATE` transaction.

## Agent integration

- Preparation: `<claude_command> -p --output-format json --tools "" --strict-mcp-config` with `--session-id <uuid>` on first use and `--resume <uuid>` afterwards, run in the issue directory with the prompt on stdin.
- Chat: `<claude_command> --resume <uuid>` in the same directory, plus `--add-dir <clone>` when a local clone is configured. The directory's `CLAUDE.md` carries the review rules.
- `claude_command` is configurable for wrappers.
- If the stored created/resumed flag is wrong, the call switches mode once (`already in use` / `No conversation found`).

Verified with Claude Code 2.1.332 (CLI mechanics only, no authenticated model call): `--session-id` creates a session that `--resume` finds, also from another directory; reusing `--session-id` fails with `already in use`; an unknown ID fails with `No conversation found`; JSON output carries `is_error`, `result`, `session_id`. Model output quality and wrapper pass-through are unverified.

## CLI

`inbox init | sync [--full] | prepare [ISSUE] | start | status | list | current | open ISSUE | next | done ISSUE | follow-up ISSUE DATE | brief ISSUE | chat ISSUE | comment ISSUE | summary | usage | schedule`

`ISSUE` is `group/project#IID` or the issue URL. Exit code 0 success, 1 rejected operation, 2 completed with incomplete sync or failed preparation.

## Security

- Local storage is not local inference: issue text is sent to whatever endpoint `claude_command` uses. Use only an approved endpoint for confidential data.
- Preparation has no tools, so issue text cannot trigger a write. Issue text is marked untrusted in the prompt and in the session rules.
- The only GitLab write is `create_note`, reached only through `comment --publish`.
- Configuration holds no credentials; glab authentication is reused. Unknown configuration keys are rejected.
- Snapshots, briefs and transcripts under the home directory contain issue content: protect that directory. Never commit it or real fixtures to this public repository.
