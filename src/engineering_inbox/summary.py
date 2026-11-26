"""Daily review summary and usage records for workflow validation."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from .config import Config
from .review import queue, ref, session_for
from .sync import last_run


def _day_bounds(cfg: Config, day: date) -> tuple[str, str]:
    start = datetime.combine(day, datetime.min.time(), cfg.tz)
    fmt = lambda d: d.astimezone(timezone.utc).isoformat(timespec="seconds")
    return fmt(start), fmt(start + timedelta(days=1))


def _minutes(session: sqlite3.Row) -> int | None:
    if not session["completed_at"]:
        return None
    span = datetime.fromisoformat(session["completed_at"]) - datetime.fromisoformat(session["started_at"])
    return round(span.total_seconds() / 60)


def daily(conn: sqlite3.Connection, cfg: Config, day: date) -> str:
    """What actually happened on a local day: published replies, decisions, action items,
    follow-ups, unpublished drafts and unfinished work."""
    lo, hi = _day_bounds(cfg, day)
    out = [f"# Review summary {day.isoformat()}", ""]
    session = session_for(conn, day)
    if session is None:
        out.append("No review session was started.")
    else:
        items = queue(conn, session["id"])
        resolved = sum(1 for i in items if i["outcome"])
        minutes = _minutes(session)
        zero = "yes" if items and resolved == len(items) or not items else "no"
        out.append(f"Session: {session['status']} · reviewed {resolved}/{len(items)} · Inbox Zero: {zero} · duration: {f'{minutes} min' if minutes is not None else 'in progress'}")
    run = last_run(conn)
    if run is None:
        out.append("Sync: never run; the queue may be missing issues.")
    elif not run["complete"]:
        out += [f"Sync INCOMPLETE at {run['finished_at']}; the queue may be missing issues:"] + [f"- {e}" for e in json.loads(run["errors"])]

    ops = conn.execute(
        "SELECT o.*, t.project_path, t.iid, t.web_url FROM operation_logs o JOIN tracked_issues t ON t.id = o.issue_id"
        " WHERE o.updated_at >= ? AND o.updated_at < ? ORDER BY o.id",
        (lo, hi),
    ).fetchall()
    records = conn.execute(
        "SELECT r.*, t.project_path, t.iid, t.web_url FROM review_records r JOIN tracked_issues t ON t.id = r.issue_id"
        " WHERE r.created_at >= ? AND r.created_at < ? ORDER BY r.id",
        (lo, hi),
    ).fetchall()
    link = lambda r: f"[{ref(r)}]({r['web_url']})"

    def section(title: str, lines: list[str]) -> None:
        out.extend(["", f"## {title}"] + (lines or ["- none"]))

    section("Published replies", [f"- {link(o)}: {o['remote_url']}" for o in ops if o["status"] == "succeeded"])
    section("Decisions", [f"- {link(r)}: {r['decision']}" for r in records if r["decision"]])
    section("Action items", [f"- {link(r)}: {a}" for r in records for a in json.loads(r["actions"])])
    section("Follow-ups scheduled", [
        f"- {link(r)}: {r['follow_up_date']}" + (f" ({r['follow_up_reason']})" if r["follow_up_reason"] else "")
        for r in records if r["outcome"] == "deferred"
    ])
    section("Not published", [
        f"- {link(o)}: {o['status']}" + (f" ({o['error']})" if o["error"] else "") + f", operation {o['id']}"
        for o in ops if o["status"] != "succeeded"
    ])
    unfinished = [f"- {link(i)} ({i['state']})" for i in queue(conn, session["id"]) if not i["outcome"]] if session else []
    section("Unfinished (carries over)", unfinished)
    return "\n".join(out) + "\n"


def record_usage(conn: sqlite3.Connection, session: sqlite3.Row, missed_follow_ups: int, missed_issues: int, friction: str) -> None:
    conn.execute(
        "UPDATE review_sessions SET missed_follow_ups = ?, missed_issues = ?, friction = ? WHERE id = ?",
        (missed_follow_ups, missed_issues, friction, session["id"]),
    )


def usage_report(conn: sqlite3.Connection) -> str:
    """Per-session usage table for the five-day validation."""
    rows = conn.execute("SELECT * FROM review_sessions ORDER BY session_date").fetchall()
    out = ["| Date | Status | Minutes | Candidates | Reviewed | Missed follow-ups | Missed issues | Friction |", "|---|---|---|---|---|---|---|---|"]
    for s in rows:
        items = queue(conn, s["id"])
        cell = lambda v: "not recorded" if v is None else str(v).replace("|", "/")
        out.append(
            f"| {s['session_date']} | {s['status']} | {cell(_minutes(s)) if s['completed_at'] else 'open'} | {len(items)} |"
            f" {sum(1 for i in items if i['outcome'])} | {cell(s['missed_follow_ups'])} | {cell(s['missed_issues'])} | {cell(s['friction'])} |"
        )
    return "\n".join(out) + "\n"
