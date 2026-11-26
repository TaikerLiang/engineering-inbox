"""#4: persistent state and validated transitions (AC-03, AC-04)."""
import sqlite3

import pytest

from conftest import MON, at, issue
from engineering_inbox import review
from engineering_inbox.review import ReviewError


def test_explicit_transitions(world):
    w = world({"g/a": [issue(1), issue(2, updated_at="2026-10-01T00:00:00Z")]})
    w.sync()
    assert w.row("g/a#1")["state"] == "pending"
    review.start(w.conn, w.cfg, MON)
    assert w.row("g/a#1")["state"] == "reviewing" and w.row("g/a#2")["state"] == "pending"
    review.done(w.conn, w.cfg, w.row("g/a#1"), MON, "ship it", ["write tests"])
    assert w.row("g/a#1")["state"] == "done"
    review.open_issue(w.conn, w.row("g/a#2"))
    review.follow_up(w.conn, w.cfg, w.row("g/a#2"), MON, "2026-11-09", "wait for design")
    row = w.row("g/a#2")
    assert (row["state"], row["follow_up_date"], row["follow_up_reason"]) == ("deferred", "2026-11-09", "wait for design")


def test_invalid_transitions_are_rejected_with_actionable_errors(world):
    w = world({"g/a": [issue(1), issue(2, updated_at="2026-10-01T00:00:00Z")]})
    w.sync()
    with pytest.raises(ReviewError, match="inbox start"):
        review.open_issue(w.conn, w.row("g/a#1"))
    review.start(w.conn, w.cfg, MON)
    with pytest.raises(ReviewError, match="inbox open g/a#2"):
        review.done(w.conn, w.cfg, w.row("g/a#2"), MON)
    review.done(w.conn, w.cfg, w.row("g/a#1"), MON)
    with pytest.raises(ReviewError, match="is done, not reviewing"):
        review.done(w.conn, w.cfg, w.row("g/a#1"), MON)
    review.open_issue(w.conn, w.row("g/a#2"))
    for bad in ("tomorrow", "2026-11-02", "2026-11-01"):  # not a date, today, past
        with pytest.raises(ReviewError):
            review.follow_up(w.conn, w.cfg, w.row("g/a#2"), MON, bad)
    assert w.row("g/a#2")["state"] == "reviewing"


def test_explicit_issue_identity(world):
    w = world({"g/a": [issue(1)], "g/b": [issue(1)]})
    w.sync()
    assert w.row("g/b#1")["project_path"] == "g/b"
    assert w.row("https://gitlab.example.com/g/a/-/issues/1")["project_path"] == "g/a"
    for bad in ("1", "#1", "g/c#1", "g/a#9"):
        with pytest.raises(ReviewError):
            w.row(bad)


def test_restart_preserves_state_follow_up_and_queue(world):
    w = world({"g/a": [issue(1), issue(2, updated_at="2026-10-01T00:00:00Z"), issue(3, updated_at="2026-09-01T00:00:00Z")]})
    w.sync()
    w.finish("g/a#1", until="2026-11-09")
    review.open_issue(w.conn, w.row("g/a#2"))
    w.reconnect()
    assert w.row("g/a#1")["follow_up_date"] == "2026-11-09"
    assert [w.row(f"g/a#{i}")["state"] for i in (1, 2, 3)] == ["deferred", "reviewing", "pending"]
    session, how = review.start(w.conn, w.cfg, at(2, 5))
    assert how == "resumed"
    assert [(i["iid"], i["outcome"]) for i in review.queue(w.conn, session["id"])] == [(1, "deferred"), (2, None), (3, None)]
    assert review.current(w.conn)["iid"] == 2


def test_failure_rolls_back_the_whole_transition(world, monkeypatch):
    w = world({"g/a": [issue(1)]})
    w.sync()
    review.start(w.conn, w.cfg, MON)

    def boom(*_):
        raise RuntimeError("disk full")
    monkeypatch.setattr(review, "_complete_if_zero", boom)
    with pytest.raises(RuntimeError):
        review.done(w.conn, w.cfg, w.row("g/a#1"), MON, "decision")
    assert w.row("g/a#1")["state"] == "reviewing"
    assert w.conn.execute("SELECT COUNT(*) FROM review_records").fetchone()[0] == 0
    assert review.queue(w.conn, review.active_session(w.conn)["id"])[0]["outcome"] is None


def test_schema_rejects_inconsistent_rows(world):
    w = world({"g/a": [issue(1)]})
    w.sync()
    with pytest.raises(sqlite3.IntegrityError):
        w.conn.execute("UPDATE tracked_issues SET state = 'deferred'")  # deferred needs a follow-up date
    with pytest.raises(sqlite3.IntegrityError):
        w.conn.execute("UPDATE tracked_issues SET state = 'archived'")
    with pytest.raises(sqlite3.IntegrityError):
        w.conn.execute("INSERT INTO tracked_issues(host, project_id, iid, project_path, title, web_url, remote_state, state, activity_at)"
                       " SELECT host, project_id, iid, project_path, title, web_url, remote_state, state, activity_at FROM tracked_issues")
