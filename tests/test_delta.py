"""#7: comment delta and description diff against the last reviewed snapshot (AC-12)."""
from conftest import ME, MON, at, issue, note
from engineering_inbox import agent, review
from engineering_inbox.delta import compute, load_snapshot, render_delta
from engineering_inbox.gitlab import GitLabError


def delta_of(w, ref="g/a#1"):
    row = w.row(ref)
    return compute(load_snapshot(w.conn, row["reviewed_snapshot_id"]), load_snapshot(w.conn, row["current_snapshot_id"]))


def test_first_review_treats_everything_as_new(world):
    w = world({"g/a": [issue(1, notes=[note(1, "bob", "a"), note(2, "bob", "assigned", system=True)])]})
    w.sync()
    d = delta_of(w)
    assert d.first_review and [n.id for n in d.new_notes] == [1] and not d.no_change


def test_new_edited_and_deleted_notes_and_description_diff(world):
    w = world({"g/a": [issue(1, description="line one\nline two", notes=[note(1, "bob", "keep"), note(2, "bob", "edit me"), note(3, "bob", "delete me")])]})
    w.sync()
    w.finish("g/a#1")
    raw = w.remote("g/a#1")
    raw["notes"] = [raw["notes"][0], {**raw["notes"][1], "body": "edited"}, note(4, "carol", "brand new")]
    w.change("g/a#1", "2026-11-03T00:00:00Z", description="line one\nline 2")
    w.sync(at(3))
    d = delta_of(w)
    assert [n.id for n in d.new_notes] == [4]
    assert [(a.body, b.body) for a, b in d.edited_notes] == [("edit me", "edited")]
    assert [n.id for n in d.deleted_notes] == [3]
    assert "-line two" in d.description_diff and "+line 2" in d.description_diff
    text = render_delta(w.row("g/a#1")["web_url"], d)
    assert "brand new" in text and "https://gitlab.example.com/g/a/-/issues/1#note_4" in text
    assert "EDITED" in text and "edit me" in text and "DELETED" in text and "delete me" in text


def test_sync_alone_never_advances_the_reviewed_baseline(world):
    w = world({"g/a": [issue(1)]})
    w.sync()
    w.finish("g/a#1")
    reviewed = w.row("g/a#1")["reviewed_snapshot_id"]
    w.change("g/a#1", "2026-11-03T00:00:00Z", add_notes=[note(1, "bob", "first")])
    w.sync(at(3))
    w.change("g/a#1", "2026-11-04T00:00:00Z", add_notes=[note(2, "bob", "second")])
    w.sync(at(4))
    assert w.row("g/a#1")["reviewed_snapshot_id"] == reviewed
    assert [n.id for n in delta_of(w).new_notes] == [1, 2]


def test_completion_records_the_observed_snapshot_not_the_latest_fetch(world):
    w = world({"g/a": [issue(1, notes=[note(1, "bob", "seen")])]})
    w.sync()
    review.start(w.conn, w.cfg, MON)
    agent.brief(w.conn, w.cfg, w.row("g/a#1"), MON)  # user reads this snapshot
    w.change("g/a#1", "2026-11-02T02:00:00Z", add_notes=[note(2, "bob", "arrived after reading")])
    w.sync(at(2, 3))
    review.done(w.conn, w.cfg, w.row("g/a#1"), at(2, 4))
    assert [n.id for n in delta_of(w).new_notes] == [2]


def test_no_change_signal_for_due_follow_up(world):
    w = world({"g/a": [issue(1, notes=[note(1, "bob", "x")])]})
    w.sync()
    w.finish("g/a#1", until="2026-11-05")
    w.sync(at(6))
    assert w.row("g/a#1")["state"] == "pending" and delta_of(w).no_change
    assert render_delta("u", delta_of(w)).startswith("No changes")


def test_incomplete_fetch_does_not_report_deletions(world):
    w = world({"g/a": [issue(1, notes=[note(1, "bob", "x")])]})
    w.sync()
    w.finish("g/a#1")
    w.change("g/a#1", "2026-11-03T00:00:00Z")
    w.remote("g/a#1")["notes"] = []
    w.adapter.fail["list_notes:g/a#1"] = GitLabError("pagination stopped at page 2", "incomplete")
    assert not w.sync(at(3)).complete
    assert delta_of(w).no_change  # partial data was not stored
