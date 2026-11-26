"""Discovery, snapshot ingestion and re-entry events."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .config import Config, iso
from .db import tx
from .delta import Snapshot, load_snapshot, note_url
from .gitlab import Adapter, GitLabError, RemoteIssue, RemoteNote

WATERMARK_OVERLAP = timedelta(minutes=5)
_CODE = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]*`", re.S)


def mentions(body: str, username: str) -> bool:
    """Direct @username mention outside code spans. Group and @all mentions are not detected."""
    text = _CODE.sub(" ", body or "")
    return re.search(rf"(?<![\w@/.+-])@{re.escape(username)}(?![\w-]|\.\w)", text, re.I) is not None


def relevance(issue: RemoteIssue, notes: list[RemoteNote], me: str) -> list[str]:
    comments = [n for n in notes if not n.system]
    found = []
    if me in issue.assignees:
        found.append("assignee")
    if issue.author == me:
        found.append("author")
    if (issue.author != me and mentions(issue.description, me)) or any(
        n.author != me and mentions(n.body, me) for n in comments
    ):
        found.append("mentioned")
    if any(n.author == me for n in comments):
        found.append("participant")
    if issue.subscribed:
        found.append("subscriber")
    return found


@dataclass
class SyncReport:
    full: bool
    errors: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    reentered: list[str] = field(default_factory=list)
    due: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.errors


def _add_reasons(conn: sqlite3.Connection, issue_id: int, reasons: list[str]) -> None:
    row = conn.execute("SELECT reasons FROM tracked_issues WHERE id = ?", (issue_id,)).fetchone()
    merged = list(dict.fromkeys(json.loads(row["reasons"]) + reasons))
    conn.execute("UPDATE tracked_issues SET reasons = ? WHERE id = ?", (json.dumps(merged), issue_id))


def _event(conn: sqlite3.Connection, issue_id: int, key: str, kind: str, detail: str, now: datetime) -> bool:
    """Record an event identity. Returns False when it was already consumed."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO events(issue_id, key, kind, detail, created_at) VALUES (?,?,?,?,?)",
        (issue_id, key, kind, detail, iso(now)),
    )
    return cur.rowcount == 1


def _reenter(conn: sqlite3.Connection, issue_id: int, reasons: list[str]) -> bool:
    """Done/Deferred becomes Pending and the active follow-up is cleared (history stays in
    review_records). Pending/Reviewing only gains the reasons."""
    _add_reasons(conn, issue_id, reasons)
    cur = conn.execute(
        "UPDATE tracked_issues SET state = 'pending', follow_up_date = NULL, follow_up_reason = NULL "
        "WHERE id = ? AND state IN ('done','deferred')",
        (issue_id,),
    )
    return cur.rowcount == 1


def ingest(conn: sqlite3.Connection, cfg: Config, me: str, issue: RemoteIssue, notes: list[RemoteNote], now: datetime) -> str | None:
    """Store one completely fetched issue. Returns 'new', 'reentered', 'updated' or None
    (not tracked and not relevant). Snapshot and event consumption commit together."""
    snap = Snapshot(None, issue.description, tuple(notes), iso(now))
    rel = relevance(issue, notes, me)
    with tx(conn):
        row = conn.execute(
            "SELECT * FROM tracked_issues WHERE host = ? AND project_id = ? AND iid = ?",
            (cfg.host, issue.project_id, issue.iid),
        ).fetchone()
        if row is None:
            if not rel or issue.state != "opened":
                return None
            issue_id = conn.execute(
                "INSERT INTO tracked_issues(host, project_id, iid, project_path, title, web_url, remote_state,"
                " state, activity_at, relevance, reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (cfg.host, issue.project_id, issue.iid, issue.project_path, issue.title, issue.web_url,
                 "opened", "pending", issue.updated_at, json.dumps(rel), json.dumps([f"new: {', '.join(rel)}"])),
            ).lastrowid
            _store_snapshot(conn, issue_id, snap)
            for n in notes:  # existing mentions are part of the first review, not future events
                if not n.system and n.author != me and mentions(n.body, me):
                    _event(conn, issue_id, f"mention:{n.id}", "mention_seen", "", now)
            return "new"

        issue_id = row["id"]
        merged_rel = list(dict.fromkeys(json.loads(row["relevance"]) + rel))
        conn.execute(
            "UPDATE tracked_issues SET project_path = ?, title = ?, web_url = ?, remote_state = ?, closed_at = ?,"
            " closed_by = ?, activity_at = ?, relevance = ?, sync_error = NULL WHERE id = ?",
            (issue.project_path, issue.title, issue.web_url, issue.state, issue.closed_at, issue.closed_by,
             issue.updated_at, json.dumps(merged_rel), issue_id),
        )
        triggers: list[str] = []
        previous = load_snapshot(conn, row["current_snapshot_id"])
        if previous is None or previous.content_hash != snap.content_hash:
            snapshot_id = _store_snapshot(conn, issue_id, snap)
            for n in notes:
                if not n.system and n.author != me and mentions(n.body, me):
                    if _event(conn, issue_id, f"mention:{n.id}", "mention", f"@{n.author}", now):
                        triggers.append(f"mention by @{n.author}: {note_url(issue.web_url, n)}")
            if previous is not None and previous.description_hash != snap.description_hash:
                seen = {n.id for n in previous.notes}
                editors = {n.author for n in notes if n.system and n.id not in seen and n.body.startswith("changed the description")}
                if editors and editors <= {me}:
                    _event(conn, issue_id, f"description:{snapshot_id}", "description_self", "", now)
                else:
                    who = ", ".join(f"@{e}" for e in sorted(editors - {me})) or "unknown author (attribution uncertain)"
                    _event(conn, issue_id, f"description:{snapshot_id}", "description", who, now)
                    triggers.append(f"description changed by {who}")
        if row["remote_state"] == "opened" and issue.state == "closed" and row["state"] in ("pending", "reviewing"):
            _add_reasons(conn, issue_id, [f"closed remotely by @{issue.closed_by or 'unknown'} at {issue.closed_at or 'unknown time'}"])
        if triggers and _reenter(conn, issue_id, triggers):
            return "reentered"
        return "updated"


def _store_snapshot(conn: sqlite3.Connection, issue_id: int, snap: Snapshot) -> int:
    snapshot_id = conn.execute(
        "INSERT INTO snapshots(issue_id, content_hash, description, description_hash, notes, fetched_at) VALUES (?,?,?,?,?,?)",
        (issue_id, snap.content_hash, snap.description, snap.description_hash, snap.notes_json(), snap.fetched_at),
    ).lastrowid
    conn.execute("UPDATE tracked_issues SET current_snapshot_id = ? WHERE id = ?", (snapshot_id, issue_id))
    return snapshot_id


def process_due_follow_ups(conn: sqlite3.Connection, today: date, now: datetime) -> list[str]:
    """Deferred issues whose follow-up date is today or earlier become Pending, changed or not."""
    due = []
    with tx(conn):
        rows = conn.execute(
            "SELECT * FROM tracked_issues WHERE state = 'deferred' AND follow_up_date <= ?", (today.isoformat(),)
        ).fetchall()
        for row in rows:
            reason = f"follow-up due {row['follow_up_date']}" + (f": {row['follow_up_reason']}" if row["follow_up_reason"] else "")
            _event(conn, row["id"], f"follow_up:{row['follow_up_date']}:{iso(now)}", "follow_up_due", reason, now)
            _reenter(conn, row["id"], [reason])
            due.append(f"{row['project_path']}#{row['iid']}")
    return due


def refresh_issue(conn: sqlite3.Connection, cfg: Config, adapter: Adapter, me: str, project: str, iid: int, now: datetime) -> str | None:
    """Fetch one issue completely, then ingest. Raises GitLabError without storing anything."""
    issue = adapter.get_issue(project, iid)
    notes = adapter.list_notes(project, iid)
    return ingest(conn, cfg, me, issue, notes, now)


def sync(conn: sqlite3.Connection, cfg: Config, adapter: Adapter, now: datetime, full: bool = False) -> SyncReport:
    """Scan the allowlist. Incremental by per-project watermark; `full` rescans every open
    issue and every tracked issue. Any failure leaves the watermark and stored data untouched
    for the affected scope and is reported, never treated as absence of issues."""
    report = SyncReport(full=full)
    report.due = process_due_follow_ups(conn, cfg.today(now), now)
    try:
        me = adapter.current_user()
    except GitLabError as exc:
        report.errors.append(f"current user ({exc.kind}): {exc}")
        _record_run(conn, now, report)
        return report

    for project in cfg.projects:
        row = conn.execute("SELECT watermark FROM project_sync WHERE host = ? AND project_path = ?", (cfg.host, project)).fetchone()
        watermark = row["watermark"] if row else None
        project_full = full or watermark is None
        errors_before = len(report.errors)
        try:
            if project_full:
                listed = adapter.list_issues(project, "opened")
            else:
                since = datetime.fromisoformat(watermark) - WATERMARK_OVERLAP
                listed = adapter.list_issues(project, "all", iso(since))
        except GitLabError as exc:
            report.errors.append(f"{project} ({exc.kind}): {exc}")
            continue

        tracked = {
            r["iid"]: r for r in conn.execute("SELECT * FROM tracked_issues WHERE host = ? AND project_path = ?", (cfg.host, project))
        }
        targets: dict[int, RemoteIssue | None] = {i.iid: i for i in listed}
        if project_full:  # tracked issues absent from the open list were closed, moved or lost
            targets.update({iid: None for iid in tracked if iid not in targets})
        for iid, seen in sorted(targets.items()):
            known = tracked.get(iid)
            if known is None and seen.state != "opened":
                continue  # only open issues are discovered
            if known and not full and seen and known["current_snapshot_id"] and not known["sync_error"] and known["activity_at"] == seen.updated_at:
                continue
            key = f"{project}#{iid}"
            try:
                result = refresh_issue(conn, cfg, adapter, me, project, iid, now)
            except GitLabError as exc:
                report.errors.append(f"{key} ({exc.kind}): {exc}")
                if known:
                    with tx(conn):
                        conn.execute("UPDATE tracked_issues SET sync_error = ? WHERE id = ?", (f"{exc.kind}: {exc}", known["id"]))
                continue
            if result == "new":
                report.new.append(key)
            elif result == "reentered":
                report.reentered.append(key)
        if len(report.errors) == errors_before:
            with tx(conn):
                conn.execute(
                    "INSERT INTO project_sync(host, project_path, watermark) VALUES (?,?,?)"
                    " ON CONFLICT(host, project_path) DO UPDATE SET watermark = excluded.watermark",
                    (cfg.host, project, iso(now)),
                )
    _record_run(conn, now, report)
    return report


def _record_run(conn: sqlite3.Connection, now: datetime, report: SyncReport) -> None:
    with tx(conn):
        conn.execute(
            "INSERT INTO sync_runs(started_at, finished_at, complete, errors) VALUES (?,?,?,?)",
            (iso(now), iso(now), int(report.complete), json.dumps(report.errors)),
        )


def last_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
