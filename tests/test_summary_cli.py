"""#10, #11: summary, usage records, schedule output and an end-to-end CLI run on synthetic data."""
import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import ME, MON, at, issue, note
from engineering_inbox import agent, config, publish, review, schedule, summary
from engineering_inbox.cli import app
from engineering_inbox.gitlab import GitLabError


def test_summary_separates_published_from_drafts_and_lists_unfinished(world):
    w = world({"g/a": [issue(1), issue(2, updated_at="2026-10-01T00:00:00Z"), issue(3, updated_at="2026-09-01T00:00:00Z")]})
    w.sync()
    review.start(w.conn, w.cfg, MON)
    agent.brief(w.conn, w.cfg, w.row("g/a#1"), MON)
    publish.publish(w.conn, w.cfg, w.row("g/a#1"), w.adapter, "Posted reply", MON)
    publish.save_draft(w.conn, w.row("g/a#1"), "Unsent idea", MON)
    review.done(w.conn, w.cfg, w.row("g/a#1"), MON, "Adopt option B", ["Update the runbook"])
    review.open_issue(w.conn, w.row("g/a#2"))
    review.follow_up(w.conn, w.cfg, w.row("g/a#2"), MON, "2026-11-09", "after release")
    text = summary.daily(w.conn, w.cfg, w.cfg.today(MON))
    published, not_published = text.split("## Not published")
    assert "#note_1" in published and "draft" in not_published and "#note_1" not in not_published
    assert "Adopt option B" in text and "Update the runbook" in text and "2026-11-09 (after release)" in text
    assert "reviewed 2/3" in text and "Inbox Zero: no" in text
    assert "[g/a#3](https://gitlab.example.com/g/a/-/issues/3) (pending)" in text.split("## Unfinished")[1]
    assert "No review session" in summary.daily(w.conn, w.cfg, w.cfg.today(at(5)))


def test_summary_reports_incomplete_sync(world):
    w = world({"g/a": [issue(1)]})
    w.adapter.fail["list_issues:g/a"] = GitLabError("503", "server")
    w.sync()
    review.start(w.conn, w.cfg, MON)
    assert "Sync INCOMPLETE" in summary.daily(w.conn, w.cfg, w.cfg.today(MON))


def test_usage_report_records_five_day_metrics(world):
    w = world({"g/a": [issue(1)]})
    w.sync()
    session, _ = review.start(w.conn, w.cfg, MON)
    review.done(w.conn, w.cfg, w.row("g/a#1"), at(2, 1, 20))
    summary.record_usage(w.conn, review.session_for(w.conn, w.cfg.today(MON)), 0, 1, "slow context recovery")
    line = summary.usage_report(w.conn).splitlines()[-1]
    assert line == "| 2026-11-02 | completed | 20 | 1 | 1 | 0 | 1 | slow context recovery |"


@pytest.mark.parametrize("fmt,needle", [("cron", "30 8 * * 1-5"), ("systemd", "OnCalendar=Mon..Fri 08:30"), ("launchd", "<key>Weekday</key><integer>5</integer>")])
def test_schedule_runs_prepare_on_weekdays_at_configured_time(world, fmt, needle):
    w = world({"g/a": []})
    text = schedule.render(w.cfg, fmt, "/usr/local/bin/inbox")
    assert needle in text and "/usr/local/bin/inbox" in text and "prepare" in text and str(w.cfg.home) in text


def test_config_validation(tmp_path):
    base = {"host": "gitlab.example.com", "projects": ["g/a"]}
    assert config.from_dict(base, tmp_path).claude_command == ("claude",)
    for bad in ({"projects": ["g/a"]}, {**base, "host": "https://x/y"}, {**base, "projects": []}, {**base, "timezone": "Mars/Base"},
                {**base, "prepare_time": "25:00"}, {**base, "prepare_concurrency": 0}, {**base, "token": "x"}):
        with pytest.raises(config.ConfigError):
            config.from_dict(bad, tmp_path)


def test_cli_end_to_end_with_synthetic_gitlab_and_fake_claude(tmp_path, monkeypatch):
    home = tmp_path / "home"
    fixture = tmp_path / "gitlab.json"
    fixture.write_text(json.dumps({"user": ME, "projects": {"g/a": {"id": 1, "issues": [
        issue(1, notes=[note(1, "bob", f"@{ME} can you confirm?")]), issue(2, updated_at="2026-10-01T00:00:00Z")]}}}))
    claude = Path(__file__).parent.parent / "examples" / "fake_claude.py"
    monkeypatch.setenv(config.HOME_ENV, str(home))
    monkeypatch.setenv(config.NOW_ENV, "2026-11-02T01:00:00+00:00")
    run = lambda *args: CliRunner().invoke(app, list(args))

    assert "Edit host and projects" in run("init").output
    (home / "config.toml").write_text(
        f'host = "gitlab.example.com"\nprojects = ["g/a"]\ntimezone = "Asia/Taipei"\nadapter = "fixture"\n'
        f'fixture_path = "{fixture}"\nclaude_command = ["{sys.executable}", "{claude}"]\n')
    assert "2 new" in run("init").output
    result = run("prepare")
    assert result.exit_code == 0 and "prepared 2/2" in result.output
    assert "[ ]  1. g/a#1" in run("start").output
    assert "## Suggested next actions" in run("brief", "g/a#1").output
    assert "NOT published" in run("comment", "g/a#1", "--body", "Draft only").output
    assert "#note_2" in run("comment", "g/a#1", "--body", "Confirmed.", "--publish").output
    assert run("done", "g/a#2").exit_code == 1  # still pending: must be opened first
    assert run("done", "g/a#1", "--decision", "Confirmed scope").exit_code == 0
    assert "g/a#2" in run("next").output
    assert "Inbox Zero" in run("follow-up", "g/a#2", "2026-11-04", "--reason", "wait").output
    text = run("summary").output
    assert "Confirmed scope" in text and "#note_2" in text and "draft" in text
    assert run("usage", "--missed-follow-ups", "0", "--missed-issues", "0", "--friction", "none").exit_code == 0
    assert "2026-11-02 | completed" in run("usage").output
    assert "pending 0, reviewing 0, deferred 1, done 1" in run("status").output
    monkeypatch.setenv(config.NOW_ENV, "2026-11-04T01:00:00+00:00")
    assert "[ ]  1. g/a#2" in run("start").output  # follow-up due re-enters
    assert "No changes since the last review" in run("brief", "g/a#2").output  # fast path, no Claude call
    assert "Weekday" in run("schedule").output
