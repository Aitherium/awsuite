---
name: suite-weekly-digest
description: Produce a weekly digest of the user's Google Workspace with awsuite - the week's important mail, meetings held and coming up, and documents that changed - optionally saved to a Google Doc. Use when the user asks for a weekly summary, digest, recap, or "what happened this week".
---

# Weekly digest (awsuite)

Uses the `suite_*` tools (MCP server `awsuite`, or the awdk toolpack).

## Steps

1. **Window.** Default: the last 7 days for looking back, the next 7 days for looking
   ahead. Use the user's window if they named one.
2. **Mail.** `suite_mail_search` with
   `query: "newer_than:7d -category:promotions -category:social"`, `max_results: 50`.
   Group by thread/sender; read (`suite_mail_read`) only threads that look like decisions,
   requests or deadlines.
3. **Meetings.** `suite_calendar_events` twice: past week (`time_min` = 7 days ago,
   `time_max` = now) and the coming week (defaults).
4. **Documents.** `suite_drive_search` with `q: "modifiedTime > '<RFC3339 7 days ago>'"`,
   `max_results: 20`.
5. **Write the digest** in this order, short lines:
   - **Decisions and deadlines** surfaced in mail.
   - **Waiting on you** - requests not yet answered.
   - **Meetings** - last week (count + notable), next week (each with time).
   - **Documents changed** - name, who, link.
6. **Optional save.** If the user wants it saved, call `suite_docs_create` (then
   `suite_docs_append`) - each WITHOUT `confirm` first to show the preview, then with
   `confirm: true` once approved.

## Rules

- Summarize; never paste whole emails or documents.
- If a service errors with a `ScopeError`, finish the digest with what you have and
  name the missing scope at the end.
