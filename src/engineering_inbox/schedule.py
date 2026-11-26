"""Scheduler definitions for weekday preparation. Printed for the user to install; the
schedule uses the machine's local time, so keep it equal to the configured timezone."""
from __future__ import annotations

from .config import Config

LABEL = "local.engineering-inbox.prepare"


def render(cfg: Config, fmt: str, executable: str) -> str:
    hour, minute = (int(part) for part in cfg.prepare_time.split(":")[:2])
    env = f"ENGINEERING_INBOX_HOME={cfg.home}"
    log = cfg.home / "prepare.log"
    if fmt == "cron":
        return f"{minute} {hour} * * 1-5 {env} {executable} prepare >> {log} 2>&1\n"
    if fmt == "systemd":
        return (
            f"# ~/.config/systemd/user/engineering-inbox.service\n[Unit]\nDescription=Engineering Inbox preparation\n\n"
            f"[Service]\nType=oneshot\nEnvironment={env}\nExecStart={executable} prepare\n\n"
            f"# ~/.config/systemd/user/engineering-inbox.timer\n[Unit]\nDescription=Engineering Inbox weekday preparation\n\n"
            f"[Timer]\nOnCalendar=Mon..Fri {hour:02d}:{minute:02d}\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n"
        )
    if fmt == "launchd":
        slots = "".join(
            f"    <dict><key>Weekday</key><integer>{d}</integer><key>Hour</key><integer>{hour}</integer>"
            f"<key>Minute</key><integer>{minute}</integer></dict>\n"
            for d in range(1, 6)
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            f"<!-- ~/Library/LaunchAgents/{LABEL}.plist -->\n"
            '<plist version="1.0">\n<dict>\n'
            f"  <key>Label</key><string>{LABEL}</string>\n"
            f"  <key>ProgramArguments</key>\n  <array><string>{executable}</string><string>prepare</string></array>\n"
            f"  <key>EnvironmentVariables</key>\n  <dict><key>ENGINEERING_INBOX_HOME</key><string>{cfg.home}</string></dict>\n"
            f"  <key>StartCalendarInterval</key>\n  <array>\n{slots}  </array>\n"
            f"  <key>StandardOutPath</key><string>{log}</string>\n"
            f"  <key>StandardErrorPath</key><string>{log}</string>\n"
            "</dict>\n</plist>\n"
        )
    raise ValueError(f"Unknown format '{fmt}'. Use launchd, cron or systemd.")
