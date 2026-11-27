"""`inbox` command line. Mutations take an explicit issue; navigation never completes a review."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import typer

from . import agent, config, publish as publishing, review, schedule as scheduling, summary as summaries, sync as syncing
from .db import connect, get_meta, set_meta, tx
from .gitlab import FixtureAdapter, GitLabError, GlabAdapter

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Personal review inbox for GitLab issues.")


class Ctx:
    def __init__(self):
        self.cfg = config.load()
        self.conn = connect(self.cfg.db_path)
        self.now = config.clock()

    @property
    def adapter(self):
        if self.cfg.adapter == "fixture":
            return FixtureAdapter(path=Path(self.cfg.fixture_path).expanduser(), host=self.cfg.host)
        return GlabAdapter(self.cfg.host)

    def issue(self, issue_ref: str):
        return review.resolve(self.conn, self.cfg, issue_ref)


def fail(message: str) -> None:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def guarded(fn):
    """Turn expected failures into a one-line error and exit code 1."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except publishing.StaleContext as exc:
            if exc.detail:
                typer.echo(exc.detail)
            fail(str(exc))
        except (config.ConfigError, review.ReviewError, publishing.PublishError, GitLabError) as exc:
            fail(str(exc))

    return wrapper


def _print_sync(report: syncing.SyncReport) -> None:
    typer.echo(f"sync ({'full' if report.full else 'incremental'}): {len(report.new)} new, {len(report.reentered)} re-entered, {len(report.due)} follow-ups due")
    for key in report.new + report.reentered + report.due:
        typer.echo(f"  + {key}")
    if not report.complete:
        typer.secho("SYNC INCOMPLETE: issues may be missing. Nothing was dropped; fix and re-run `inbox sync`.", fg=typer.colors.YELLOW)
        for error in report.errors:
            typer.echo(f"  ! {error}")


def _line(row) -> str:
    extra = f" until {row['follow_up_date']}" if row["follow_up_date"] else ""
    flags = "".join([" [closed]" if row["remote_state"] == "closed" else "", " [sync error]" if row["sync_error"] else ""])
    return f"{review.ref(row):40} {row['state']}{extra}{flags}  {row['title']}"


def _show(c: Ctx, row) -> None:
    path, warnings = agent.brief(c.conn, c.cfg, row, c.now)
    typer.echo(_line(row))
    for reason in json.loads(row["reasons"]):
        typer.echo(f"  why: {reason}")
    for warning in warnings:
        typer.secho(f"  ! {warning}", fg=typer.colors.YELLOW)
    typer.echo(f"  brief: {path}")
    typer.echo(f"  chat:  inbox chat {review.ref(row)}")


@app.command()
@guarded
def init():
    """Create the home directory and configuration, then run the initial full discovery."""
    path = config.config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config.EXAMPLE)
        typer.echo(f"Wrote {path}. Edit host and projects, then run `inbox init` again.")
        return
    c = Ctx()
    _print_sync(syncing.sync(c.conn, c.cfg, c.adapter, c.now, full=True))


@app.command()
@guarded
def sync(full: bool = typer.Option(False, "--full", help="Rescan every open and tracked issue.")):
    """Discover relevant issues and detect re-entry events."""
    c = Ctx()
    report = syncing.sync(c.conn, c.cfg, c.adapter, c.now, full=full)
    _print_sync(report)
    raise typer.Exit(0 if report.complete else 2)


@app.command()
@guarded
def prepare(
    issue: Optional[str] = typer.Argument(None, help="Prepare one issue (always re-analysed)."),
    no_sync: bool = typer.Option(False, "--no-sync", help="Skip the sync before preparing."),
):
    """Sync, then prepare briefs for Pending/Reviewing issues. Safe to re-run after a missed schedule."""
    c = Ctx()
    try:
        with agent.lock(c.cfg.home / "prepare.lock"):
            complete = True
            if not no_sync:
                report = syncing.sync(c.conn, c.cfg, c.adapter, c.now)
                _print_sync(report)
                complete = report.complete
            if issue:
                targets = [c.issue(issue)]
            else:
                rows = c.conn.execute("SELECT * FROM tracked_issues WHERE state IN ('pending','reviewing') ORDER BY activity_at DESC").fetchall()
                targets = [r for r in rows if agent.needs_preparation(c.conn, r)]
            results = agent.prepare(c.conn, c.cfg, targets, c.now)
            for key, result in results.items():
                typer.echo(f"  {key}: {result}")
            failed = [k for k, r in results.items() if not r.startswith(("ok", "no_change"))]
            with tx(c.conn):
                set_meta(c.conn, "last_prepare", json.dumps({"at": config.iso(c.now), "prepared": len(results), "failed": failed, "sync_complete": complete}))
            typer.echo(f"prepared {len(results) - len(failed)}/{len(results)}")
            raise typer.Exit(0 if complete and not failed else 2)
    except agent.Busy:
        fail("Another preparation is running. Wait for it to finish.")


@app.command()
@guarded
def start(
    no_sync: bool = typer.Option(False, "--no-sync", help="Use stored data without syncing first."),
    rebuild: bool = typer.Option(False, "--rebuild", help="Re-derive today's queue membership; resolved items are kept."),
):
    """Start or resume today's fixed review queue."""
    c = Ctx()
    if (rebuild or review.session_for(c.conn, c.cfg.today(c.now)) is None) and not no_sync:  # first start of the day, or a rebuild
        _print_sync(syncing.sync(c.conn, c.cfg, c.adapter, c.now))
    session, how = review.start(c.conn, c.cfg, c.now, rebuild=rebuild)
    p = review.progress(c.conn, session)
    typer.echo(f"session {session['session_date']} {how.replace('_', ' ')}: {p['resolved']}/{p['total']} resolved")
    items = review.queue(c.conn, session["id"])
    for item in items:
        mark = {"done": "x", "deferred": ">"}.get(item["outcome"], " ")
        typer.echo(f" [{mark}] {item['position']:>2}. {_line(item)}")
    unprepared = [i for i in items if not i["outcome"] and agent.needs_preparation(c.conn, i)]
    if unprepared:
        typer.secho(f"{len(unprepared)} item(s) not prepared; run `inbox prepare`. Raw context is available meanwhile.", fg=typer.colors.YELLOW)
    if p["inbox_zero"]:
        typer.echo("Inbox Zero." + ("" if how == "created" else " New activity waits for the next session."))
        _warn_sync(c)


def _warn_sync(c: Ctx) -> None:
    run = syncing.last_run(c.conn)
    if run is None or not run["complete"]:
        typer.secho("Last sync was incomplete: Inbox Zero covers only the issues that were fetched.", fg=typer.colors.YELLOW)


@app.command()
@guarded
def status():
    """Inbox counts, queue progress, sync and preparation health."""
    c = Ctx()
    counts = {r["state"]: r["n"] for r in c.conn.execute("SELECT state, COUNT(*) AS n FROM tracked_issues GROUP BY state")}
    typer.echo("issues: " + ", ".join(f"{s} {counts.get(s, 0)}" for s in ("pending", "reviewing", "deferred", "done")))
    session = review.active_session(c.conn) or review.session_for(c.conn, c.cfg.today(c.now))
    if session:
        p = review.progress(c.conn, session)
        typer.echo(f"session {session['session_date']}: {session['status']}, {p['resolved']}/{p['total']} resolved" + (", Inbox Zero" if p["inbox_zero"] else ""))
    else:
        typer.echo("session: none today; run `inbox start`")
    run = syncing.last_run(c.conn)
    if run is None:
        typer.secho("sync: never run", fg=typer.colors.YELLOW)
    else:
        typer.echo(f"sync: {run['finished_at']} {'complete' if run['complete'] else 'INCOMPLETE'}")
        for error in json.loads(run["errors"]):
            typer.echo(f"  ! {error}")
    last = get_meta(c.conn, "last_prepare")
    if last:
        info = json.loads(last)
        typer.echo(f"prepare: {info['at']}, {info['prepared']} prepared, {len(info['failed'])} failed")
    else:
        typer.echo("prepare: never run")
    for row in c.conn.execute("SELECT * FROM tracked_issues WHERE sync_error IS NOT NULL"):
        typer.echo(f"  ! {review.ref(row)}: {row['sync_error']}")
    unknown = c.conn.execute(
        "SELECT o.id, t.project_path, t.iid FROM operation_logs o JOIN tracked_issues t ON t.id = o.issue_id WHERE o.status IN ('ambiguous','started')"
    ).fetchall()
    for op in unknown:
        typer.secho(f"  ! comment operation {op['id']} on {review.ref(op)} has an unknown outcome; it is reconciled on the next publish", fg=typer.colors.YELLOW)


@app.command("list")
@guarded
def list_issues(state: Optional[str] = typer.Option(None, "--state", help="pending | reviewing | done | deferred")):
    """List tracked issues."""
    c = Ctx()
    if state and state not in ("pending", "reviewing", "done", "deferred"):
        fail("--state must be pending, reviewing, done or deferred")
    rows = c.conn.execute(
        "SELECT * FROM tracked_issues WHERE (? IS NULL OR state = ?) ORDER BY activity_at DESC, project_id, iid", (state, state)
    ).fetchall()
    for row in rows:
        typer.echo(_line(row))


@app.command()
@guarded
def current():
    """Show the item under review."""
    c = Ctx()
    row = review.current(c.conn)
    if row is None:
        typer.echo("No current item.")
        return
    _show(c, row)


@app.command("open")
@guarded
def open_(issue: str):
    """Begin reviewing a specific item of today's queue."""
    c = Ctx()
    _show(c, review.open_issue(c.conn, c.issue(issue)))


@app.command("next")
@guarded
def next_():
    """Move to the next unresolved item. Does not mark anything done."""
    c = Ctx()
    row = review.next_issue(c.conn)
    if row is None:
        typer.echo("No unresolved items in today's queue.")
        return
    _show(c, row)


@app.command()
@guarded
def prev():
    """Move to the previous unresolved item. Does not mark anything done."""
    c = Ctx()
    row = review.next_issue(c.conn, back=True)
    if row is None:
        typer.echo("No unresolved items in today's queue.")
        return
    _show(c, row)


@app.command()
@guarded
def done(
    issue: str,
    decision: str = typer.Option("", "--decision", help="Decision summary for the daily summary."),
    action: list[str] = typer.Option([], "--action", help="Action item; repeatable."),
):
    """Complete the review of an issue."""
    c = Ctx()
    review.done(c.conn, c.cfg, c.issue(issue), c.now, decision, action)
    typer.echo(f"{issue}: done")
    _zero(c)


@app.command("follow-up")
@guarded
def follow_up(
    issue: str,
    when: str = typer.Argument(..., metavar="DATE", help="Future local date, YYYY-MM-DD."),
    reason: str = typer.Option("", "--reason"),
    decision: str = typer.Option("", "--decision"),
    action: list[str] = typer.Option([], "--action"),
):
    """Defer an issue until a date; this completes the current review."""
    c = Ctx()
    review.follow_up(c.conn, c.cfg, c.issue(issue), c.now, when, reason, decision, action)
    typer.echo(f"{issue}: deferred until {when}")
    _zero(c)


def _zero(c: Ctx) -> None:
    session = review.session_for(c.conn, c.cfg.today(c.now))
    if session and session["status"] == "completed":
        typer.echo("Inbox Zero: every item in today's queue is Done or Deferred.")
        _warn_sync(c)


@app.command()
@guarded
def brief(issue: str, path: bool = typer.Option(False, "--path", help="Print the file path instead of the content.")):
    """Show the review brief (Markdown)."""
    c = Ctx()
    file, warnings = agent.brief(c.conn, c.cfg, c.issue(issue), c.now)
    for warning in warnings:
        typer.secho(f"! {warning}", fg=typer.colors.YELLOW, err=True)
    typer.echo(str(file) if path else file.read_text())


@app.command()
@guarded
def chat(issue: str):
    """Resume the issue's Claude Code session interactively."""
    c = Ctx()
    row = c.issue(issue)
    commands, workdir = agent.chat_commands(c.conn, c.cfg, row)
    try:
        with agent.session_lock(c.cfg, row):
            code = subprocess.run(commands[0], cwd=workdir).returncode
            if code != 0:
                code = subprocess.run(commands[1], cwd=workdir).returncode
            if code == 0:
                agent.mark_created(c.conn, row)
            raise typer.Exit(code)
    except agent.Busy:
        fail(f"The session for {issue} is in use by another process (preparation or another chat).")


@app.command()
@guarded
def comment(
    issue: str,
    body: Optional[str] = typer.Option(None, "--body"),
    body_file: Optional[Path] = typer.Option(None, "--body-file", help="Read the comment from a file; '-' reads stdin."),
    publish: bool = typer.Option(False, "--publish", help="Post to GitLab. Use only on the user's explicit instruction."),
    accept_stale: bool = typer.Option(False, "--accept-stale", help="Post even though the issue changed since it was viewed."),
):
    """Save a comment draft, or post it with --publish. Never changes review state."""
    c = Ctx()
    if (body is None) == (body_file is None):
        fail("Give exactly one of --body or --body-file.")
    text = body if body is not None else (sys.stdin.read() if str(body_file) == "-" else body_file.read_text())
    row = c.issue(issue)
    if not publish:
        op_id = publishing.save_draft(c.conn, row, text, c.now)
        typer.echo(f"draft {op_id} saved for {issue}. NOT published; add --publish to post.")
        return
    op = publishing.publish(c.conn, c.cfg, row, c.adapter, text, c.now, accept_stale)
    typer.echo(f"published: {op['remote_url']}")


@app.command()
@guarded
def summary(day: Optional[str] = typer.Option(None, "--date", help="Local date, YYYY-MM-DD. Default: today.")):
    """Daily summary of published replies, decisions and action items."""
    c = Ctx()
    try:
        target = date.fromisoformat(day) if day else c.cfg.today(c.now)
    except ValueError:
        fail(f"--date '{day}' is not YYYY-MM-DD")
    typer.echo(summaries.daily(c.conn, c.cfg, target))


@app.command()
@guarded
def usage(
    missed_follow_ups: Optional[int] = typer.Option(None, "--missed-follow-ups", min=0),
    missed_issues: Optional[int] = typer.Option(None, "--missed-issues", min=0),
    friction: Optional[str] = typer.Option(None, "--friction", help="Context-recovery friction and overall experience."),
):
    """Record today's usage observations, or print the usage table when called without options."""
    c = Ctx()
    if missed_follow_ups is None and missed_issues is None and friction is None:
        typer.echo(summaries.usage_report(c.conn))
        return
    if missed_follow_ups is None or missed_issues is None or friction is None:
        fail("Record all three: --missed-follow-ups N --missed-issues N --friction TEXT.")
    session = review.session_for(c.conn, c.cfg.today(c.now))
    if session is None:
        fail("No review session today to attach the record to.")
    with tx(c.conn):
        summaries.record_usage(c.conn, session, missed_follow_ups, missed_issues, friction)
    typer.echo(f"recorded for {session['session_date']}")


@app.command()
@guarded
def schedule(fmt: str = typer.Option("launchd", "--format", help="launchd | cron | systemd")):
    """Print a weekday preparation schedule for the native OS scheduler."""
    c = Ctx()
    executable = shutil.which("inbox") or str(Path(sys.argv[0]).resolve())
    try:
        typer.echo(scheduling.render(c.cfg, fmt, executable))
    except ValueError as exc:
        fail(str(exc))
