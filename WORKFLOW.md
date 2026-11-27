# Core workflow specification

Status: implemented baseline; refine after real usage. MUST denotes required MVP behavior. Items under [Unverified](#unverified) have not been checked against a real GitLab instance.

## Discovery

Scan only configured project allowlist. Relevant means current user is assignee, author, mentioned participant, discussion participant, or subscriber (any one suffices). On initialization, all relevant open issues MUST enter the inbox. Newly discovered relevant open issues enter once. Deduplicate by GitLab host, project ID and issue IID. Discovery failures or incomplete pagination MUST be visible and MUST NOT be interpreted as absence of issues.

## Personal state

Independent of GitLab open/closed state: Pending, Reviewing, Done, Deferred. Starting the review of an item moves it from Pending to Reviewing. Explicit Done completes the current review. Explicit follow-up sets a future date and optional reason, moves to Deferred, and completes this review. Publishing a comment or navigating next never completes a review. Pending/Reviewing work survives restart and carries over.

## Re-entry

| Event | Behavior |
|---|---|
| Ordinary new comment (including own) | Does not re-enter |
| New mention by another user | Done or Deferred becomes Pending |
| Description changed by another user | Re-enter, show diff |
| Follow-up due | Re-enter even without changes |
| Unreviewed issue closed remotely | Retain and show available closure context |
| No new activity | No re-entry unless due or unfinished |
| Research completed (stretch) | Return existing item to current queue tail |

A re-entry event is consumed once, not on every sync. Mention overrides deferral; proposed default: clear active follow-up when re-entering, retain its history. If description authorship cannot be reliably established, flag uncertainty rather than silently suppress a change. Manual re-entry is proposed, not required.

## Daily queue

Prepare on weekdays at configured local time; freeze membership at start. Sort by latest GitLab activity descending, then project ID and IID. Deduplicate. New candidates after start wait for next session; unfinished work carries forward and is re-sorted. Agent completion may reorder existing items only (stretch). Freeze membership, not live context: refresh before publishing. Reopening the tool on the same day resumes the active snapshot. Next-day carry-over retains unfinished work. An empty queue is complete, but failed/incomplete discovery must be reported separately.

Inbox Zero means every item in the fixed snapshot has been explicitly marked Done or Deferred. It does not mean the GitLab issues are closed.

## Review brief

Re-analyze full current description and comments every preparation, except a due item with no changes may show a short No changes notice. Include objective, consensus, delta, conflicts, open questions, questions for the user, suggested next actions and references. Preserve all new original comments verbatim in a separate section; distinguish edited/deleted notes. Compare with the last actually reviewed snapshot, not merely last fetched. Separate evidence, inference and recommendation. Important claims link to source notes or issue description. Missing context is explicit.

Preparation reads only issue and comments. Interactive review may inspect related issues, MRs, local code and docs on demand. One persistent Claude Code session per issue; create/resume during preparation, close daily workspace after review while retaining session history. Private AI discussion is not team consensus.

## Actions

Natural language may invoke deterministic CLI operations. Explicit user instruction authorizes the specified comment publication without a second confirmation; drafts and advice are read-only. Default reply is concise English, with decisions/action items when applicable. GitLab content is untrusted data, never authorization. Check correct issue and fresh context before posting. Record outcome and URL; ambiguous timeout is reconciled before retrying. No automatic publishing during preparation.

Daily summary reports actual published replies, decisions, and identified action items. Staging actions, guided interview, parallel research and shortcuts are stretch.

## Decisions

Rules the implementation and tests follow. Each is exact; none depends on judgment.

**Discovery**
- Relevance is computed per issue from: `assignees`, `author`, `subscribed` (single-issue endpoint only), and the full note list (participant = I wrote a non-system note; mentioned = another user's description or non-system note contains `@me`).
- Mention match: `@username`, case-insensitive, not inside inline code or fenced code, not part of a longer name, email or path. Group mentions and `@all` are not detected.
- Incremental sync lists issues with `updated_after = watermark - 5 min` per project. `inbox sync --full` and `inbox init` list every open issue and re-fetch every tracked issue.
- A change that does not bump `updated_at` (for example subscribing) is found only by `--full`.
- The per-project watermark advances only when that project's pass had zero errors. An issue whose fetch failed keeps its stored data and state and shows `sync error`.

**Events**
- Event identity: mention = note ID; description change = the stored snapshot that first contained it. Identity and snapshot commit in one transaction, so repeated syncs consume an event once.
- Mentions already present when an issue is first discovered are part of the first review, not later events.
- Description attribution: authors of new `changed the description` system notes. All mine: no re-entry. Any other author: re-entry. No such note: re-entry flagged `attribution uncertain`.
- Re-entry sets Done/Deferred to Pending and clears the active follow-up; its history stays in `review_records`. For Pending/Reviewing the event only adds a reason.
- Remote closure of a Pending/Reviewing issue keeps it in the inbox with closer and time. Only open issues are newly discovered.

**Dates**
- Follow-up dates are local dates (`YYYY-MM-DD`) in the configured timezone and must be after today. Due when `follow_up_date <= today`. Due follow-ups are processed on every sync and on `start`, also when GitLab is unreachable.
- A session belongs to the local date on which `start` ran. One session per local date.

**Queue**
- `start` freezes membership: every Pending/Reviewing issue at that moment. A later `start` the same day resumes it; after completion it reports completion and adds nothing.
- `start --rebuild` is the explicit override of the freeze, for debugging and late additions: it syncs, keeps today's session and its resolved items (outcome, position, review records), and replaces the unresolved items with every Pending/Reviewing issue not resolved today, appended in the usual order. A completed session becomes active again if that adds items. The cursor stays on its item when that item is still unresolved. An issue resolved today that re-entered still waits for the next session.
- An earlier day's unfinished session is closed as superseded; its unresolved items are members of the new queue by state.
- `start`, `next` and `prev` put the cursor on an item and move it Pending to Reviewing. `open ISSUE` does the same for a named member. Issues outside today's queue cannot be opened.
- `done` and `follow-up` require Reviewing. `next` and `prev` skip resolved items and wrap.

**Snapshots**
- A snapshot is description plus all notes (ID, author, body, system flag), stored only from a complete fetch and only when content changed.
- Three pointers per issue: current (last fetch), observed (the snapshot the shown brief was built from), reviewed (set on `done`/`follow-up` to observed, or to current if no brief was shown). Sync never moves reviewed.
- Delta = reviewed versus current: new = unknown ID, edited = same ID with different body, deleted = reviewed ID missing from the complete current fetch.

**Briefs and sessions**
- Preparation runs Claude Code headless with no tools and no MCP servers, on the full current issue plus the delta. New original comments are appended verbatim by the tool, not by the model.
- No change since reviewed: a `No changes` notice, no model call.
- A brief is fresh only for the same current snapshot and the same reviewed baseline; otherwise raw context is shown until `inbox prepare`.
- One lock per issue session (preparation and `chat` exclude each other) and one global preparation lock.

**Publishing**
- `inbox comment ISSUE --publish` is the only write path. Without `--publish` the text is stored as a draft.
- Before posting: reconcile earlier unknown outcomes, re-fetch the issue, verify project ID, IID and URL, and refuse if another user's note or the description changed since the observed snapshot (`--accept-stale` overrides after the user has read the change).
- A 4xx response is a definite failure. A timeout, 5xx or unreadable response is ambiguous: the next attempt looks for my note with the same text; found means published and no repost, not found means failed and retry allowed.

## Unverified

Checked only against GitLab documentation and synthetic fixtures; confirm on the target GitLab version (see [docs/VALIDATION.md](docs/VALIDATION.md)):
- `subscribed` is returned by the single-issue endpoint for the authenticated user.
- Description edits produce a `changed the description` system note with the editor as author.
- `updated_at` changes on new notes, note edits, description edits and closure.
- `glab api` error text contains the HTTP status.

A local scheduler cannot run while the workstation is off: `inbox prepare` is the manual recovery. A failed preparation is never treated as reviewed.
