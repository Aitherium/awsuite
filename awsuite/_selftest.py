"""`awsuite --self-test`: prove the offline machinery, every check SEEN to run.

No network. Exit 0 only when every check ran and passed; 1 when one failed;
2 when the self-test itself could not run (an import or setup error).

Kept out of `_doctor.py` because that file is generated and a self-test written
there is deleted by the next regeneration.
"""

from __future__ import annotations

import io
import json
import re
from typing import Callable, List


def _checks(check: Callable[[bool, str], None]) -> None:
    from . import auth, install, mcp, scopes, tools
    from .errors import ScopeError
    from .providers.google import GoogleProvider
    from .toolpack import register as pack_register

    # 1. PKCE -- RFC 7636 Appendix B vector, then a fresh pair.
    check(auth.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
          == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
          "PKCE S256 challenge matches the RFC 7636 Appendix B vector")
    v = auth.pkce_verifier()
    check(43 <= len(v) <= 128 and re.fullmatch(r"[A-Za-z0-9\-._~]+", v) is not None,
          f"PKCE verifier is {len(v)} chars of the unreserved alphabet")
    check(auth.pkce_verifier() != v, "PKCE verifiers are fresh per call")

    # 2. one table -> MCP tools/list, awdk toolpack, CLI
    names = [t.name for t in tools.TOOLS]
    check(len(names) == len(set(names)) and all(n.startswith("suite_") for n in names),
          f"{len(names)} tool names are unique and suite_*")
    mcp_list = tools.mcp_tools()
    check([t["name"] for t in mcp_list] == names, "MCP tools/list names == the table")

    class _Reg:
        def __init__(self) -> None:
            self.fns: dict = {}

        def register(self, fn, name=None, description=None, action_class=""):
            self.fns[name or fn.__name__] = (fn, description, action_class)

    reg = _Reg()
    n = pack_register(reg)
    check(n == len(names) and list(reg.fns) == names, "toolpack register() == the table")
    parity = [] if len(mcp_list) == len(tools.TOOLS) else ["(list lengths differ)"]
    for t, entry in zip(tools.TOOLS, mcp_list[:len(tools.TOOLS)], strict=not parity):
        fn, desc, action = reg.fns[t.name]
        derived = tools.schema_from_callable(fn)
        same = (derived == entry["inputSchema"] == t.input_schema
                and desc == entry["description"]
                and (fn.__doc__ or "").strip().split("\n")[0] == entry["description"]
                and (action == "write") == t.writes)
        if not same:
            parity.append(t.name)
    check(not parity, "awdk-derived schemas == MCP inputSchema for every tool"
          + (f" (DRIFT: {', '.join(parity)})" if parity else ""))
    yaml_text = (install.TOOLPACK_DIR / ".toolpack.yaml").read_text("utf-8")
    pats = re.findall(r'^\s*-\s*"([a-z_]+\*?)"\s*$', yaml_text, re.M)
    covered = all(any(n.startswith(p[:-1]) if p.endswith("*") else n == p for p in pats)
                  for n in names)
    check(bool(pats) and covered, f"toolpack mcp_tools {pats} cover every tool")
    yaml_skills = re.findall(r"^\s*-\s*(suite-[a-z-]+)\s*$", yaml_text, re.M)
    check(sorted(yaml_skills) == install.skill_names() and len(yaml_skills) >= 4,
          f"toolpack skills == shipped SKILL.md dirs ({len(yaml_skills)})")
    from .cli import build_parser
    help_text = build_parser().format_help()
    cli_groups = {t.cli[0] for t in tools.TOOLS if t.cli}
    check(all(g in help_text for g in cli_groups), f"CLI renders groups {sorted(cli_groups)}")

    # 3. write gating: no confirm -> dry run, and no provider is ever built.
    class _Explodes:
        def __getattr__(self, item):
            raise AssertionError(f"provider touched during a dry run: {item}")

    ctx = tools.Context(provider=_Explodes())
    writes = [t for t in tools.TOOLS if t.writes]
    sample = {"suite_mail_send": {"to": ["a@example.com"], "subject": "s", "body": "b"},
              "suite_mail_draft": {"to": ["a@example.com"], "subject": "s", "body": "b"},
              "suite_drive_upload": {"content": "hello", "name": "x.txt"},
              "suite_calendar_create": {"summary": "s", "start": "2026-01-01",
                                        "end": "2026-01-02"},
              "suite_docs_create": {"title": "t"},
              "suite_docs_append": {"document_id": "d", "text": "t"},
              "suite_sheets_append": {"spreadsheet_id": "s", "range": "A1", "rows": [["1"]]}}
    dry_ok = []
    for t in writes:
        for confirm in (None, False, "true"):
            args = dict(sample[t.name])
            if confirm is not None:
                args["confirm"] = confirm
            out = tools.call_tool(t.name, args, ctx)
            dry_ok.append(out.get("dry_run") is True)
    check(len(writes) == len(sample) and all(dry_ok),
          f"{len(writes)} write tools return a dry-run without confirm=true (x3 variants)")

    # 4. masking
    secret = "ya29." + "A" * 40 + "zz9"
    m = auth.mask(secret)
    check(secret not in m and "zz9" not in m and "fp=" in m, f"mask hides the token ({m})")
    md = auth.masked({"access_token": secret, "refresh_token": "1//" + "r" * 30, "mode": "o"})
    check(all(secret not in str(x) for x in md.values()) and md["mode"] == "o",
          "masked() hides every secret field and keeps the rest")

    # 5. scope pre-flight refuses offline, naming the scope
    class _Ro(auth.TokenSource):
        def token(self) -> str:
            raise AssertionError("token requested for a call that must not run")

        def granted_scopes(self):
            return {scopes.full("gmail.readonly")}

    try:
        GoogleProvider(_Ro()).mail_send(["a@example.com"], "s", "b")
        check(False, "a send without gmail.send is refused before the network")
    except ScopeError as exc:
        check("gmail.send" in exc.scope, f"a send without the scope raises ScopeError({exc.scope})")

    # 6. MCP over an in-memory pipe
    frames = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "selftest", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "suite_docs_create", "arguments": {"title": "x"}}},
    ]
    stdin = io.BytesIO(b"".join(json.dumps(f).encode() + b"\n" for f in frames))
    stdout = io.BytesIO()
    mcp.serve(stdin, stdout, mcp.Server(ctx_factory=lambda: ctx))
    replies = [json.loads(x) for x in stdout.getvalue().splitlines() if x.strip()]
    by_id = {r.get("id"): r for r in replies}
    check([r.get("id") for r in replies] == [1, 2, 3, 4],
          "MCP answers every request and no notification")
    init = by_id.get(1, {}).get("result", {})
    check(init.get("serverInfo", {}).get("name") == "awsuite"
          and "tools" in init.get("capabilities", {}), "MCP initialize advertises tools")
    listed = by_id.get(2, {}).get("result", {}).get("tools", [])
    check(listed == mcp_list, f"MCP tools/list over the pipe == the table ({len(listed)})")
    call = by_id.get(4, {}).get("result", {})
    body = json.loads(call.get("content", [{}])[0].get("text", "{}"))
    check(call.get("isError") is False and body.get("dry_run") is True,
          "MCP tools/call of a write without confirm returns the dry-run")


def run() -> int:
    fails: List[str] = []
    ran = [0]

    def check(ok: bool, what: str) -> None:
        ran[0] += 1
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            fails.append(what)

    print("awsuite self-test (offline)")
    try:
        _checks(check)
    except Exception as exc:  # noqa: BLE001 - "could not run" is its own answer
        print(f"  NOT VERIFIED: self-test could not run: {type(exc).__name__}: {exc}")
        return 2
    if ran[0] == 0:
        print("  NOT VERIFIED: no check ran")
        return 2
    print(f"\nself-test: {'PASS' if not fails else 'FAIL'} "
          f"({ran[0] - len(fails)}/{ran[0]} checks)")
    return 0 if not fails else 1
