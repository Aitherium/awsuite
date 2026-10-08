# awsuite

<!-- aither-header:start GENERATED from the ecosystem registry. Edits here are overwritten; change the registry instead. -->

[Source](https://github.com/Aitherium/awsuite)  ·  [The Aither World](https://aitherium.github.io/)

> **The Aither World** is an operating system for agents — a Linux you can hand to one, the runtimes it works in, and the tools it works with. [awnix](https://github.com/Aitherium/awnix) is the Linux underneath it; **awsuite** is one of its 67 bricks — each installs on its own, runs offline, and needs no account.
>
> **Start here:** Sign in with your own Google OAuth client and search your inbox from the CLI.

<!-- aither-header:end -->

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

## Four ways to authenticate

| mode | for | how |
|---|---|---|
| `oauth` (default) | a person's own account | loopback flow with PKCE (S256) on `127.0.0.1`; `--no-browser` prints the URL only. Client from `--client-secret` or `AWSUITE_GOOGLE_CLIENT_ID` / `AWSUITE_GOOGLE_CLIENT_SECRET` |
| `service-account` | a Workspace admin automating for users | `--mode service-account --key key.json --subject user@domain` (domain-wide delegation; needs `awsuite[sa]`) |
| `token` | a platform that already holds the grant | `AWSUITE_ACCESS_TOKEN`, or `--mode token --token-cmd "<command printing a token>"` |
| `workspace` | you already connected Google in an Aither workspace | `--workspace https://<portal> --bearer-env NAME` (below) |

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

## Use your workspace's Google connection

If your organisation runs an Aither workspace and you have connected Google
there, awsuite can use that same connection: no Google Cloud project, no second
OAuth client, no refresh token on your machine.

```bash
adk login                         # once: your Aither sign-in (pip install awdk)
awsuite auth login --workspace https://workspace.example.com
awsuite mail search "is:unread newer_than:1d"
```

- With no `--bearer-env`, awsuite sends the Aither sign-in `adk login` saved
  (`~/.aither/auth.json`). To use another credential, put it in an environment
  variable and pass `--bearer-env NAME`.
- The profile stores the workspace URL, the provider and (if given) the NAME of
  the variable -- never the bearer or a Google token.
- The workspace accepts this route only with an `Authorization` bearer and no
  browser cookies, so a web page cannot use it to read your token.
- Each fetch calls `GET <workspace>/api/connectors/google/token` with your bearer
  and keeps the answer in memory only, until 60 s before it expires. The
  workspace hands back your own token, never anyone else's.
- An admin must first allow it: Connectors -> Google Workspace -> "Allow members
  to use this connection from their CLI/agents". Until then the call fails with
  an `AuthError` that says so.
- `not connected` means you have not connected Google in the workspace yet; the
  error prints the link that starts it.
- Gmail is available only if the workspace admin ticked "Include Gmail" and you
  re-connected afterwards; otherwise mail tools raise a `ScopeError`.
- `https://` is required, except to `127.0.0.1` / `localhost`.

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
                        # workspace-mode bearer variable set or not,
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

<!-- aither-ecosystem:start GENERATED from the ecosystem registry. Edits here are overwritten; change the registry instead. -->

## The aw family

Standalone tools that share one idea: **replace something you would otherwise have to _trust_ with something you can _check_.**

Each installs on its own, works offline, and needs no account.

| | instead of trusting | you check |
|---|---|---|
| [awdk](https://github.com/Aitherium/awdk) | a framework's idea of how your agents should run | one loop you can read, pointed at a backend you already pay for |
| [awskills](https://github.com/Aitherium/awskills) | that an agent knows your procedure | the procedure written down, versioned, and loadable by any agent |
| [awpack](https://github.com/Aitherium/awpack) | that the pack you want shipped inside somebody's SDK, under whatever licence that SDK happens to carry | the pack as its own versioned artifact, with its own licence, that any agent runtime can install |
| [awm](https://github.com/Aitherium/awm) | that memory stayed in its lane | tenant:user:project scopes, so a write cannot cross a boundary |
| [awdesk](https://github.com/Aitherium/awdesk) | that the agent is somewhere behind a browser tab | a tray icon, a face on your desktop, and the decision card that pops when it needs you |
| [awnode](https://github.com/Aitherium/awnode) | a vendor's cloud with every prompt | a local gateway routing to backends you chose |
| [awgraph](https://github.com/Aitherium/awgraph) | that grep found everything | an AST + tree-sitter call graph an agent can traverse |
| [awgit](https://github.com/Aitherium/awgit) | that no one else is editing this file | a lease, refused at commit time if you do not hold it |
| [awdelphi](https://github.com/Aitherium/awdelphi) | one agent's confident take on a decision | the round trace, the anonymity, and who dissents |
| [awclassify](https://github.com/Aitherium/awclassify) | a filename, a folder, or whoever last touched it | doc_type, visibility, audience and topics, with the evidence lines that decided each |
| [awdecide](https://github.com/Aitherium/awdecide) | a hosted classifier's probability that never learns whether it was right | the decision, its probability, and the calibration curve from your own resolved outcomes |
| [awtoll](https://github.com/Aitherium/awtoll) | that your tooling is saving you context | the measured token cost of each tool call, and what the alternative cost |
| [awseal](https://github.com/Aitherium/awseal) | that the artifact came from who you think | an Ed25519 seal — the key that verifies is not the key that forges |
| [awshare](https://github.com/Aitherium/awshare) | that the download is intact | content-addressed bundles, verified on fetch |
| **awsuite** _(you are here)_ | that an agent holding your mailbox will not send on its own | every send, draft, upload and create returns a dry-run until confirm is true |
| [awnest](https://github.com/Aitherium/awnest) | that there is a person on the other end | a verdict with evidence, where "we could not tell" is not "yes" |
| [awrena](https://github.com/Aitherium/awrena) | a leaderboard someone can edit, and votes nobody counted | a scored duel with both answers kept, and a result bound to them |
| [awnboard](https://github.com/Aitherium/awnboard) | a share link anyone who sees it can use | an invitation addressed to one person, for one gate, revocable |
| [awnix](https://github.com/Aitherium/awnix) | that the box is what you left it as | an immutable image you built, with atomic rollback |
| [awrecover](https://github.com/Aitherium/awrecover) | that the restore worked | a restore that fully lands or does not land at all |
| [awstorage](https://github.com/Aitherium/awstorage) | a du you ran last month, and a peers file that says 3 TB free | an inventory snapshot per node with a diff since the last one, and each tree classified re-fetchable or not |
| [awrelay](https://github.com/Aitherium/awrelay) | a SaaS in the middle of your agents | findings, alerts and coordination over your own transport |
| [awask](https://github.com/Aitherium/awask) | that anyone read the paragraph where you asked | the ask itself, with a button that steers the session that raised it |
| [awmail](https://github.com/Aitherium/awmail) | a mailbox somebody else can read | mail your agents send and receive over your own server |
| [awswarm](https://github.com/Aitherium/awswarm) | that a model either fits your GPU or it doesn't run at all | a placement plan and an acquisition-probability estimate before you spend on a run |
| [awfind](https://github.com/Aitherium/awfind) | one vendor's idea of the web | results from whichever providers you configured |
| [awbrowse](https://github.com/Aitherium/awbrowse) | that the page said what you were told | the render, the DOM and the requests it made |
| [awvoice](https://github.com/Aitherium/awvoice) | that a cloud vendor may hold your audio | a transcript and a wav from a service you host |
| [awvision](https://github.com/Aitherium/awvision) | a filename and a caption somebody wrote | what a model actually reports about the pixels |
| [awscreen](https://github.com/Aitherium/awscreen) | a selector that was true when the page was written | the elements actually rendered, by what they look like |
| [awbeads](https://github.com/Aitherium/awbeads) | that a layout your users built survives the next deploy | the arrangement as data you can read back, diff, and hand to another surface |
| [awbonsai](https://github.com/Aitherium/awbonsai) | that inference always means a request left the machine | a WebGPU model answering on the tab's own GPU, with a consent record logged before it ever loaded |
| [gawbbonet](https://github.com/Aitherium/gawbbonet) | the model to keep a 300-message campaign coherent by itself | campaign facts recalled from scoped memory you can list and edit |
| [aitherkvcache](https://github.com/Aitherium/aitherkvcache) | a vendor's quantisation defaults | sub-byte KV cache kernels you can benchmark yourself |
| [awrtifact](https://github.com/Aitherium/awrtifact) | a hand-rolled split script and a hand-edited worker manifest | byte-verified parts in a release, served with Range + CORS, sizes asserted by a live gate |
| [AitherZero](https://github.com/Aitherium/AitherZero) | a pile of scripts nobody has numbered | numbered, discoverable automation with declarative playbooks |
| [AitherConnect](https://github.com/Aitherium/AitherConnect) | what a page tells your browser to do | a federated search and desktop bridge you host |
| [awreason](https://github.com/Aitherium/awreason) | a confident paragraph | the phases it went through, and every tool call it made to get there |
| [awrecurse](https://github.com/Aitherium/awrecurse) | that everything you pasted in was actually read | which slices it opened, and what it concluded from each |
| [awprism](https://github.com/Aitherium/awprism) | the first explanation that fits | the ranked alternatives, and the observation that separates them |
| [awrepl](https://github.com/Aitherium/awrepl) | what the agent believes the value is | the value, printed from the live session |
| [awreport](https://github.com/Aitherium/awreport) | that the report you pasted carried no token in it | a redacted report, and the duplicate it merged into instead of filing twice |
| [awresearch](https://github.com/Aitherium/awresearch) | a summary of pages nobody opened | every claim against the source it came from |
| [awfocus](https://github.com/Aitherium/awfocus) | twelve terminal tabs and a bad memory | one command that names every session, finds any transcript, and opens or steers the one you want |
| [awgym](https://github.com/Aitherium/awgym) | that a world model learned anything from the games it saw | transitions captured from real play, fed back, and the retrodiction score falling on grids it never saw |
| [awpredict](https://github.com/Aitherium/awpredict) | a model because it trained without erroring | its prediction against a self-updating lookup, on the rows that are actually novel |
| [awevolve](https://github.com/Aitherium/awevolve) | that your optimisation loop is finding anything | every version it kept, the score that version earned, and the edit that produced it |
| [awsh](https://github.com/Aitherium/awsh) | that you already know the name of the command | what it decided your line meant, before it acts on it |
| [awmine](https://github.com/Aitherium/awmine) | that a session's lesson survived the session | a row per outcome, a candidate per lesson, and the transcript line each one came from |
| [awrise](https://github.com/Aitherium/awrise) | that a scheduled agent ran at all, and ran exactly once | a durable record of every wake -- fired, skipped, overlapped or timed out -- each with its reason |
| [awkno](https://github.com/Aitherium/awkno) | that the docs site is up, or that you remember the family | the whole ecosystem in your terminal, with no network at all |
| [awwall](https://github.com/Aitherium/awwall) | that a service only talks to the hosts you think it talks to | an explicit egress allowlist, where a denial names the rule that denied it |
| [awembed](https://github.com/Aitherium/awembed) | a general-purpose embedder that has never seen your code | a held-out split of whole directories, scored teacher vs student vs int8 |
| [awtax](https://github.com/Aitherium/awtax) | a closed tax app's sealed file you can never read again | a plain, provider-neutral schema of every figure, with the page it came from |
| [awsettings](https://github.com/Aitherium/awsettings) | that you will remember to re-approve the same thing on every box you work from | one profile, unioned rather than overwritten, with the credentials left behind |
| [awavatar](https://github.com/Aitherium/awavatar) | a cloud 3D vendor's opaque task id | a manifest with a sha256, a licence and a rig-audit verdict per file |

[**awnix**](https://github.com/Aitherium/awnix) is the ground floor — A Linux you can hand to an agent — immutable base, capabilities included.

## The Aitherium ecosystem

Every repository here is public. Each publishes an `aither-manifest.json` beside its page, so any surface can read every sibling's — the network is browsable from any node in it.

| repo | what it is | pages |
|---|---|---|
| [awdk](https://github.com/Aitherium/awdk) | Build AI agent fleets — 3 lines, any backend, local or cloud | [docs](https://aitherium.github.io/awdk/) |
| [awskills](https://github.com/Aitherium/awskills) | Portable agent skills — self-contained procedures an agent loads on demand | [docs](https://aitherium.github.io/awskills/) |
| [awpack](https://github.com/Aitherium/awpack) | First-party agent packs — the ones we build, versioned and installable on their own | [docs](https://aitherium.github.io/awpack/) |
| [awm](https://github.com/Aitherium/awm) | A portable, scoped agent memory | [docs](https://aitherium.github.io/awm/) |
| [awdesk](https://github.com/Aitherium/awdesk) | Aither World Desk -- the desktop body of AitherOS Online: tray, avatars, decision cards, the Living Desktop as an overlay | [docs](https://aitherium.github.io/awdesk/) |
| [awnode](https://github.com/Aitherium/awnode) | A lightweight local gateway — bridges your apps to the AI backends you chose | [docs](https://aitherium.github.io/awnode/) |
| [awrun](https://github.com/Aitherium/awrun) | A priority-aware queue and dispatcher for agentic runs and ad-hoc CI builds. It also judges whether the runner pool is big enough for the queue it is draining, and can ask a host to grow it -- reserving capacity is zero-sum, so a saturated pool needs more of it, not a different share of it | [docs](https://aitherium.github.io/awrun/) |
| [awgraph](https://github.com/Aitherium/awgraph) | A semantic code graph for agents — AST + tree-sitter, call graphs | [docs](https://aitherium.github.io/awgraph/) |
| [awgit](https://github.com/Aitherium/awgit) | Semantic version control on top of git — edit-ops and leases | [docs](https://aitherium.github.io/awgit/) |
| [awdelphi](https://github.com/Aitherium/awdelphi) | Anonymous multi-round expert panels — a converged answer with a trace | [docs](https://aitherium.github.io/awdelphi/) |
| [awclassify](https://github.com/Aitherium/awclassify) | Classify any document -- what it is, who may read it, who it is for, what it is about | — |
| [awdecide](https://github.com/Aitherium/awdecide) | One typed-decision contract -- choice / score / bool with a probability -- over a ladder of backends you already run (rules, tiny local models, an LLM's logprobs), fail-closed, with a Brier ledger that resolves every decision against its outcome | — |
| [awtoll](https://github.com/Aitherium/awtoll) | What every tool call costs you in context, measured from your own transcripts | [docs](https://aitherium.github.io/awtoll/) |
| [awseal](https://github.com/Aitherium/awseal) | Sign an artifact so a stranger can verify it | [docs](https://aitherium.github.io/awseal/) |
| [awshare](https://github.com/Aitherium/awshare) | Publish an artifact and fetch it back verified | [docs](https://aitherium.github.io/awshare/) |
| **awsuite** _(you are here)_ | Your Google Workspace as agent tools, and no write happens without a yes | — |
| [awdit](https://github.com/Aitherium/awdit) | An append-only audit trail whose gaps are DETECTABLE | [docs](https://aitherium.github.io/awdit/) |
| [awbac](https://github.com/Aitherium/awbac) | Role-based access control that fails closed and explains itself | [docs](https://aitherium.github.io/awbac/) |
| [awiam](https://github.com/Aitherium/awiam) | Who is this caller? A directory and session store that fails honestly | [docs](https://aitherium.github.io/awiam/) |
| [awtunnel](https://github.com/Aitherium/awtunnel) | Reach a service that has no public address | [docs](https://aitherium.github.io/awtunnel/) |
| [awnest](https://github.com/Aitherium/awnest) | Prove there is a human before you let them into the nest | [docs](https://aitherium.github.io/awnest/) |
| [awrena](https://github.com/Aitherium/awrena) | Put two agents head to head and get a verdict you can check | [docs](https://aitherium.github.io/awrena/) |
| [awnboard](https://github.com/Aitherium/awnboard) | A front gate you can put in front of anything, and hand someone the key to | [docs](https://aitherium.github.io/awnboard/) |
| [awnix](https://github.com/Aitherium/awnix) | A Linux you can hand to an agent — immutable base, capabilities included | [docs](https://aitherium.github.io/awnix/) |
| [awrecover](https://github.com/Aitherium/awrecover) | Labelled snapshots with an all-or-nothing restore | [docs](https://aitherium.github.io/awrecover/) |
| [awstorage](https://github.com/Aitherium/awstorage) | Every drive on every node, indexed, classified and diffed -- so you can see what you own before you delete it | [docs](https://aitherium.github.io/awstorage/) |
| [awrelay](https://github.com/Aitherium/awrelay) | Portable agent messaging — findings, alerts, coordination | [docs](https://aitherium.github.io/awrelay/) |
| [awask](https://github.com/Aitherium/awask) | Your agent asks you a question — and acts on your answer | [docs](https://aitherium.github.io/awask/) |
| [awmail](https://github.com/Aitherium/awmail) | Give an agent an email address — send, and actually receive | [docs](https://aitherium.github.io/awmail/) |
| [awnet](https://github.com/Aitherium/awnet) | The agentic web — agents host a mesh, and agents join one | [docs](https://aitherium.github.io/awnet/) |
| [awswarm](https://github.com/Aitherium/awswarm) | Run one model too big for any single GPU across a pool of small ones | — |
| [awfind](https://github.com/Aitherium/awfind) | A portable search client — query, results, ranking | [docs](https://aitherium.github.io/awfind/) |
| [awbrowse](https://github.com/Aitherium/awbrowse) | A portable browser client — navigate, console, network, DOM, screenshot | [docs](https://aitherium.github.io/awbrowse/) |
| [awvoice](https://github.com/Aitherium/awvoice) | Hear and speak — transcribe audio, synthesize a voice | [docs](https://aitherium.github.io/awvoice/) |
| [awvision](https://github.com/Aitherium/awvision) | See an image — describe it, ask it a question, compare two | [docs](https://aitherium.github.io/awvision/) |
| [awscreen](https://github.com/Aitherium/awscreen) | See this machine — what is on screen, and where to click it | [docs](https://aitherium.github.io/awscreen/) |
| [awkit](https://github.com/Aitherium/awkit) | Render an agent panel from a tool result — one component, any React app | — |
| [awbeads](https://github.com/Aitherium/awbeads) | A spatial canvas for a page — arrange things, connect them, and keep the arrangement | — |
| [awbonsai](https://github.com/Aitherium/awbonsai) | Run a real model in the visitor's own browser — no server round trip, no upload | — |
| [awknowledge](https://github.com/Aitherium/awknowledge) | How to run a coding agent so the result survives — the laws, with evidence | [docs](https://aitherium.github.io/awknowledge/) |
| [awbrain](https://github.com/Aitherium/awbrain) | Your history as a wiki of linked markdown — claims pinned to the evidence | — |
| [gawbbonet](https://github.com/Aitherium/gawbbonet) | GobboNet campaigns with a real agent brain — scoped memory, graph recall | [docs](https://aitherium.github.io/gawbbonet/) |
| [aitherkvcache](https://github.com/Aitherium/aitherkvcache) | Near-optimal KV cache quantization for LLM inference — sub-byte compression | [docs](https://aitherium.github.io/aitherkvcache/) |
| [awrtifact](https://github.com/Aitherium/awrtifact) | Deliberately chunk artifacts into GitHub release assets — the productized aitherkvcache mirror lane | [docs](https://aitherium.github.io/awrtifact/) |
| [AitherZero](https://github.com/Aitherium/AitherZero) | PowerShell 7+ automation framework — numbered, self-describing scripts | [docs](https://aitherium.github.io/AitherZero/) |
| [AitherConnect](https://github.com/Aitherium/AitherConnect) | Browser extension — federated AI search, page context, and the Living OS overlay | [docs](https://aitherium.github.io/AitherConnect/) |
| [awreason](https://github.com/Aitherium/awreason) | A portable reasoning client — sessions, phases, thoughts, and the chain that produced the answer | [docs](https://aitherium.github.io/awreason/) |
| [awrecurse](https://github.com/Aitherium/awrecurse) | Answer a question over a context far larger than the window — recursively, with the trace kept | [docs](https://aitherium.github.io/awrecurse/) |
| [awprism](https://github.com/Aitherium/awprism) | Turn a failure into ranked hypotheses — and say what would confirm each one | [docs](https://aitherium.github.io/awprism/) |
| [awrepl](https://github.com/Aitherium/awrepl) | A REPL an agent can actually use — state that survives between turns | [docs](https://aitherium.github.io/awrepl/) |
| [awreport](https://github.com/Aitherium/awreport) | File a bug report that has already scrubbed your secrets and collapsed the duplicate | — |
| [awresearch](https://github.com/Aitherium/awresearch) | Ask a research question, get a cited report you can check | [docs](https://aitherium.github.io/awresearch/) |
| [awfocus](https://github.com/Aitherium/awfocus) | See, search and steer every Claude session from one command | [docs](https://aitherium.github.io/awfocus/) |
| [awgym](https://github.com/Aitherium/awgym) | An ARC training gym — a game a world model can watch, and six roles that play through it | [docs](https://aitherium.github.io/awgym/) |
| [awpredict](https://github.com/Aitherium/awpredict) | Predict what your environment does next, and how surprised you were | [docs](https://aitherium.github.io/awpredict/) |
| [awevolve](https://github.com/Aitherium/awevolve) | Point an agent at a file and a command that scores it, and let it improve | — |
| [awsh](https://github.com/Aitherium/awsh) | Your terminal answers you -- type a question where a command would go | [docs](https://aitherium.github.io/awsh/) |
| [awmine](https://github.com/Aitherium/awmine) | Mine what your agents did -- outcomes, lessons and procedures out of the transcripts they left behind | — |
| [awrise](https://github.com/Aitherium/awrise) | Wake an agent on a schedule, let it do one thing, and put it back to sleep | [docs](https://aitherium.github.io/awrise/) |
| [awkno](https://github.com/Aitherium/awkno) | The man page for the Aither World — every brick, stack and law, offline | [docs](https://aitherium.github.io/awkno/) |
| [awwall](https://github.com/Aitherium/awwall) | Say what a workload may reach, and watch everything else fail closed | [docs](https://aitherium.github.io/awwall/) |
| [awrouter](https://github.com/Aitherium/awrouter) | OpenRouter for your own fleet: pick a model backend by cost/latency/ capability, fail over, fit the context window, stream. Standalone, OpenAI-compatible, no Aither-specifics required to be valuable | — |
| [awembed](https://github.com/Aitherium/awembed) | Train an embedding model that knows your corpus, and prove it beats the big one | [docs](https://aitherium.github.io/awembed/) |
| [awtax](https://github.com/Aitherium/awtax) | Turn any tax PDF -- returns, W-2, 1099, statements, even scans -- into structured data you can check | [docs](https://aitherium.github.io/awtax/) |
| [awflow](https://github.com/Aitherium/awflow) | A deterministic workflow runtime — chain agent calls with journal replay and budget control | [docs](https://aitherium.github.io/awflow/) |
| [awsettings](https://github.com/Aitherium/awsettings) | Your agent's permissions and config, following you to the next machine | [docs](https://aitherium.github.io/awsettings/) |
| [awavatar](https://github.com/Aitherium/awavatar) | One character spec in, a rigged, animated, multi-style avatar pack out | [docs](https://aitherium.github.io/awavatar/) |

**Built on** [llama.cpp](https://github.com/ggml-org/llama.cpp) · [vLLM](https://github.com/vllm-project/vllm) · [ComfyUI](https://github.com/comfyanonymous/ComfyUI) · [CentOS Stream](https://www.centos.org/centos-stream/) · [Podman](https://github.com/containers/podman) · [Docker](https://github.com/moby/moby) · [LanceDB](https://github.com/lancedb/lancedb) · [WireGuard](https://www.wireguard.com/) · [FFmpeg](https://ffmpeg.org/) · [Blender + Rigify](https://www.blender.org/) · [headroom](https://github.com/headroomlabs-ai/headroom) · [SANA](https://github.com/NVlabs/Sana) · [Hunyuan3D](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1) · [repowise](https://github.com/repowise-dev/repowise) · [Playwright](https://github.com/microsoft/playwright) · [Chromium](https://www.chromium.org/) · [Next.js](https://github.com/vercel/next.js) · [React](https://github.com/facebook/react).

<div id="aither-constellation" data-self="awsuite"></div>
<script src="aither-constellation.js"></script>

<!-- aither-ecosystem:end -->
