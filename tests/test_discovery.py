"""#3: allowlisted, deduplicated, failure-visible discovery (AC-01, AC-02, AC-15)."""
import json
import subprocess

import pytest

from conftest import ME, at, issue, note
from engineering_inbox.gitlab import GitLabError, GlabAdapter
from engineering_inbox.sync import mentions


def test_five_relevance_conditions_enter_once(world):
    w = world({"g/a": [
        issue(1, assignees=[ME]),
        issue(2, assignees=[], author=ME),
        issue(3, assignees=[], notes=[note(10, "bob", f"ping @{ME}")]),
        issue(4, assignees=[], notes=[note(11, ME, "my view")]),
        issue(5, assignees=[], subscribed=True),
        issue(6, assignees=[]),
        issue(7, assignees=[ME], state="closed"),
    ]})
    report = w.sync()
    assert report.complete and report.full is False
    rows = {r["iid"]: json.loads(r["relevance"]) for r in w.conn.execute("SELECT * FROM tracked_issues")}
    assert rows == {1: ["assignee"], 2: ["author"], 3: ["mentioned"], 4: ["participant"], 5: ["subscriber"]}
    assert w.sync(at(3)).new == [] and w.sync(at(4), full=True).new == []


def test_overlapping_conditions_and_repeat_sync_deduplicate(world):
    w = world({"g/a": [issue(1, author=ME, assignees=[ME], subscribed=True, notes=[note(1, ME, "x")])]})
    w.sync(); w.sync(at(3), full=True)
    rows = w.conn.execute("SELECT * FROM tracked_issues").fetchall()
    assert len(rows) == 1 and json.loads(rows[0]["relevance"]) == ["assignee", "author", "participant", "subscriber"]


def test_only_allowlisted_projects_are_queried(world):
    w = world({"g/a": [issue(1)]})
    w.data["projects"]["g/secret"] = {"id": 99, "issues": [issue(1)]}
    w.sync()
    assert not any("g/secret" in call for call in w.adapter.calls)
    assert [r["project_path"] for r in w.conn.execute("SELECT * FROM tracked_issues")] == ["g/a"]


def test_same_iid_in_two_projects_is_two_issues(world):
    w = world({"g/a": [issue(1)], "g/b": [issue(1)]})
    w.sync()
    assert w.conn.execute("SELECT COUNT(*) FROM tracked_issues").fetchone()[0] == 2


def test_partial_failure_is_visible_and_recovers(world):
    w = world({"g/a": [issue(1)], "g/b": [issue(2)]})
    w.adapter.fail["list_issues:g/b"] = GitLabError("403 Forbidden", "permission")
    report = w.sync()
    assert not report.complete and "g/b (permission)" in report.errors[0]
    assert report.new == ["g/a#1"]
    assert w.conn.execute("SELECT complete FROM sync_runs ORDER BY id DESC").fetchone()[0] == 0
    w.adapter.fail.clear()
    report = w.sync(at(3))
    assert report.complete and report.new == ["g/b#2"]


def test_authentication_failure_reports_incomplete(world):
    w = world({"g/a": [issue(1)]})
    w.adapter.fail["current_user"] = GitLabError("401 Unauthorized", "auth")
    report = w.sync()
    assert not report.complete and "auth" in report.errors[0]


def test_failed_note_fetch_for_new_candidate_is_retried(world):
    w = world({"g/a": [issue(1)]})
    w.adapter.fail["list_notes:g/a#1"] = GitLabError("timeout", "timeout")
    assert not w.sync().complete
    w.adapter.fail.clear()
    assert w.sync(at(3)).new == ["g/a#1"]  # watermark did not advance past the failure


def test_incremental_sync_skips_unchanged_and_full_rescans(world):
    w = world({"g/a": [issue(1), issue(2, assignees=[])]})
    w.sync()
    w.adapter.calls.clear()
    w.sync(at(3))
    assert w.adapter.calls == ["current_user", "list_issues:g/a"]
    w.remote("g/a#2")["subscribed"] = True  # subscription does not bump updated_at
    assert w.sync(at(4)).new == [] and w.sync(at(5), full=True).new == ["g/a#2"]


@pytest.mark.parametrize("body,expected", [
    ("hi @me", True), ("@me: look", True), ("(@me)", True), ("cc @ME.", True),
    ("@meow", False), ("mail me@me.com", False), ("`@me`", False), ("```\n@me\n```", False), ("@me-too", False),
])
def test_mention_parsing(body, expected):
    assert mentions(body, "me") is expected


def _glab(pages: dict[int, object]):
    def run(cmd, **_):
        page = int(cmd[2].split("page=")[-1]) if "page=" in cmd[2] else 1
        result = pages.get(page, [])
        if isinstance(result, tuple):
            return subprocess.CompletedProcess(cmd, 1, "", result[0])
        return subprocess.CompletedProcess(cmd, 0, json.dumps(result), "")
    return run


def _raw(iid):
    return {"project_id": 1, "iid": iid, "title": "t", "description": "", "state": "opened", "author": {"username": "bob"},
            "assignees": [], "updated_at": "2026-11-01T00:00:00Z", "web_url": f"https://h/g/a/-/issues/{iid}"}


def test_glab_adapter_paginates_all_pages():
    adapter = GlabAdapter("h", runner=_glab({1: [_raw(i) for i in range(100)], 2: [_raw(i) for i in range(100, 130)]}))
    assert len(adapter.list_issues("g/a", "opened")) == 130


def test_glab_adapter_reports_incomplete_pagination():
    adapter = GlabAdapter("h", runner=_glab({1: [_raw(i) for i in range(100)], 2: ("glab: 500 Internal Server Error (HTTP 500)",)}))
    with pytest.raises(GitLabError) as exc:
        adapter.list_issues("g/a", "opened")
    assert exc.value.kind == "incomplete"


@pytest.mark.parametrize("stderr,kind", [
    ("glab: 401 Unauthorized (HTTP 401)", "auth"), ("glab: 403 Forbidden (HTTP 403)", "permission"), ("glab: 404 Not Found (HTTP 404)", "not_found"),
])
def test_glab_adapter_classifies_failures(stderr, kind):
    with pytest.raises(GitLabError) as exc:
        GlabAdapter("h", runner=_glab({1: (stderr,)})).current_user()
    assert exc.value.kind == kind and exc.value.ambiguous is False


def test_glab_write_failure_ambiguity():
    def timeout(cmd, **_):
        raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(GitLabError) as exc:
        GlabAdapter("h", runner=timeout).create_note("g/a", 1, "x")
    assert exc.value.ambiguous is True
    with pytest.raises(GitLabError) as exc:
        GlabAdapter("h", runner=_glab({1: ("glab: 403 Forbidden (HTTP 403)",)})).create_note("g/a", 1, "x")
    assert exc.value.ambiguous is False
    with pytest.raises(GitLabError) as exc:
        GlabAdapter("h", runner=_glab({1: ("glab: 502 Bad Gateway (HTTP 502)",)})).create_note("g/a", 1, "x")
    assert exc.value.ambiguous is True
