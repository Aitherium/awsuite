---
name: suite-meeting-prep
description: Prepare the user for an upcoming meeting with awsuite - read the calendar event, pull recent email threads with the attendees and related Drive documents, and produce a one-page brief. Use when the user asks to prep for a meeting, a call, or "my next meeting".
---

# Meeting prep (awsuite)

Uses the `suite_*` tools (MCP server `awsuite`, or the awdk toolpack). CLI equivalents:
`awsuite cal events`, `awsuite mail search`, `awsuite drive search`.

## Steps

1. **Find the meeting.** `suite_calendar_events` for the next 24 hours (or the window the
   user named). Pick the event the user meant; if ambiguous, list the candidates and ask.
2. **Who is in it.** Take the attendee addresses from the event (skip the user's own).
3. **Recent context.** For each attendee (max 5), `suite_mail_search` with
   `query: "from:<addr> OR to:<addr> newer_than:30d"`, `max_results: 5`. Read the one or two
   threads that look substantive with `suite_mail_read`.
4. **Documents.** `suite_drive_search` with `text` set to the meeting title's key terms
   (`max_results: 5`). Also follow any Drive links in the event description.
   `suite_drive_read` the most relevant one or two (`max_chars: 8000`).
5. **Write the brief**, at most one page:
   - Meeting: title, time, attendees, link.
   - Purpose (from the description, or inferred - say which).
   - Open threads with each attendee: one line each.
   - Key facts from the documents, with the doc name.
   - Three questions the user may want to ask.

## Rules

- Read-only. This skill never sends mail or edits the calendar.
- Say plainly when context is thin ("no recent mail with X") instead of padding.
