"""awsuite command line.

    awsuite auth login [--scopes mail,drive] [--write] [--client-secret client_secret.json]
    awsuite mail search "is:unread newer_than:2d"
    awsuite mail send --to a@b.com --subject Hi --body "..." --confirm
    awsuite drive push --query "q3 report" --to https://workspace.example.com --bearer-env TOK
    awsuite tools | mcp | pack install | skills install | doctor | --self-test

Every tool subcommand (mail, drive, cal, docs, sheets, users) is GENERATED from
the tool table in `awsuite.tools` -- the same table the MCP server and the awdk
toolpack render. `--json` anywhere switches every command to machine output.

Exit codes: 0 done, 1 the provider refused or failed, 2 could not run
(not logged in, bad arguments, missing configuration).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from . import __version__
from . import auth as _auth
from . import scopes as _scopes
from .errors import AuthError, ConfigError, SuiteError
from .tools import TOOLS, Context, Tool, call_tool

GROUP_HELP = {
    "mail": "Gmail: search, read, labels, draft, send",
    "drive": "Drive: search, read, upload, push (to a workspace)",
    "cal": "Calendar: events, freebusy, create",
    "docs": "Docs: read, create, append",
    "sheets": "Sheets: read, append",
}


# --------------------------------------------------------------------------- output


def _emit(obj: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))
        return
    if isinstance(obj, dict) and obj.get("dry_run"):
        print("DRY RUN - nothing was changed. Add --confirm to perform it.")
        print(json.dumps(obj.get("would", obj), indent=2, default=str, ensure_ascii=False))
        return
    res = obj.get("result", obj) if isinstance(obj, dict) and "ok" in obj else obj
    _human(res)


def _human(res: Any) -> None:
    if isinstance(res, list):
        if not res:
            print("(none)")
        for item in res:
            if isinstance(item, dict):
                vals = [str(v) for v in item.values() if isinstance(v, (str, int)) and v != ""]
                print("  ".join(vals[:5]))
            else:
                print(item)
        return
    if isinstance(res, dict):
        long_key = next((k for k in ("body", "text") if isinstance(res.get(k), str)), None)
        for k, v in res.items():
            if k == long_key or isinstance(v, (dict, list)) and not v:
                continue
            print(f"{k}: {v if not isinstance(v, (dict, list)) else json.dumps(v, default=str)}")
        if long_key:
            print()
            print(res[long_key])
        return
    print(res)


def _fail(exc: SuiteError, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": False, **exc.to_dict()}, indent=2))
    else:
        print(f"awsuite: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 2 if isinstance(exc, (AuthError, ConfigError)) else 1


# --------------------------------------------------------------------------- generated


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def _add_tool_args(sp: argparse.ArgumentParser, tool: Tool) -> None:
    for p in tool.all_params:
        help_ = p.description
        if p.type == "boolean":
            sp.add_argument(_flag(p.name), dest=p.name, action="store_true", help=help_)
        elif p.type == "rows":
            sp.add_argument("--row", dest="rows", action="append", metavar="A,B,C",
                            help="A row as comma-separated cells (repeatable).")
            sp.add_argument("--rows-json", dest="rows_json", metavar="JSON",
                            help="All rows as a JSON list of lists.")
        elif p.type == "array":
            sp.add_argument(_flag(p.name), dest=p.name, action="append",
                            metavar="VALUE", help=help_ + " (repeatable or comma-separated)")
        elif p.positional:
            sp.add_argument(p.name, nargs=None if p.required else "?",
                            type=int if p.type == "integer" else str, help=help_)
        else:
            sp.add_argument(_flag(p.name), dest=p.name,
                            type=int if p.type == "integer" else str, help=help_)
    sp.set_defaults(_tool=tool)


def _tool_args(tool: Tool, ns: argparse.Namespace) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for p in tool.all_params:
        if p.type == "rows":
            if getattr(ns, "rows_json", None):
                try:
                    out["rows"] = json.loads(ns.rows_json)
                except ValueError as exc:
                    raise ConfigError(f"--rows-json is not valid JSON: {exc}") from exc
            elif getattr(ns, "rows", None):
                out["rows"] = [[c.strip() for c in r.split(",")] for r in ns.rows]
            continue
        v = getattr(ns, p.name, None)
        if v is None or v is False:
            continue
        if p.type == "array":
            v = [x.strip() for item in v for x in item.split(",") if x.strip()]
        out[p.name] = v
    return out


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="awsuite",
        description="Google Workspace for agents: CLI, MCP server, awdk toolpack, skills.")
    ap.add_argument("--version", action="version", version=f"awsuite {__version__}")
    ap.add_argument("--profile", default=None,
                    help="Profile name (default: $AWSUITE_PROFILE or 'default').")
    ap.add_argument("--self-test", action="store_true",
                    help="Prove the offline machinery works (exit 0/1/2).")
    sub = ap.add_subparsers(dest="cmd")

    # auth -------------------------------------------------------------------
    a = sub.add_parser("auth", help="log in, show status, log out, list scopes")
    asub = a.add_subparsers(dest="auth_cmd")
    lg = asub.add_parser("login", help="create or extend a profile")
    lg.add_argument("--mode", choices=_auth.MODES, default="oauth")
    lg.add_argument("--client-secret", help="Google OAuth client_secret.json (installed app).")
    lg.add_argument("--scopes", default="all",
                    help=f"Services: {','.join(_scopes.SERVICES)} (default all).")
    lg.add_argument("--write", action="store_true",
                    help="Also request write scopes (send/compose, drive.file, events, ...).")
    lg.add_argument("--no-browser", action="store_true", help="Print the URL only.")
    lg.add_argument("--key", help="service-account: JSON key file.")
    lg.add_argument("--subject", help="service-account: user to impersonate (user@domain).")
    lg.add_argument("--token-cmd", help="token mode: command that prints an access token.")
    lg.add_argument("--timeout", type=float, default=300.0,
                    help="Seconds to wait for the browser redirect.")
    asub.add_parser("status", help="show the profile (tokens masked)")
    lo = asub.add_parser("logout", help="revoke and delete the profile")
    lo.add_argument("--no-revoke", action="store_true", help="Delete locally only.")
    asub.add_parser("scopes", help="service -> scope map, and what the profile holds")

    # generated tool groups ----------------------------------------------------
    groups: Dict[str, argparse._SubParsersAction] = {}
    for tool in TOOLS:
        if not tool.cli:
            continue
        group, verb = tool.cli
        if verb is None:
            _add_tool_args(sub.add_parser(group, help=tool.description), tool)
            continue
        if group not in groups:
            g = sub.add_parser(group, help=GROUP_HELP.get(group, group))
            groups[group] = g.add_subparsers(dest=f"{group}_cmd")
        _add_tool_args(groups[group].add_parser(verb, help=tool.description), tool)

    # drive push (CLI-only: it carries a session credential) -------------------
    push = groups["drive"].add_parser(
        "push", help="upload Drive files into an Aither workspace (dry run without --confirm)")
    push.add_argument("--id", dest="ids", action="append", help="Drive file id (repeatable).")
    push.add_argument("--query", default="", help="Select files by Drive full-text search.")
    push.add_argument("--limit", type=int, default=10, help="Max files for --query.")
    push.add_argument("--to", required=True, metavar="BASE_URL", dest="to",
                      help="Workspace base URL, e.g. https://workspace.example.com")
    push.add_argument("--bearer-env", default="", metavar="NAME",
                      help="Env var holding your workspace session bearer.")
    push.add_argument("--cookie-env", default="", metavar="NAME",
                      help="Env var holding a session Cookie header instead.")
    push.add_argument("--doc-type", default="", help="Workspace doc_type (default: its own).")
    push.add_argument("--confirm", action="store_true", help="Actually upload.")
    push.set_defaults(_push=True)

    # plumbing ---------------------------------------------------------------
    t = sub.add_parser("tools", help="list the tool table")
    t.add_argument("--schema", action="store_true", help="Include input schemas.")
    m = sub.add_parser("mcp", help="run the MCP stdio server")
    m.set_defaults(_mcp=True)
    pk = sub.add_parser("pack", help="awdk toolpack")
    pks = pk.add_subparsers(dest="pack_cmd")
    pi = pks.add_parser("install", help="copy the toolpack + skills to <dir>/awsuite/")
    pi.add_argument("--dir", default=None, help="Default ~/.aitheros/packs")
    sk = sub.add_parser("skills", help="agent skills")
    sks = sk.add_subparsers(dest="skills_cmd")
    si = sks.add_parser("install", help="copy skills to <dir>/<name>/SKILL.md")
    si.add_argument("--dir", default=None, help="Default ~/.claude/skills")
    sub.add_parser("doctor", help="python, token perms, scopes, reachability")
    return ap


# --------------------------------------------------------------------------- commands


def _auth_cmd(ns: argparse.Namespace, as_json: bool) -> int:
    prof = ns.profile or _auth.default_profile()
    if ns.auth_cmd == "login":
        svcs = _scopes.parse_services(ns.scopes)
        st = _auth.login(prof, ns.mode, services=svcs, write=ns.write,
                         client_secret=ns.client_secret, open_browser=not ns.no_browser,
                         key_path=ns.key or "", subject=ns.subject or "",
                         token_cmd=ns.token_cmd or "", timeout=ns.timeout,
                         printer=lambda s: print(s, file=sys.stderr))
        _emit(st, as_json)
        return 0
    if ns.auth_cmd == "status" or ns.auth_cmd is None:
        st = _auth.status(prof)
        _emit(st, as_json)
        return 0 if st.get("configured") else 2
    if ns.auth_cmd == "logout":
        _emit(_auth.logout(prof, revoke=not ns.no_revoke), as_json)
        return 0
    if ns.auth_cmd == "scopes":
        table = {s: {"read": [_scopes.full(x) for x in _scopes.READ[s]],
                     "write": [_scopes.full(x) for x in _scopes.WRITE[s]]}
                 for s in _scopes.SERVICES}
        st = _auth.status(prof)
        out = {"services": table, "profile": {k: st.get(k) for k in (
            "profile", "configured", "scopes_requested", "scopes_granted", "missing_scopes")}}
        if as_json:
            _emit(out, True)
        else:
            for s, v in table.items():
                print(f"{s:10} read: {', '.join(_scopes.short(x) for x in v['read'])}")
                if v["write"]:
                    print(f"{'':10} write: {', '.join(_scopes.short(x) for x in v['write'])}")
            print()
            _human(out["profile"])
        return 0
    return 2


def _push_cmd(ns: argparse.Namespace, as_json: bool) -> int:
    from . import push as _push

    base = _push.check_base(ns.to)
    headers = _push.credential_headers(ns.bearer_env, ns.cookie_env)
    provider = Context(ns.profile).provider
    ids = _push.resolve_ids(provider, ns.ids, ns.query, ns.limit)
    out = _push.push(provider, ids, base, headers, doc_type=ns.doc_type, confirm=ns.confirm)
    if as_json:
        _emit(out, True)
    else:
        if out.get("dry_run"):
            print("DRY RUN - nothing was uploaded. Add --confirm to push.")
        print(f"target: {out['target']}")
        for f in out["files"]:
            print(f"  {f['action']:12} {f.get('name') or f['id']}"
                  + (f"  ({f['reason']})" if f.get("reason") else ""))
    failed = [f for f in out["files"] if f["action"] == "skipped"]
    return 1 if failed and len(failed) == len(out["files"]) and out["files"] else 0


def _tools_cmd(ns: argparse.Namespace, as_json: bool) -> int:
    rows = [{"name": t.name, "writes": t.writes, "description": t.description,
             "cli": " ".join(x for x in (t.cli or ()) if x) or None,
             **({"input_schema": t.input_schema} if ns.schema else {})} for t in TOOLS]
    if as_json:
        _emit(rows, True)
    else:
        for r in rows:
            mark = "W" if r["writes"] else " "
            print(f"{mark} {r['name']:26} {r['description']}")
        print("\nW = write tool: dry-run preview unless confirm=true (CLI: --confirm)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            _ = None
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    # --profile is global: accept it anywhere, not only before the subcommand.
    profile: Optional[str] = None
    rest: List[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--profile" and i + 1 < len(args):
            profile, i = args[i + 1], i + 2
            continue
        if args[i].startswith("--profile="):
            profile = args[i].split("=", 1)[1]
        else:
            rest.append(args[i])
        i += 1
    ap = build_parser()
    ns = ap.parse_args(rest)
    ns.profile = profile

    if ns.self_test:
        from . import _selftest
        return int(_selftest.run())
    try:
        if ns.cmd is None:
            ap.print_help()
            return 2
        if ns.cmd == "auth":
            return _auth_cmd(ns, as_json)
        if getattr(ns, "_push", False):
            return _push_cmd(ns, as_json)
        if getattr(ns, "_tool", None) is not None:
            tool: Tool = ns._tool
            out = call_tool(tool.name, _tool_args(tool, ns), Context(ns.profile))
            _emit(out, as_json)
            return 0
        if ns.cmd == "tools":
            return _tools_cmd(ns, as_json)
        if ns.cmd == "mcp":
            from . import mcp
            return mcp.main(ns.profile)
        if ns.cmd == "pack":
            if ns.pack_cmd != "install":
                ap.parse_args(["pack", "--help"])
            from .install import install_pack
            _emit(install_pack(ns.dir), as_json)
            return 0
        if ns.cmd == "skills":
            if ns.skills_cmd != "install":
                ap.parse_args(["skills", "--help"])
            from .install import install_skills
            _emit(install_skills(ns.dir), as_json)
            return 0
        if ns.cmd == "doctor":
            from . import _doctor
            if as_json:
                from . import doctor_local
                checks = doctor_local.collect()
                _emit(checks, True)
                return doctor_local.exit_code(checks)
            return int(_doctor.report())
        # A group with no verb (e.g. `awsuite mail`): show its help.
        ap.parse_args([ns.cmd, "--help"])
        return 2
    except SuiteError as exc:
        return _fail(exc, as_json)
    except ValueError as exc:
        return _fail(ConfigError(str(exc)), as_json)


if __name__ == "__main__":
    sys.exit(main())
