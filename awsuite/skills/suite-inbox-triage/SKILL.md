---
name: suite-inbox-triage
description: Triage the user's Gmail inbox with awsuite - find what needs a reply, what is waiting on others, and what can be ignored, then optionally draft replies (drafts only, never sent without explicit confirmation). Use when the user asks to triage, clean up, or summarize their inbox or unread mail.
---

# Inbox triage (awsuite)

Uses the `suite_*` tools (MCP server `awsuite`, or the awdk toolpack). If the tools
are not available, the same steps run through the CLI: `awsuite mail search ... --json`.

## Steps

1. **Check the account.** Call `suite_auth_status`. If it is not configured, tell the
   user to run `awsuite auth login --scopes mail` and stop.
2. **Pull the candidates.** `suite_mail_search` with
   `query: "in:inbox is:unread newer_than:3d"` and `max_results: 30`. If the user named a
   window or label, use it instead (Gmail query syntax: `newer_than:7d`, `label:x`,
   `from:y`, `-category:promotions`).
3. **Read only what matters.** For messages whose snippet does not make the action
   obvious, `suite_mail_read` them. Do not read every message.
4. **Sort into four buckets** and present them as a short list, one line each
   (sender, subject, why):
   - **Reply needed** - a direct question or request to the user.
   - **Waiting on others** - the user already acted; nothing to do now.
   - **FYI** - informational, no action.
   - **Noise** - newsletters, notifications, promotions.
5. **Offer drafts.** For "Reply needed" items, offer to draft replies. Call
   `suite_mail_draft` WITHOUT `confirm` first: it returns a preview. Show the preview,
   and only call it again with `confirm: true` after the user approves. Never call
   `suite_mail_send` from this skill unless the user explicitly asks to send.

## Rules

- Quote at most one line of any email; summarize the rest.
- Never forward or send content to anyone the user did not name.
- A `ScopeError` names the missing scope: relay the `awsuite auth login --scopes ...`
  command it suggests instead of retrying.
