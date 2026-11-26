# MVP scope and delivery

Goal: validate that personal inbox state and follow-up fit daily engineering work. Target one development week followed by five working days of actual use; estimates, not deadlines.

## Required

1. Cross-project issue discovery.
2. SQLite personal state.
3. Daily queue snapshot.
4. Done/follow-up/re-entry policy.
5. Comment delta and description diff.
6. Claude Code session mapping and resume.
7. Markdown review brief with references.
8. Authorized GitLab comment publishing.
9. Daily review summary.
10. Local scheduled preparation.

Manual Herdr split panes and Markdown viewer are part of the initial experience.

## Stretch

Herdr workspace automation; concurrent interactive research; keyboard shortcuts; action item staging; guided interview mode.

## Later

Full GitLab issue operations; cross-project semantic search; advanced code exploration; dedicated web console; multi-user support (not a current product goal).

## Acceptance

Deterministic tests:
- AC-01 Allowlist and all relevant open issues at initialization; incomplete coverage visible.
- AC-02 Deduplicate by host/project/IID.
- AC-03 Restart preserves state, follow-up, queue.
- AC-04 Comment publishing and next do not mark done.
- AC-05 Ordinary comments do not re-enter.
- AC-06 New mentions re-enter Done/Deferred exactly once.
- AC-07 Description changes re-enter with diff.
- AC-08 Due follow-ups re-enter even unchanged.
- AC-09 Unreviewed remotely closed items remain visible.
- AC-10 Fixed membership; new events deferred to next queue.
- AC-11 Carry over unfinished items, re-sort deterministically.
- AC-12 New, edited and deleted notes compared against last reviewed snapshot.
- AC-13 Correct Claude session restored across restarts.
- AC-14 Explicit publication only; log outcome/reference; reconcile ambiguous errors.
- AC-15 Failed sync/preparation cannot lose pending work or falsely report completeness.
- AC-16 Inbox Zero only when every queue item is Done/Deferred.

Agent quality: required brief sections, grounded clickable references, separation of fact/inference/recommendation, correct open questions and conflicts, verbatim new notes, concise English reply, no unauthorized writes, no-change fast path, explicit incomplete context. Use at least five synthetic or sanitized fixtures, manual semantic review initially.

Test map (`tests/`): AC-01, AC-02 `test_discovery.py` · AC-03, AC-04 `test_state.py`, `test_publish.py` · AC-05 to AC-09, AC-15 `test_reentry.py` · AC-10, AC-11, AC-16 `test_queue.py` · AC-12 `test_delta.py` · AC-13 `test_agent.py` · AC-14 `test_publish.py`. Synthetic fixtures for agent review: `examples/gitlab.fixture.json`.

## Roadmap

Day 1: workflow baseline and integration spikes (glab discovery and Claude resume).
Day 2: SQLite state, event detection, follow-up tests.
Day 3: queue, carry-over, delta/diff, CLI.
Day 4: agent preparation, briefs, resume, publishing.
Day 5: manual Herdr workspace, local scheduler, summary and release smoke test.
Next five working days: use daily and refine workflow.

## Usage validation

Record duration, candidates/reviewed, missed follow-ups, missed eligible issues, context recovery friction and overall experience. 30 minutes is a target, not a release gate, especially with initial backlog. Primary acceptance is five days of actual use and a judgment that the workflow fits. Follow-up reliability and queue correctness are hard correctness goals. Do not claim validation until performed.
