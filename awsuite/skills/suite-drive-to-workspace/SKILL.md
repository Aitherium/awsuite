---
name: suite-drive-to-workspace
description: Find documents in the user's Google Drive and upload them into an Aither workspace (any portal exposing the documents API) so the workspace can answer questions over them. Use when the user asks to load, sync, import or push Drive files into their workspace or its knowledge base.
---

# Drive to an Aither workspace (awsuite)

An Aither workspace (any portal exposing the documents API) ingests documents through
`POST <base>/api/documents/upload` (multipart field `file`, optional form field
`doc_type`), authenticated by the user's workspace session. awsuite does the whole
transfer with one command, so the session credential never passes through the
conversation.

## Steps

1. **Find the files.** `suite_drive_search` with the user's terms (`text`) or a Drive
   query (`q`, e.g. `mimeType = 'application/vnd.google-apps.document'`). Show the
   matches (name, type, modified) and confirm which ones to push.
2. **Ask for the target.** The workspace base URL (https) and the NAME of the environment
   variable holding their session bearer (for example `WORKSPACE_TOKEN`). Never ask the
   user to paste the token into the chat.
3. **Dry run first** (shows exactly what would be uploaded, uploads nothing):

   ```bash
   awsuite drive push --id <FILE_ID> --id <FILE_ID> \
     --to https://workspace.example.com --bearer-env WORKSPACE_TOKEN --json
   ```

   or select by search: `--query "quarterly report" --limit 5`.
4. **Push** after the user approves the dry-run list: add `--confirm`. Optional
   `--doc-type <type>` sets the workspace's document type; omit it to use its default.
5. **Report** per file: uploaded, or skipped with the reason.

## What gets sent

- Google Docs and Slides are exported to `.txt`, Sheets to `.csv`.
- Other files are sent as-is when the workspace can extract text from them
  (`.pdf .docx .doc .txt .md .csv`); anything else is skipped and named.
- If the session uses a cookie rather than a bearer, use `--cookie-env NAME` instead.

## Rules

- Plain `http://` is refused except for a loopback address.
- A 401/403 from the workspace means the session expired: ask the user to sign in again
  and refresh the environment variable; do not retry in a loop.
