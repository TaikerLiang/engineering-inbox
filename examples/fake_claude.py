#!/usr/bin/env python3
"""Stand-in for headless Claude Code in the smoke test. It reads the prompt and prints a
canned brief in the headless JSON shape. It performs no analysis."""
import json
import sys

sys.stdin.read()
sections = ["Objective", "Consensus", "Delta", "Conflicts", "Open questions", "Questions for you", "Suggested next actions", "References"]
brief = "\n\n".join(f"## {name}\n[inference] Placeholder from examples/fake_claude.py; no analysis was run." for name in sections)
print(json.dumps({"type": "result", "is_error": False, "result": brief}))
