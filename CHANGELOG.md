# Changelog

## 0.1.1

- New auth mode `workspace`: `awsuite auth login --workspace https://<portal>
  --bearer-env NAME [--provider google]` uses the Google connection you already
  made in an Aither workspace. The profile stores the URL, the provider and the
  variable NAME only; each fetch asks the workspace for your own access token and
  keeps it in memory until 60 s before it expires. 401, 403 (an admin has not
  enabled CLI hand-off) and 404 (not connected, with the connect link) are
  reported as `AuthError`s an agent can act on. `https://` is required except to
  loopback.
- Without `--bearer-env`, workspace mode uses the Aither sign-in `adk login` saved
  (`~/.aither/auth.json`); the local-root profile does not count.
- `awsuite doctor` reports a workspace profile's bearer source (set or not).
- `--self-test` covers the workspace https rule and refusal messages.

## 0.1.0

- First release: Gmail, Drive, Calendar, Docs, Sheets and the Admin Directory as
  one tool table rendered as a CLI, an MCP stdio server, an awdk toolpack and
  agent skills; `oauth`, `service-account` and `token` auth modes; every write
  is a dry run unless confirmed.
