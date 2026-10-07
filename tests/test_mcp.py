from __future__ import annotations

import io
import json

from test_tools import Recorder

from awsuite import mcp, tools
from awsuite.errors import NotFound


def _run(frames, provider=None):
    raw = b"".join((f if isinstance(f, bytes) else json.dumps(f).encode()) + b"\n"
                   for f in frames)
    out = io.BytesIO()
    srv = mcp.Server(ctx_factory=lambda: tools.Context(provider=provider or Recorder()))
    assert mcp.serve(io.BytesIO(raw), out, srv) == 0
    return [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]


def test_round_trip_initialize_list_call():
    replies = _run([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "suite_mail_read", "arguments": {"message_id": "m1"}}},
        {"jsonrpc": "2.0", "id": "p", "method": "ping"},
    ])
    assert [r["id"] for r in replies] == [1, 2, 3, "p"]
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    assert replies[0]["result"]["serverInfo"]["name"] == "awsuite"
    assert replies[1]["result"]["tools"] == tools.mcp_tools()
    call = replies[2]["result"]
    assert call["isError"] is False
    assert json.loads(call["content"][0]["text"])["result"] == {"called": "mail_read"}
    assert replies[3]["result"] == {}


def test_unknown_protocol_version_gets_ours():
    r = _run([{"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "1999-01-01"}}])
    assert r[0]["result"]["protocolVersion"] == mcp.PROTOCOL_VERSIONS[0]


def test_write_tool_over_mcp_is_dry_run_without_confirm():
    rec = Recorder()
    r = _run([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "suite_mail_send",
                          "arguments": {"to": ["a@x"], "subject": "s", "body": "b"}}}], rec)
    body = json.loads(r[0]["result"]["content"][0]["text"])
    assert body["dry_run"] is True and rec.calls == []


def test_tool_error_is_is_error_not_protocol_error():
    r = _run([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "suite_docs_read", "arguments": {"document_id": "x"}}}],
             Recorder(raises=NotFound("no such doc", 404)))
    res = r[0]["result"]
    assert res["isError"] is True and "no such doc" in res["content"][0]["text"]


def test_protocol_errors():
    r = _run([
        b"{not json",
        {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope"}},
        {"jsonrpc": "2.0", "method": "some/notification"},
        {"jsonrpc": "1.0", "id": 3, "method": "ping"},
    ])
    assert r[0]["error"]["code"] == -32700 and r[0]["id"] is None
    assert r[1]["error"]["code"] == -32601
    assert r[2]["error"]["code"] == -32602
    assert r[3]["error"]["code"] == -32600 and r[3]["id"] == 3
    assert len(r) == 4  # the notification got no reply


def test_batch_request():
    raw = json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                      {"jsonrpc": "2.0", "method": "notifications/initialized"}]).encode()
    out = io.BytesIO()
    mcp.serve(io.BytesIO(raw + b"\n"), out)
    assert json.loads(out.getvalue()) == [{"jsonrpc": "2.0", "id": 1, "result": {}}]
