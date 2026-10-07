# awsuite

Let an agent use a Google Workspace -- Gmail, Drive, Calendar, Docs, Sheets and
the Admin Directory -- with every write gated behind an explicit confirm.

One tool table, rendered onto every surface an agent reaches you through:

| surface | how |
|---|---|
| CLI | `awsuite mail search "is:unread"` |
| MCP server (Claude Code, any MCP client) | `claude mcp add awsuite -- awsuite mcp` |
| awdk toolpack | `awsuite pack install` |
| agent skills | `awsuite skills install` |

Stdlib only at runtime. `pip install "awsuite[sa]"` adds `cryptography`, used
only for service-account signing.

## Quickstart

```bash
pip install awsuite

# 1. Create an OAuth client of type "Desktop app" in Google Cloud Console and
#    download its client_secret.json. Then:
awsuite auth login --client-secret client_secret.json --scopes mail,drive,calendar

# 2. Use it.
awsuite mail search "is:unread newer_than:2d"
awsuite cal events
awsuite drive search "quarterly plan"
awsuite docs read <document-id>

# 3. Give it to your agent.
claude mcp add awsuite -- awsuite mcp
```

Read-only scopes are the default. `--write` adds the narrowest write scope per
service (`gmail.send`/`gmail.compose`, `drive.file`, `calendar.events`,
`documents`, `spreadsheets`). Logins are incremental: a second
`auth login --scopes docs --write` keeps what the profile already had.

## Writes never happen silently

`suite_mail_send`, `suite_mail_draft`, `suite_calendar_create`,
`suite_docs_create`, `suite_docs_append`, `suite_sheets_append` and
`suite_drive_upload` return a **dry-run preview** unless called with
`confirm: true` (CLI: `--confirm`). A dry run does not even construct a
client, so a preview cannot send.

```bash
awsuite mail send --to a@example.com --subject Hi --body "..."            # preview
awsuite mail send --to a@example.com --subject Hi --body "..." --confirm  # sends
```

## Tools

`awsuite tools` prints the table; `awsuite tools --json --schema` prints the
input schemas the MCP server and the toolpack both serve.

```
  suite_auth_status          suite_drive_search        suite_docs_read
  suite_mail_search          suite_drive_read          suite_docs_create      W
  suite_mail_read            suite_drive_upload     W  suite_docs_append      W
  suite_mail_labels          suite_calendar_events     suite_sheets_read
  suite_mail_draft        W  suite_calendar_freebusy   suite_sheets_append    W
  suite_mail_send         W  suite_calendar_create  W  suite_directory_users
```

## Three ways to authenticate

| mode | for | how |
|---|---|---|
| `oauth` (default) | a person's own account | loopback flow with PKCE (S256) on `127.0.0.1`; `--no-browser` prints the URL only. Client from `--client-secret` or `AWSUITE_GOOGLE_CLIENT_ID` / `AWSUITE_GOOGLE_CLIENT_SECRET` |
| `service-account` | a Workspace admin automating for users | `--mode service-account --key key.json --subject user@domain` (domain-wide delegation; needs `awsuite[sa]`) |
| `token` | a platform that already holds the grant | `AWSUITE_ACCESS_TOKEN`, or `--mode token --token-cmd "<command printing a token>"` |

`token` mode is the seam for hosting platforms: the platform hands the agent a
short-lived access token and the brick never stores a refresh credential.

Profiles live in `~/.aither/awsuite/<profile>.json` (override with
`AWSUITE_HOME`), written mode 0600 where the OS honours it. Token values are
never printed: `awsuite auth status` shows a masked fingerprint.

```bash
awsuite auth status      # who, which scopes, granted vs requested
awsuite auth scopes      # the service -> scope map
awsuite auth logout      # revoke and delete
```

## Errors an agent can act on

| error | means | do |
|---|---|---|
| `AuthError` | no credential, or it was rejected | `awsuite auth login` |
| `ScopeError` | valid credential, missing scope -- **the error names it** | `awsuite auth login --scopes <svc> [--write]` |
| `RateLimited` | 429 that survived bounded retries | wait |
| `NotFound` | wrong id, or not visible to this account | check the id |

429, rate-limit 403s, 5xx and connection errors are retried with bounded
backoff (honouring `Retry-After`). A 401 gets one forced refresh.

CLI exit codes: `0` done, `1` the provider refused or failed, `2` could not run
(not logged in, bad arguments).

## Agent surfaces

**MCP.** `awsuite mcp` speaks JSON-RPC 2.0 over stdio (newline-delimited):
`initialize`, `tools/list`, `tools/call`, `ping`.

```bash
claude mcp add awsuite -- awsuite mcp
claude mcp add awsuite-work -- awsuite mcp --profile work
```

**awdk toolpack.** `awsuite pack install` copies the pack to
`~/.aitheros/packs/awsuite/` (or `--dir`), where awdk discovers it; directories
listed in `AITHER_TOOLPACK_DIRS` are scanned too. Write tools register with
`action_class="write"`, so an awdk agent's own authorization applies on top of
the confirm gate.

**Skills.** `awsuite skills install` copies four skills to `~/.claude/skills/`
(or `--dir`): `suite-inbox-triage`, `suite-meeting-prep`, `suite-weekly-digest`
and `suite-drive-to-workspace`.

## Drive to an Aither workspace

```bash
awsuite drive push --query "q3 plan" --to https://workspace.example.com --bearer-env WORKSPACE_TOKEN
awsuite drive push --id <file-id> --to https://workspace.example.com --bearer-env WORKSPACE_TOKEN --confirm
```

Uploads Drive files to an Aither workspace (any portal exposing the documents
API: `POST /api/documents/upload`, multipart field `file`, form field `doc_type`).
Google Docs and Slides export to `.txt`, Sheets to `.csv`. The session
credential is read from the environment variable you NAME, so it never lands in
argv or shell history; plain `http://` is refused except to loopback. Without
`--confirm` it is a dry run.

## Doctor and self-test

```bash
awsuite doctor          # python, token-file perms, scopes granted vs requested,
                        # token endpoint reachable within 5 s
awsuite --self-test     # offline proof: PKCE (RFC 7636 vector), table parity across
                        # MCP/toolpack/CLI, dry-run gating, masking, MCP over a pipe
```

Both exit `0` proven, `1` a check failed, `2` could not judge.

## It pairs with awmail; it does not replace it

```
awmail  -- an agent's OWN mailbox: an address it sends from and receives at
awsuite -- the USER's workspace: their mail, files, calendar and documents
```

An agent that needs an inbox of its own wants awmail. An agent working on your
behalf, inside your Google account, wants awsuite. Microsoft 365 is not
implemented yet; the provider layer is shaped for it.

## Licence

Apache-2.0
