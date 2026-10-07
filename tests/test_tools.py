from __future__ import annotations

import json

import pytest

from awsuite import tools
from awsuite.errors import ConfigError, NotFound


class Recorder:
    """A provider double: records every method call, returns a canned value."""

    def __init__(self, raises=None):
        self.calls = []
        self.raises = raises

    def __getattr__(self, name):
        def method(*a, **kw):
            self.calls.append((name, a, kw))
            if self.raises:
                raise self.raises
            return {"called": name}
        return method


WRITE_ARGS = {
    "suite_mail_send": {"to": ["a@x"], "subject": "s", "body": "b"},
    "suite_mail_draft": {"to": ["a@x"], "subject": "s", "body": "b"},
    "suite_drive_upload": {"content": "hello", "name": "x.txt"},
    "suite_calendar_create": {"summary": "s", "start": "2026-01-01", "end": "2026-01-02"},
    "suite_docs_create": {"title": "t"},
    "suite_docs_append": {"document_id": "d", "text": "t"},
    "suite_sheets_append": {"spreadsheet_id": "s", "range": "A1", "rows": [["1"]]},
}


def test_write_tools_are_exactly_the_mutating_ones():
    assert sorted(t.name for t in tools.TOOLS if t.writes) == sorted(WRITE_ARGS)
    for t in tools.TOOLS:
        assert ("confirm" in t.input_schema["properties"]) == t.writes


@pytest.mark.parametrize("name", sorted(WRITE_ARGS))
def test_write_without_confirm_is_dry_run(name):
    rec = Recorder()
    out = tools.call_tool(name, WRITE_ARGS[name], tools.Context(provider=rec))
    assert out["dry_run"] is True and out["would"] and rec.calls == []
    out = tools.call_tool(name, {**WRITE_ARGS[name], "confirm": "yes"},
                          tools.Context(provider=rec))
    assert out["dry_run"] is True and rec.calls == []  # only literal true confirms


@pytest.mark.parametrize("name", sorted(WRITE_ARGS))
def test_write_with_confirm_calls_provider(name):
    rec = Recorder()
    out = tools.call_tool(name, {**WRITE_ARGS[name], "confirm": True},
                          tools.Context(provider=rec))
    assert "dry_run" not in out and len(rec.calls) == 1
    assert "confirm" not in rec.calls[0][2]


def test_mail_send_preview_shows_message():
    out = tools.call_tool("suite_mail_send", {"to": "a@x, b@x", "subject": "Hi", "body": "B"},
                          tools.Context(provider=Recorder()))
    assert out["would"] == {"action": "mail send", "to": ["a@x", "b@x"], "cc": [],
                            "subject": "Hi", "body": "B"}


def test_read_tool_dispatch():
    rec = Recorder()
    tools.call_tool("suite_mail_search", {"query": "x", "max_results": "3"},
                    tools.Context(provider=rec))
    assert rec.calls == [("mail_search", ("x", 3), {})]


def test_unknown_and_missing_arguments():
    ctx = tools.Context(provider=Recorder())
    with pytest.raises(ConfigError, match="unknown argument"):
        tools.call_tool("suite_mail_read", {"message_id": "m", "bogus": 1}, ctx)
    with pytest.raises(ConfigError, match="missing required"):
        tools.call_tool("suite_mail_read", {}, ctx)
    with pytest.raises(ConfigError, match="unknown tool"):
        tools.call_tool("suite_nope", {}, ctx)


def test_call_tool_safe_maps_errors():
    out, err = tools.call_tool_safe("suite_docs_read", {"document_id": "d"},
                                    tools.Context(provider=Recorder(raises=NotFound("gone", 404))))
    assert err and out == {"ok": False, "tool": "suite_docs_read", "error": "NotFound",
                           "message": "gone", "status": 404}


def test_auth_status_needs_no_provider():
    out = tools.call_tool("suite_auth_status", {}, tools.Context())
    assert out["result"]["configured"] is False


def test_upload_from_path(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# hi")
    rec = Recorder()
    tools.call_tool("suite_drive_upload", {"path": str(f), "confirm": True},
                    tools.Context(provider=rec))
    assert rec.calls == [("drive_upload", ("notes.md", b"# hi", "text/markdown", ""), {})]
    with pytest.raises(ConfigError, match="exactly one"):
        tools.call_tool("suite_drive_upload", {"path": str(f), "content": "x"},
                        tools.Context(provider=rec))


def test_adk_function_schema_parity_and_call():
    for t in tools.TOOLS:
        fn = tools.adk_function(t, lambda: tools.Context(provider=Recorder()))
        assert fn.__name__ == t.name
        assert tools.schema_from_callable(fn) == t.input_schema == t.mcp()["inputSchema"]
    fn = tools.adk_function(tools.BY_NAME["suite_docs_read"],
                            lambda: tools.Context(provider=Recorder()))
    assert json.loads(fn(document_id="d"))["result"] == {"called": "docs_read"}
