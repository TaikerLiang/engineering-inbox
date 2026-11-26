"""Personal state transitions and the daily review queue."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime

from .config import Config, iso
from .db import tx
from .sync import process_due_follow_ups


class ReviewError(Exception):
    """A rejected operation. The message says what to do next."""


def ref(row: sqlite3.Row) -> str:
    return f"{row['project_path']}#{row['iid']}"


def resolve(conn: sqlite3.Connection, cfg: Config, issue_ref: str) -> sqlite3.Row:
    """Explicit issue identity: `group/project#12` or the issue web URL."""
    url = re.fullmatch(r"https?://([^/]+)/(.+?)/-/issues/(\d+)/?(?:#.*)?", issue_ref.strip())
    short = re.fullmatch(r"([^#\s]+)#(\d+)", issue_ref.strip())
    if url:
        host, path, iid = url.group(1), url.group(2), int(url.group(3))
    elif short:
        host, path, iid = cfg.host, short.group(1).strip("/"), int(short.group(2))
    else:
        raise ReviewError(f"Cannot parse issue '{issue_ref}'. Use group/project#IID or the issue URL.")
    row = conn.execute(
        "SELECT * FROM tracked_issues WHERE host = ? AND project_path = ? AND iid = ?", (host, path, iid)
    ).fetchone()
    if row is None:
        raise ReviewError(f"{path}#{iid} on {host} is not tracked. Run `inbox list` to see tracked issues.")
    return row


def active_session(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM review_sessions WHERE status = 'active'").fetchone()


def session_for(conn: sqlite3.Connection, day: date) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM review_sessions WHERE session_date = ?", (day.isoformat(),)).fetchone()


def queue(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT q.position, q.outcome, q.reviewed_at, t.* FROM queue_items q JOIN tracked_issues t ON t.id = q.issue_id"
        " WHERE q.session_id = ? ORDER BY q.position",
        (session_id,),
    ).fetchall()


def start(conn: sqlite3.Connection, cfg: Config, now: datetime) -> tuple[sqlite3.Row, str]:
    """Open today's queue. Returns (session, 'created' | 'resumed' | 'already_completed').

    Membership is every Pending/Reviewing issue at this moment, frozen for the day and
    ordered by latest GitLab activity descending, then project ID and IID."""
    today = cfg.today(now)
    with tx(conn):
        process_due_follow_ups(conn, today, now)
        current = active_session(conn)
        if current and current["session_date"] == today.isoformat():
            return current, "resumed"
        if current:  # an earlier day's unfinished queue: its open items carry over by state
            conn.execute("UPDATE review_sessions SET status = 'superseded', completed_at = ? WHERE id = ?", (iso(now), current["id"]))
        finished = session_for(conn, today)
        if finished:
            return finished, "already_completed"
        members = conn.execute(
            "SELECT id FROM tracked_issues WHERE state IN ('pending','reviewing') ORDER BY activity_at DESC, project_id, iid"
        ).fetchall()
        session_id = conn.execute(
            "INSERT INTO review_sessions(session_date, started_at, status) VALUES (?,?,'active')", (today.isoformat(), iso(now))
        ).lastrowid
        conn.executemany(
            "INSERT INTO queue_items(session_id, issue_id, position) VALUES (?,?,?)",
            [(session_id, m["id"], pos) for pos, m in enumerate(members, 1)],
        )
        if members:
            _focus(conn, session_id, members[0]["id"])
        else:
            _complete_if_zero(conn, session_id, now)
        return conn.execute("SELECT * FROM review_sessions WHERE id = ?", (session_id,)).fetchone(), "created"


def _focus(conn: sqlite3.Connection, session_id: int, issue_id: int) -> None:
    """Point the cursor at an item and begin its review (Pending becomes Reviewing)."""
    conn.execute("UPDATE review_sessions SET cursor_issue_id = ? WHERE id = ?", (issue_id, session_id))
    conn.execute("UPDATE tracked_issues SET state = 'reviewing' WHERE id = ? AND state = 'pending'", (issue_id,))


def _complete_if_zero(conn: sqlite3.Connection, session_id: int, now: datetime) -> None:
    open_items = conn.execute(
        "SELECT COUNT(*) AS n FROM queue_items WHERE session_id = ? AND outcome IS NULL", (session_id,)
    ).fetchone()["n"]
    if open_items == 0:
        conn.execute(
            "UPDATE review_sessions SET status = 'completed', completed_at = ?, cursor_issue_id = NULL WHERE id = ? AND status = 'active'",
            (iso(now), session_id),
        )


def _require_session(conn: sqlite3.Connection) -> sqlite3.Row:
    session = active_session(conn)
    if session is None:
        raise ReviewError("No active review session. Run `inbox start`.")
    return session


def current(conn: sqlite3.Connection) -> sqlite3.Row | None:
    session = _require_session(conn)
    if session["cursor_issue_id"] is None:
        return None
    return conn.execute("SELECT * FROM tracked_issues WHERE id = ?", (session["cursor_issue_id"],)).fetchone()


def open_issue(conn: sqlite3.Connection, issue: sqlite3.Row) -> sqlite3.Row:
    """Begin reviewing a specific queue item."""
    with tx(conn):
        session = _require_session(conn)
        item = conn.execute(
            "SELECT * FROM queue_items WHERE session_id = ? AND issue_id = ?", (session["id"], issue["id"])
        ).fetchone()
        if item is None:
            raise ReviewError(f"{ref(issue)} is not in today's queue; membership is fixed at start. It will be offered in the next session.")
        if item["outcome"] is not None:
            raise ReviewError(f"{ref(issue)} was already marked {item['outcome']} in today's queue.")
        _focus(conn, session["id"], issue["id"])
        return conn.execute("SELECT * FROM tracked_issues WHERE id = ?", (issue["id"],)).fetchone()


def next_issue(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Move to the next unresolved item after the cursor, wrapping. Never completes a review."""
    with tx(conn):
        session = _require_session(conn)
        items = [i for i in queue(conn, session["id"]) if i["outcome"] is None]
        if not items:
            return None
        cursor = next((i["position"] for i in queue(conn, session["id"]) if i["id"] == session["cursor_issue_id"]), 0)
        target = next((i for i in items if i["position"] > cursor), items[0])
        _focus(conn, session["id"], target["id"])
        return conn.execute("SELECT * FROM tracked_issues WHERE id = ?", (target["id"],)).fetchone()


def _finish(conn, cfg, issue, now, outcome, decision, actions, follow_up_date=None, follow_up_reason=None) -> None:
    with tx(conn):
        row = conn.execute("SELECT * FROM tracked_issues WHERE id = ?", (issue["id"],)).fetchone()
        if row["state"] != "reviewing":
            hint = f"Run `inbox open {ref(row)}` first." if row["state"] == "pending" else "It re-enters on a mention, a description change or a due follow-up."
            raise ReviewError(f"{ref(row)} is {row['state']}, not reviewing. {hint}")
        # Record what was actually observed; fall back to the current snapshot if no brief was shown.
        snapshot_id = row["observed_snapshot_id"] or row["current_snapshot_id"]
        session = active_session(conn)
        conn.execute(
            "UPDATE tracked_issues SET state = ?, follow_up_date = ?, follow_up_reason = ?, reviewed_snapshot_id = ?,"
            " observed_snapshot_id = NULL, reasons = '[]' WHERE id = ?",
            (outcome, follow_up_date, follow_up_reason, snapshot_id, row["id"]),
        )
        conn.execute(
            "INSERT INTO review_records(issue_id, session_id, outcome, snapshot_id, decision, actions, follow_up_date,"
            " follow_up_reason, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (row["id"], session["id"] if session else None, outcome, snapshot_id, decision or "",
             json.dumps(list(actions or [])), follow_up_date, follow_up_reason, iso(now)),
        )
        if session:
            conn.execute(
                "UPDATE queue_items SET outcome = ?, reviewed_at = ? WHERE session_id = ? AND issue_id = ?",
                (outcome, iso(now), session["id"], row["id"]),
            )
            _complete_if_zero(conn, session["id"], now)


def done(conn, cfg: Config, issue, now: datetime, decision: str = "", actions: list[str] | None = None) -> None:
    _finish(conn, cfg, issue, now, "done", decision, actions)


def follow_up(conn, cfg: Config, issue, now: datetime, when: str, reason: str = "", decision: str = "", actions: list[str] | None = None) -> None:
    """Defer until a future local date (YYYY-MM-DD). The issue re-enters on that date."""
    try:
        target = date.fromisoformat(when)
    except ValueError as exc:
        raise ReviewError(f"Follow-up date '{when}' is not YYYY-MM-DD.") from exc
    if target <= cfg.today(now):
        raise ReviewError(f"Follow-up date {when} must be after today ({cfg.today(now)} in {cfg.timezone}).")
    _finish(conn, cfg, issue, now, "deferred", decision, actions, target.isoformat(), reason or None)


def progress(conn: sqlite3.Connection, session: sqlite3.Row) -> dict:
    items = queue(conn, session["id"])
    resolved = [i for i in items if i["outcome"]]
    return {"total": len(items), "resolved": len(resolved), "inbox_zero": len(items) == len(resolved)}
