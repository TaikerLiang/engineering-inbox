"""#5: follow-up scheduling and re-entry policy (AC-05..AC-09, AC-15)."""
import json

from conftest import ME, MON, at, issue, note
from engineering_inbox import review
from engineering_inbox.gitlab import GitLabError

DESC = lambda i, who: note(i, who, "changed the description", system=True)


def done_world(world, until=None):
    w = world({"g/a": [issue(1)]})
    w.sync()
    w.finish("g/a#1", until=until)
    return w


def test_ordinary_comments_do_not_reenter(world):
    w = done_world(world)
    w.change("g/a#1", "2026-11-03T00:00:00Z", add_notes=[note(1, "bob", "fyi"), note(2, ME, "my own reply @me")])
    report = w.sync(at(3))
    assert report.reentered == [] and w.row("g/a#1")["state"] == "done"
    assert w.row("g/a#1")["current_snapshot_id"] != w.row("g/a#1")["reviewed_snapshot_id"]  # still fetched


def test_new_mention_reenters_done_exactly_once(world):
    w = done_world(world)
    w.change("g/a#1", "2026-11-03T00:00:00Z", add_notes=[note(1, "bob", f"@{ME} please check")])
    assert w.sync(at(3)).reentered == ["g/a#1"]
    row = w.row("g/a#1")
    assert row["state"] == "pending" and "mention by @bob" in json.loads(row["reasons"])[0]
    w.finish("g/a#1", now=at(3, 2))
    assert w.sync(at(3, 3), full=True).reentered == [] and w.row("g/a#1")["state"] == "done"
    w.change("g/a#1", "2026-11-04T00:00:00Z", add_notes=[note(2, "bob", "unrelated")])
    assert w.sync(at(4)).reentered == [] and w.row("g/a#1")["state"] == "done"


def test_mention_overrides_deferral_and_clears_follow_up(world):
    w = done_world(world, until="2026-11-20")
    w.change("g/a#1", "2026-11-03T00:00:00Z", add_notes=[note(1, "bob", f"@{ME}?")])
    w.sync(at(3))
    row = w.row("g/a#1")
    assert (row["state"], row["follow_up_date"], row["follow_up_reason"]) == ("pending", None, None)
    history = w.conn.execute("SELECT follow_up_date FROM review_records").fetchone()
    assert history["follow_up_date"] == "2026-11-20"  # history retained
    assert w.sync(at(20)).due == []  # cleared follow-up does not fire later


def test_mention_added_by_editing_a_note_reenters_once(world):
    w = done_world(world)
    w.change("g/a#1", "2026-11-03T00:00:00Z", add_notes=[note(1, "bob", "thoughts?")])
    assert w.sync(at(3)).reentered == []
    w.remote("g/a#1")["notes"][0]["body"] = f"thoughts? @{ME}"
    w.change("g/a#1", "2026-11-04T00:00:00Z")
    assert w.sync(at(4)).reentered == ["g/a#1"]


def test_mention_while_pending_is_consumed_without_duplicate_reentry(world):
    w = world({"g/a": [issue(1)]})
    w.sync()
    w.change("g/a#1", "2026-11-03T00:00:00Z", add_notes=[note(1, "bob", f"@{ME}")])
    w.sync(at(3))
    w.finish("g/a#1", now=at(3, 2))
    assert w.sync(at(4), full=True).reentered == [] and w.row("g/a#1")["state"] == "done"


def test_description_change_by_another_user_reenters(world):
    w = done_world(world)
    w.change("g/a#1", "2026-11-03T00:00:00Z", description="Body v2", add_notes=[DESC(1, "bob")])
    assert w.sync(at(3)).reentered == ["g/a#1"]
    assert json.loads(w.row("g/a#1")["reasons"]) == ["description changed by @bob"]
    w.finish("g/a#1", now=at(3, 2))
    assert w.sync(at(3, 3), full=True).reentered == []


def test_own_description_change_does_not_reenter(world):
    w = done_world(world)
    w.change("g/a#1", "2026-11-03T00:00:00Z", description="Body v2", add_notes=[DESC(1, ME)])
    assert w.sync(at(3)).reentered == [] and w.row("g/a#1")["state"] == "done"


def test_unattributable_description_change_reenters_flagged(world):
    w = done_world(world)
    w.change("g/a#1", "2026-11-03T00:00:00Z", description="Body v2")
    assert w.sync(at(3)).reentered == ["g/a#1"]
    assert "attribution uncertain" in json.loads(w.row("g/a#1")["reasons"])[0]


def test_follow_up_due_reenters_unchanged_at_local_date_boundary(world):
    w = done_world(world, until="2026-11-05")
    # Asia/Taipei is UTC+8: local 2026-11-05 begins at 2026-11-04T16:00Z.
    assert w.sync(at(4, 15, 59)).due == [] and w.row("g/a#1")["state"] == "deferred"
    assert w.sync(at(4, 16, 0)).due == ["g/a#1"]
    row = w.row("g/a#1")
    assert row["state"] == "pending" and row["follow_up_date"] is None
    assert json.loads(row["reasons"]) == ["follow-up due 2026-11-05: check back"]
    assert w.sync(at(4, 17)).due == []


def test_overdue_follow_up_reenters_even_when_gitlab_is_unreachable(world):
    w = done_world(world, until="2026-11-05")
    w.adapter.fail["current_user"] = GitLabError("down", "server")
    report = w.sync(at(9))
    assert not report.complete and report.due == ["g/a#1"] and w.row("g/a#1")["state"] == "pending"


def test_unreviewed_issue_closed_remotely_stays_visible(world):
    w = world({"g/a": [issue(1), issue(2)]})
    w.sync()
    closed = dict(state="closed", closed_at="2026-11-03T00:00:00Z", closed_by="bob")
    w.change("g/a#1", "2026-11-03T00:00:00Z", **closed)
    w.sync(at(3))  # incremental path
    w.change("g/a#2", "2026-11-03T00:00:00Z", **closed)
    w.conn.execute("DELETE FROM project_sync")  # force the full-scan path
    w.sync(at(4))
    for ref in ("g/a#1", "g/a#2"):
        row = w.row(ref)
        assert (row["state"], row["remote_state"], row["closed_by"]) == ("pending", "closed", "bob")
        assert "closed remotely by @bob" in json.loads(row["reasons"])[-1]
    session, _ = review.start(w.conn, w.cfg, at(4, 2))
    assert len(review.queue(w.conn, session["id"])) == 2


def test_failed_fetch_keeps_pending_work_and_does_not_consume_events(world):
    w = done_world(world)
    before = w.row("g/a#1")["current_snapshot_id"]
    w.change("g/a#1", "2026-11-03T00:00:00Z", add_notes=[note(1, "bob", f"@{ME}")])
    w.adapter.fail["list_notes:g/a#1"] = GitLabError("timeout", "timeout")
    report = w.sync(at(3))
    row = w.row("g/a#1")
    assert not report.complete and row["state"] == "done" and row["current_snapshot_id"] == before
    assert "timeout" in row["sync_error"]
    w.adapter.fail.clear()
    assert w.sync(at(3, 2)).reentered == ["g/a#1"] and w.row("g/a#1")["sync_error"] is None
    w.finish("g/a#1", now=at(3, 3))
    assert w.sync(at(3, 4), full=True).reentered == []
