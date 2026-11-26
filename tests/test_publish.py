"""#9: explicitly authorised comment publishing (AC-04, AC-14)."""
import pytest

from conftest import ME, MON, at, issue, note
from engineering_inbox import agent, publish, review
from engineering_inbox.gitlab import GitLabError
from engineering_inbox.publish import PublishError, StaleContext


@pytest.fixture
def w(world):
    w = world({"g/a": [issue(1, notes=[note(1, "bob", "question")])]})
    w.sync()
    review.start(w.conn, w.cfg, MON)
    agent.brief(w.conn, w.cfg, w.row("g/a#1"), MON)  # the user has seen the issue
    return w


def ops(w):
    return [(o["status"], o["remote_url"]) for o in w.conn.execute("SELECT * FROM operation_logs ORDER BY id")]


def post(w, body="Agreed. I will take it.", now=MON, **kw):
    return publish.publish(w.conn, w.cfg, w.row("g/a#1"), w.adapter, body, now, **kw)


def test_draft_never_writes_to_gitlab(w):
    publish.save_draft(w.conn, w.row("g/a#1"), "draft text", MON)
    assert "create_note" not in w.adapter.calls and len(w.remote("g/a#1")["notes"]) == 1
    assert ops(w) == [("draft", None)]


def test_publish_records_url_and_leaves_review_state_unchanged(w):
    op = post(w)
    assert op["status"] == "succeeded" and op["remote_url"] == "https://gitlab.example.com/g/a/-/issues/1#note_2"
    assert w.remote("g/a#1")["notes"][-1]["body"] == "Agreed. I will take it."
    row = w.row("g/a#1")
    assert row["state"] == "reviewing" and review.queue(w.conn, review.active_session(w.conn)["id"])[0]["outcome"] is None
    post(w, "Second reply.")  # own earlier comment is not a stale-context conflict
    assert [s for s, _ in ops(w)] == ["succeeded", "succeeded"]


def test_unviewed_issue_is_not_published(world):
    w = world({"g/a": [issue(1)]})
    w.sync()
    with pytest.raises(StaleContext, match="has not been viewed"):
        post(w)
    assert "create_note" not in w.adapter.calls


def test_stale_context_blocks_until_accepted(w):
    w.change("g/a#1", "2026-11-02T02:00:00Z", add_notes=[note(5, "carol", "we decided the opposite")])
    with pytest.raises(StaleContext) as exc:
        post(w, now=at(2, 3))
    assert "we decided the opposite" in exc.value.detail and "create_note" not in w.adapter.calls
    assert post(w, now=at(2, 4), accept_stale=True)["status"] == "succeeded"


def test_stale_description_change_blocks(w):
    w.change("g/a#1", "2026-11-02T02:00:00Z", description="New scope")
    with pytest.raises(StaleContext) as exc:
        post(w, now=at(2, 3))
    assert "+New scope" in exc.value.detail


def test_target_mismatch_and_refresh_failure_do_not_post(w):
    w.remote("g/a#1")["web_url"] = "https://gitlab.example.com/g/moved/-/issues/1"
    with pytest.raises(PublishError, match="does not match"):
        post(w)
    del w.remote("g/a#1")["web_url"]
    w.adapter.fail["get_issue:g/a#1"] = GitLabError("503", "server")
    with pytest.raises(PublishError, match="could not refresh"):
        post(w)
    assert "create_note" not in w.adapter.calls and ops(w) == []


def test_definite_failure_is_recorded_and_retry_is_allowed(w):
    w.adapter.fail["create_note"] = GitLabError("403 Forbidden", "permission")
    with pytest.raises(PublishError, match="Nothing was posted"):
        post(w)
    assert ops(w) == [("failed", None)] and w.row("g/a#1")["state"] == "reviewing"
    w.adapter.fail.clear()
    post(w)
    assert [s for s, _ in ops(w)] == ["failed", "succeeded"]


def test_ambiguous_timeout_is_reconciled_as_not_posted_then_retried(w):
    w.adapter.fail["create_note"] = GitLabError("timed out", "timeout", ambiguous=True)
    with pytest.raises(PublishError, match="Outcome unknown"):
        post(w)
    assert ops(w) == [("ambiguous", None)]
    w.adapter.fail.clear()
    post(w, now=at(2, 2))
    assert [s for s, _ in ops(w)] == ["failed", "succeeded"]
    assert sum(n["body"] == "Agreed. I will take it." for n in w.remote("g/a#1")["notes"]) == 1


def test_ambiguous_timeout_that_actually_posted_is_not_posted_twice(w):
    def lands_then_times_out(project, iid, body):
        w.remote("g/a#1")["notes"].append(note(9, ME, body, when="2026-11-02T01:00:05Z"))
        raise GitLabError("timed out", "timeout", ambiguous=True)
    original, w.adapter.create_note = w.adapter.create_note, lands_then_times_out
    with pytest.raises(PublishError):
        post(w)
    w.adapter.create_note = original
    with pytest.raises(PublishError, match="already reached GitLab"):
        post(w, now=at(2, 2))
    assert ops(w) == [("succeeded", "https://gitlab.example.com/g/a/-/issues/1#note_9")]
    assert sum(n["body"] == "Agreed. I will take it." for n in w.remote("g/a#1")["notes"]) == 1


def test_reconcile_failure_blocks_retry(w):
    w.adapter.fail["create_note"] = GitLabError("timed out", "timeout", ambiguous=True)
    with pytest.raises(PublishError):
        post(w)
    w.adapter.fail = {"list_notes:g/a#1": GitLabError("503", "server")}
    with pytest.raises(PublishError, match="could not refresh"):
        post(w)
    assert ops(w) == [("ambiguous", None)]


def test_empty_body_is_rejected(w):
    with pytest.raises(PublishError):
        post(w, "  \n")
