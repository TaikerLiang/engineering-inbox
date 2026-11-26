"""#6: daily queue snapshot and carry-over (AC-10, AC-11, AC-16)."""
import pytest

from conftest import ME, MON, at, issue, note
from engineering_inbox import review
from engineering_inbox.gitlab import GitLabError
from engineering_inbox.review import ReviewError
from engineering_inbox.sync import last_run


def order(w, session):
    return [(i["project_path"], i["iid"]) for i in review.queue(w.conn, session["id"])]


def test_queue_is_unique_and_sorted_newest_first_with_stable_tie_break(world):
    w = world({
        "g/b": [issue(2, updated_at="2026-11-01T05:00:00Z"), issue(1, updated_at="2026-11-01T05:00:00Z")],
        "g/a": [issue(9, updated_at="2026-11-01T05:00:00Z"), issue(3, updated_at="2026-11-01T09:00:00Z")],
    })
    w.sync(); w.sync(at(2, 2), full=True)
    session, how = review.start(w.conn, w.cfg, at(2, 3))
    # g/b is project 1, g/a is project 2: ties break by project ID, then IID.
    assert how == "created" and order(w, session) == [("g/a", 3), ("g/b", 1), ("g/b", 2), ("g/a", 9)]
    assert review.current(w.conn)["iid"] == 3 and w.row("g/a#3")["state"] == "reviewing"


def test_membership_is_frozen_and_new_events_wait(world):
    w = world({"g/a": [issue(1)]})
    w.sync()
    session, _ = review.start(w.conn, w.cfg, MON)
    w.data["projects"]["g/a"]["issues"].append(issue(2, updated_at="2026-11-02T02:00:00Z"))
    assert w.sync(at(2, 3)).new == ["g/a#2"]
    assert order(w, review.start(w.conn, w.cfg, at(2, 4))[0]) == [("g/a", 1)]
    with pytest.raises(ReviewError, match="next session"):
        review.open_issue(w.conn, w.row("g/a#2"))
    review.done(w.conn, w.cfg, w.row("g/a#1"), at(2, 5))
    # A mention after completion re-enters the issue but not today's finished queue.
    w.change("g/a#1", "2026-11-02T06:00:00Z", add_notes=[note(1, "bob", f"@{ME}")])
    w.sync(at(2, 7))
    session, how = review.start(w.conn, w.cfg, at(2, 8))
    assert how == "already_completed" and w.row("g/a#1")["state"] == "pending"
    tomorrow, how = review.start(w.conn, w.cfg, at(3))
    assert how == "created" and order(w, tomorrow) == [("g/a", 1), ("g/a", 2)]


def test_next_navigates_without_completing(world):
    w = world({"g/a": [issue(1, updated_at="2026-11-01T03:00:00Z"), issue(2, updated_at="2026-11-01T02:00:00Z"), issue(3, updated_at="2026-11-01T01:00:00Z")]})
    w.sync()
    session, _ = review.start(w.conn, w.cfg, MON)
    assert [review.next_issue(w.conn)["iid"] for _ in range(3)] == [2, 3, 1]  # wraps
    assert all(i["outcome"] is None for i in review.queue(w.conn, session["id"]))
    assert {w.row(f"g/a#{i}")["state"] for i in (1, 2, 3)} == {"reviewing"}
    review.done(w.conn, w.cfg, w.row("g/a#2"), MON)
    assert review.next_issue(w.conn)["iid"] == 3  # skips resolved items
    assert review.progress(w.conn, review.active_session(w.conn)) == {"total": 3, "resolved": 1, "inbox_zero": False}


def test_inbox_zero_requires_every_item_done_or_deferred(world):
    w = world({"g/a": [issue(1), issue(2, updated_at="2026-10-01T00:00:00Z")]})
    w.sync()
    session, _ = review.start(w.conn, w.cfg, MON)
    review.done(w.conn, w.cfg, w.row("g/a#1"), MON)
    assert review.active_session(w.conn) is not None
    review.open_issue(w.conn, w.row("g/a#2"))
    review.follow_up(w.conn, w.cfg, w.row("g/a#2"), MON, "2026-11-10")
    finished = review.session_for(w.conn, w.cfg.today(MON))
    assert finished["status"] == "completed" and review.progress(w.conn, finished)["inbox_zero"]
    assert w.row("g/a#2")["remote_state"] == "opened"  # Inbox Zero is not GitLab closure


def test_empty_queue_is_complete_but_incomplete_sync_is_reported_separately(world):
    w = world({"g/a": [issue(1)]})
    w.adapter.fail["list_issues:g/a"] = GitLabError("503", "server")
    w.sync()
    session, how = review.start(w.conn, w.cfg, MON)
    assert how == "created" and session["status"] == "completed" and review.queue(w.conn, session["id"]) == []
    assert last_run(w.conn)["complete"] == 0  # the CLI warns from this; zero items is not proof of zero work


def test_unfinished_items_carry_over_and_resort_next_day(world):
    w = world({"g/a": [issue(1, updated_at="2026-11-01T03:00:00Z"), issue(2, updated_at="2026-11-01T02:00:00Z"), issue(3, updated_at="2026-11-01T01:00:00Z")]})
    w.sync()
    monday, _ = review.start(w.conn, w.cfg, MON)
    review.done(w.conn, w.cfg, w.row("g/a#1"), MON)
    review.next_issue(w.conn)  # issue 2 is now Reviewing, issue 3 still Pending
    w.change("g/a#3", "2026-11-02T10:00:00Z", add_notes=[note(1, "bob", "update")])
    w.data["projects"]["g/a"]["issues"].append(issue(4, updated_at="2026-11-02T09:00:00Z"))
    w.sync(at(2, 12))
    tuesday, how = review.start(w.conn, w.cfg, at(3))
    assert how == "created" and order(w, tuesday) == [("g/a", 3), ("g/a", 4), ("g/a", 2)]
    assert w.conn.execute("SELECT status FROM review_sessions WHERE id = ?", (monday["id"],)).fetchone()[0] == "superseded"
    assert w.row("g/a#2")["state"] == "reviewing"
