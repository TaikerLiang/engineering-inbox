# Core workflow specification

Status: initial baseline; refine after real usage. MUST denotes required MVP behavior.

## Discovery

Scan only configured project allowlist. Relevant means current user is assignee, author, mentioned participant, discussion participant, or subscriber (any one suffices). On initialization, all relevant open issues MUST enter the inbox. Newly discovered relevant open issues enter once. Deduplicate by GitLab host, project ID and issue IID. Discovery failures or incomplete pagination MUST be visible and MUST NOT be interpreted as absence of issues.

## Personal state

Independent of GitLab open/closed state: Pending, Reviewing, Done, Deferred. Start review moves Pending to Reviewing. Explicit Done completes the current review. Explicit follow-up sets a future date and optional reason, moves to Deferred, and completes this review. Publishing a comment or navigating next never completes a review. Pending/Reviewing work survives restart and carries over.

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

## Open decisions

Verify discovery coverage, mention parsing, description change attribution and closure evidence against supported GitLab versions. Specify exact event cursor and date boundary in implementation tests. A local scheduler cannot run while a workstation is off: provide manual recovery. Do not treat failed preparation as reviewed.
