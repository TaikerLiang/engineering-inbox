#!/bin/sh
# Smoke test on synthetic data: no GitLab, no model. Run from the repository root.
set -eu

ROOT=$(pwd)
WORK=$(mktemp -d)
export ENGINEERING_INBOX_HOME="$WORK/home"
export ENGINEERING_INBOX_NOW=2026-01-05T01:00:00+00:00   # Monday 09:00 Asia/Taipei
cp examples/gitlab.fixture.json "$WORK/gitlab.json"

step() { printf '\n== %s\n' "$*"; }
inbox() { uv run -q inbox "$@"; }

step "init: write the configuration template"
inbox init
cat > "$ENGINEERING_INBOX_HOME/config.toml" <<CONFIG
host = "gitlab.example.com"
projects = ["platform/ingest", "platform/query"]
timezone = "Asia/Taipei"
adapter = "fixture"
fixture_path = "$WORK/gitlab.json"
claude_command = ["python3", "$ROOT/examples/fake_claude.py"]
CONFIG

step "init: full discovery (6 relevant issues; platform/ingest#6 is not relevant)"
inbox init | tee "$WORK/out"; grep -q "6 new" "$WORK/out"

step "prepare: one brief per issue"
inbox prepare | tee "$WORK/out"; grep -q "prepared 6/6" "$WORK/out"

step "start: fixed queue, newest activity first"
inbox start

step "brief: analysis plus verbatim comments"
inbox brief 'platform/ingest#1' | tee "$WORK/out"; grep -q "can it tell transient errors" "$WORK/out"

step "comment: draft, then explicit publish"
inbox comment 'platform/ingest#1' --body 'Draft only.'
inbox comment 'platform/ingest#1' --body 'The loader can tell them apart.' --publish

step "done, next, follow-up"
inbox done 'platform/ingest#1' --decision 'Retry transient errors only' --action 'Expose the error class in the loader'
inbox next
inbox follow-up 'platform/ingest#5' 2026-01-07 --reason 'wait for runner fix'

step "summary and status"
inbox summary | tee "$WORK/out"; grep -q "note_" "$WORK/out"
inbox status

step "two days later: the follow-up is due and nothing changed"
export ENGINEERING_INBOX_NOW=2026-01-07T01:00:00+00:00
inbox start | tee "$WORK/out"; grep -q "1 follow-ups due" "$WORK/out"
inbox brief 'platform/ingest#5' | tee "$WORK/out"; grep -q "No changes since the last review" "$WORK/out"

step "schedule definition"
inbox schedule --format cron

rm -rf "$WORK"
printf '\nSmoke test passed.\n'
