# Validation

Nothing in this file has been performed yet. Record results here; never paste real issue content.

## 1. Integration checks (read-only, on the workstation)

Replace `HOST`, `GROUP%2FPROJECT` (URL-encoded path) and `IID`.

| # | Check | Command | Expected | Result |
|---|---|---|---|---|
| 1 | glab auth | `glab api user --hostname HOST` | JSON with `username` | |
| 2 | Open issue list, paging | `glab api "projects/GROUP%2FPROJECT/issues?state=opened&scope=all&per_page=100&page=1" --hostname HOST` | JSON array; page 2 returns the rest or `[]` | |
| 3 | `subscribed` on single issue | `glab api projects/GROUP%2FPROJECT/issues/IID --hostname HOST` | `subscribed` is true for an issue you subscribed to | |
| 4 | Notes including system notes | `glab api "projects/GROUP%2FPROJECT/issues/IID/notes?sort=asc&per_page=100" --hostname HOST` | Comments and `system: true` notes | |
| 5 | Description attribution | Same as 4, on an issue whose description was edited | A system note whose body starts with `changed the description`, authored by the editor | |
| 6 | `updated_after` filter | Check 2 with `state=all&updated_after=<ISO time>` | Only issues updated since then, closed ones included | |
| 7 | Error text | Check 3 with a project you cannot access | Non-zero exit; stderr contains `404` or `403` | |
| 8 | Headless session | In an empty directory: `echo 'Reply OK' \| <claude_command> -p --output-format json --tools "" --strict-mcp-config --session-id $(uuidgen)` | JSON with `is_error: false` and a `result` | |
| 9 | Resume | Same with `--resume <that id>` and a question about the previous turn | The answer shows the earlier turn is remembered | |
| 10 | Wrapper pass-through | Checks 8 and 9 through the configured `claude_command` | Same results | |
| 11 | End to end | `inbox init`, `inbox prepare`, `inbox start`, `inbox brief ISSUE` on one low-risk project | Relevant issues only; the brief has all sections and links | |

If a check fails, open a ticket naming the check number. The matching rule is in WORKFLOW.md under Decisions.

## 2. Agent output review (synthetic fixtures)

Point `fixture_path` at a copy of `examples/gitlab.fixture.json`, keep `adapter = "fixture"`, set the real `claude_command`, run `inbox init` and `inbox prepare`, then review each brief by hand.

| Fixture | Scenario | Sections complete | Facts linked | Fact / inference / recommendation separated | Conflicts and open questions correct | No instruction from issue text followed |
|---|---|---|---|---|---|---|
| platform/ingest#1 | Disagreement, direct question to you | | | | | |
| platform/ingest#2 | Own issue, one open point | | | | | |
| platform/ingest#3 | Subscribed, measurement update | | | | | |
| platform/ingest#4 | Own earlier commitment | | | | | |
| platform/ingest#5 | Comment containing an injected instruction | | | | | |
| platform/query#1 | Mention only | | | | | |

## 3. Five working days of use

After each review: `inbox usage --missed-follow-ups N --missed-issues N --friction "..."`. At the end, `inbox usage` prints the table; paste it here.

| Date | Status | Minutes | Candidates | Reviewed | Missed follow-ups | Missed issues | Friction |
|---|---|---|---|---|---|---|---|

30 minutes is a target, not a release gate. Follow-up reliability and queue correctness are hard goals: any missed follow-up or missed eligible issue gets a ticket.

## 4. Findings and follow-up tickets

None yet.
