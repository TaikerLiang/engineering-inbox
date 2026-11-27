# Usage

## Install

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), an authenticated `glab`, and Claude Code.

```sh
uv tool install .        # provides the `inbox` command
inbox init               # writes ~/.engineering-inbox/config.toml
# edit host and projects in that file
inbox init               # full discovery of relevant open issues
```

State lives in `~/.engineering-inbox` (override with `ENGINEERING_INBOX_HOME`). It contains issue content: keep it out of source control and out of backups you do not control.

## Configuration

```toml
host = "gitlab.example.com"
projects = ["group/project-a", "group/project-b"]  # allowlist; nothing else is queried
timezone = "Asia/Taipei"
prepare_time = "08:30"        # weekday local time used by `inbox schedule`
prepare_concurrency = 2       # parallel Claude preparations (1 to 8)
claude_command = ["claude"]   # or a wrapper, e.g. ["ccr", "code"]
agent_timeout_seconds = 600

[clones]                      # optional, for on-demand code investigation in chat
"group/project-a" = "~/src/project-a"
```

No credentials go in this file. Unknown keys are rejected.

## Daily flow

| Step | Command |
|---|---|
| Prepare (scheduled, or by hand) | `inbox prepare` |
| Start or resume today's queue | `inbox start` |
| Pull issues discovered after start into today's queue | `inbox start --rebuild` |
| Read the brief | `inbox brief ISSUE` or `glow "$(inbox brief ISSUE --path)"` |
| Discuss | `inbox chat ISSUE` |
| Save a reply draft | `inbox comment ISSUE --body-file reply.md` |
| Post the reply | `inbox comment ISSUE --body-file reply.md --publish` |
| Finish | `inbox done ISSUE --decision "..." --action "..."` |
| Defer | `inbox follow-up ISSUE 2026-12-01 --reason "..."` |
| Move on, or back | `inbox next`, `inbox prev` |
| End of day | `inbox summary` |

`ISSUE` is `group/project#IID` or the issue URL. `inbox status` shows counts, sync and preparation health. `inbox list --state deferred` shows scheduled follow-ups.

Inside `inbox chat`, Claude Code can run these commands for you. It posts only when you tell it to; the rules are in the `CLAUDE.md` of each issue directory.

If publishing reports that the issue changed since you viewed it, read the printed changes, then repeat with `--accept-stale`.

## Workspace layout (Herdr, manual)

One Herdr workspace with three panes, split by hand with your Herdr version's split keys:

| Pane | Runs | Purpose |
|---|---|---|
| Left | a shell | `inbox start`, `next`, `done`, `follow-up`, `summary` |
| Top right | `glow -p "$(inbox brief ISSUE --path)"` | Markdown brief and verbatim new comments |
| Bottom right | `inbox chat ISSUE` | Claude Code discussion for that issue |

Per issue: run `inbox current` (or `next`) on the left, then re-run the two right-hand commands with the printed issue reference. Closing the workspace loses nothing: session IDs and directories are stored, and `inbox chat ISSUE` resumes the same conversation next time. Any terminal multiplexer works the same way; the tool does not depend on Herdr.

## Scheduled preparation

```sh
inbox schedule --format launchd > ~/Library/LaunchAgents/local.engineering-inbox.prepare.plist
launchctl load ~/Library/LaunchAgents/local.engineering-inbox.prepare.plist
# or: inbox schedule --format cron      (one crontab line to add with `crontab -e`)
# or: inbox schedule --format systemd   (a service unit and a timer unit)
```

The schedule runs `inbox prepare` Monday to Friday at `prepare_time` in the machine's local time; keep the machine timezone equal to `timezone`. The scheduler needs `glab` and the Claude command on its `PATH`.

Missed run (machine off or asleep): run `inbox prepare`. `inbox start` also syncs before building the queue and lists unprepared items; those show raw context until prepared. Two preparations never overlap: the second exits with an error.

## Recovery

| Symptom | Action |
|---|---|
| `SYNC INCOMPLETE` | Fix the listed cause (login, permission, network), run `inbox sync`. Nothing was dropped. |
| Issue shows `[sync error]` | Stored data is kept; `inbox sync --full` retries it. |
| `preparation failed` on a brief | Raw context is shown; run `inbox prepare ISSUE`. |
| Publish outcome unknown | Run the same `comment --publish` again; it checks GitLab first and never posts twice. |
| Subscribed to an issue but it is missing | `inbox sync --full`. |
| Issue is tracked but `not in today's queue` | It waits for the next session by design; `inbox start --rebuild` adds it today. |

## Smoke test (synthetic data, no GitLab, no model)

Run from the repository root:

```sh
uv sync
sh examples/smoke.sh
```

The script uses a temporary home, the synthetic GitLab in `examples/gitlab.fixture.json` and `examples/fake_claude.py` in place of Claude Code. It walks through init, prepare, start, brief, publish, done, follow-up, summary and the follow-up coming due two days later, and exits non-zero if a step fails.

`adapter = "fixture"` and `ENGINEERING_INBOX_NOW` (a fixed clock) exist for this test only.
