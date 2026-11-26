"""Comment drafts and explicitly authorised publication."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from .config import Config, iso
from .db import tx
from .delta import compute, load_snapshot, note_url, render_delta
from .gitlab import Adapter, GitLabError
from .review import ref
from .sync import ingest


class PublishError(Exception):
    pass


class StaleContext(PublishError):
    """New remote activity since the user last saw the issue. `detail` is the verbatim delta."""

    def __init__(self, message: str, detail: str):
        super().__init__(message)
        self.detail = detail


def _log(conn, issue_id: int, body: str, status: str, now: datetime) -> int:
    with tx(conn):
        return conn.execute(
            "INSERT INTO operation_logs(issue_id, operation, body, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (issue_id, "comment", body, status, iso(now), iso(now)),
        ).lastrowid


def _update(conn, op_id: int, now: datetime, status: str, remote_ref=None, remote_url=None, error=None) -> None:
    with tx(conn):
        conn.execute(
            "UPDATE operation_logs SET status = ?, remote_ref = ?, remote_url = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, remote_ref, remote_url, error, iso(now), op_id),
        )


def save_draft(conn: sqlite3.Connection, issue: sqlite3.Row, body: str, now: datetime) -> int:
    """Record a draft. Nothing is sent to GitLab."""
    if not body.strip():
        raise PublishError("Comment body is empty.")
    return _log(conn, issue["id"], body, "draft", now)


def reconcile(conn: sqlite3.Connection, issue: sqlite3.Row, adapter: Adapter, me: str, now: datetime) -> list[str]:
    """Resolve writes whose outcome is unknown by looking for the comment on GitLab.
    Found: succeeded. Not found: failed, safe to retry. Raises GitLabError if it cannot check."""
    pending = conn.execute(
        "SELECT * FROM operation_logs WHERE issue_id = ? AND status IN ('ambiguous','started') ORDER BY id", (issue["id"],)
    ).fetchall()
    if not pending:
        return []
    notes = adapter.list_notes(issue["project_path"], issue["iid"])
    outcomes = []
    for op in pending:
        earliest = datetime.fromisoformat(op["created_at"]) - timedelta(minutes=5)
        match = next(
            (n for n in notes if not n.system and n.author == me and n.body.strip() == op["body"].strip()
             and datetime.fromisoformat(n.created_at) >= earliest),
            None,
        )
        if match:
            _update(conn, op["id"], now, "succeeded", str(match.id), note_url(issue["web_url"], match))
            outcomes.append(f"operation {op['id']}: found on GitLab, recorded as published")
        else:
            _update(conn, op["id"], now, "failed", error="not found on GitLab after ambiguous result")
            outcomes.append(f"operation {op['id']}: not on GitLab, recorded as failed")
    return outcomes


def publish(conn: sqlite3.Connection, cfg: Config, issue: sqlite3.Row, adapter: Adapter, body: str, now: datetime, accept_stale: bool = False) -> sqlite3.Row:
    """Post one comment. Call only on explicit user instruction. Never changes review state.

    Order: reconcile unknown earlier writes, refresh and verify the target, refuse on unseen
    activity unless `accept_stale`, log intent, post, log the outcome."""
    if not body.strip():
        raise PublishError("Comment body is empty.")
    try:
        me = adapter.current_user()
        reconciled = reconcile(conn, issue, adapter, me, now)
        remote = adapter.get_issue(issue["project_path"], issue["iid"])
        notes = adapter.list_notes(issue["project_path"], issue["iid"])
    except GitLabError as exc:
        raise PublishError(f"Not published: could not refresh {ref(issue)} ({exc.kind}): {exc}") from exc
    if any("recorded as published" in r for r in reconciled) and _already_published(conn, issue, body):
        raise PublishError(f"Not published: an earlier attempt with the same text already reached GitLab. {' '.join(reconciled)}")
    if (remote.project_id, remote.iid, remote.web_url) != (issue["project_id"], issue["iid"], issue["web_url"]):
        raise PublishError(f"Not published: remote issue {remote.web_url} does not match tracked {issue['web_url']}.")

    baseline_id = issue["observed_snapshot_id"] or issue["reviewed_snapshot_id"]
    ingest(conn, cfg, me, remote, notes, now)
    fresh = conn.execute("SELECT * FROM tracked_issues WHERE id = ?", (issue["id"],)).fetchone()
    if not accept_stale:
        if baseline_id is None:
            raise StaleContext(f"Not published: {ref(issue)} has not been viewed. Run `inbox brief {ref(issue)}` first.", "")
        delta = compute(load_snapshot(conn, baseline_id), load_snapshot(conn, fresh["current_snapshot_id"]))
        theirs = lambda n: n.author != me  # own notes are not a conflict
        unseen = [n for n in delta.new_notes if theirs(n)] + [a for _, a in delta.edited_notes if theirs(a)] + [n for n in delta.deleted_notes if theirs(n)]
        if unseen or delta.description_changed:
            raise StaleContext(
                f"Not published: {ref(issue)} changed since you last viewed it. Review the changes, then repeat with --accept-stale.",
                render_delta(issue["web_url"], delta),
            )

    op_id = _log(conn, issue["id"], body, "started", now)
    try:
        note = adapter.create_note(issue["project_path"], issue["iid"], body)
    except GitLabError as exc:
        status = "ambiguous" if exc.ambiguous else "failed"
        _update(conn, op_id, now, status, error=f"{exc.kind}: {exc}")
        hint = "Outcome unknown; the next publish attempt reconciles before retrying." if exc.ambiguous else "Nothing was posted."
        raise PublishError(f"Publish {status} for {ref(issue)}: {exc}. {hint}") from exc
    _update(conn, op_id, now, "succeeded", str(note.id), note_url(issue["web_url"], note))
    return conn.execute("SELECT * FROM operation_logs WHERE id = ?", (op_id,)).fetchone()


def _already_published(conn, issue: sqlite3.Row, body: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM operation_logs WHERE issue_id = ? AND status = 'succeeded' AND trim(body) = trim(?)", (issue["id"], body)
    ).fetchone() is not None
