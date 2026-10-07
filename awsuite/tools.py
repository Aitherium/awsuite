"""THE tool table. CLI, MCP server and awdk toolpack all render from `TOOLS`.

There is no second list anywhere: `mcp.py` turns each `Tool` into a
`tools/list` entry, `toolpack/__init__.py` turns each into a typed Python
callable an awdk ToolRegistry can introspect, and `cli.py` turns each into an
argparse subcommand. The self-test proves the three renderings agree.

Write safety: every tool with `writes=True` gets a `confirm` parameter, and
`call_tool` returns a DRY-RUN PREVIEW unless `confirm` is literally `true`. No
provider is even constructed for a dry run, so a preview can never send.
"""

from __future__ import annotations

import inspect
import json
import mimetypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import auth as _auth
from .errors import ConfigError, SuiteError

#: JSON-schema type name -> Python annotation used for the awdk rendering.
PY_TYPES: Dict[str, Any] = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "array": List[str],
    "rows": List[List[str]],
}

CONFIRM_DESC = ("Must be true to perform this write. Without it the tool returns a "
                "dry-run preview and changes nothing.")


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    description: str
    required: bool = False
    positional: bool = False  # CLI: a positional argument rather than --flag

    def schema(self) -> Dict[str, Any]:
        if self.type == "array":
            s: Dict[str, Any] = {"type": "array", "items": {"type": "string"}}
        elif self.type == "rows":
            s = {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}
        else:
            s = {"type": self.type}
        s["description"] = self.description
        return s


@dataclass
class Tool:
    name: str
    description: str
    params: List[Param]
    handler: Callable[["Context", Dict[str, Any]], Any]
    writes: bool = False
    preview: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    cli: Optional[Tuple[str, Optional[str]]] = None
    all_params: List[Param] = field(default_factory=list)

    def __post_init__(self) -> None:
        ps = list(self.params)
        if self.writes:
            ps.append(Param("confirm", "boolean", CONFIRM_DESC))
        self.all_params = ps

    @property
    def input_schema(self) -> Dict[str, Any]:
        schema: Dict[str, Any] = {"type": "object",
                                  "properties": {p.name: p.schema() for p in self.all_params}}
        req = [p.name for p in self.all_params if p.required]
        if req:
            schema["required"] = req
        return schema

    def mcp(self) -> Dict[str, Any]:
        """The MCP `tools/list` entry."""
        return {"name": self.name, "description": self.description,
                "inputSchema": self.input_schema,
                "annotations": {"readOnlyHint": not self.writes,
                                "destructiveHint": False,
                                "openWorldHint": True}}


class Context:
    """What a handler gets: the profile, and a provider built only on demand."""

    def __init__(self, profile: Optional[str] = None, provider: Any = None) -> None:
        self.profile = profile or _auth.default_profile()
        self._provider = provider

    @property
    def provider(self) -> Any:
        if self._provider is None:
            from .providers import get_provider
            self._provider = get_provider(self.profile)
        return self._provider


# --------------------------------------------------------------------------- helpers


def _upload_payload(args: Dict[str, Any]) -> Tuple[str, bytes, str]:
    path, content = args.get("path"), args.get("content")
    if bool(path) == bool(content):
        raise ConfigError("give exactly one of `path` (a local file) or `content` (text)")
    if path:
        p = Path(str(path)).expanduser()
        if not p.is_file():
            raise ConfigError(f"no such file: {p}")
        data = p.read_bytes()
        name = args.get("name") or p.name
    else:
        data = str(content).encode("utf-8")
        name = args.get("name") or "awsuite-upload.txt"
    mime = (args.get("mime_type") or _EXTRA_TYPES.get(Path(name).suffix.lower())
            or mimetypes.guess_type(name)[0] or "application/octet-stream")
    return name, data, mime


#: Types the host's mimetypes table may lack (it varies by OS and Python version).
_EXTRA_TYPES = {".md": "text/markdown", ".csv": "text/csv", ".txt": "text/plain",
                ".json": "application/json", ".yaml": "application/x-yaml"}


def _preview_upload(a: Dict[str, Any]) -> Dict[str, Any]:
    name, data, mime = _upload_payload(a)
    return {"action": "drive upload", "name": name, "bytes": len(data), "mime_type": mime,
            "parent": a.get("parent") or "(My Drive root)"}


def _do_upload(ctx: "Context", a: Dict[str, Any]) -> Any:
    name, data, mime = _upload_payload(a)
    return ctx.provider.drive_upload(name, data, mime, a.get("parent") or "")


def _clip(s: Any, n: int = 400) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + f"... [{len(s) - n} more chars]"


def _mail_preview(verb: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    def prev(a: Dict[str, Any]) -> Dict[str, Any]:
        return {"action": f"mail {verb}", "to": a.get("to"), "cc": a.get("cc") or [],
                "subject": a.get("subject"), "body": _clip(a.get("body"))}
    return prev


# --------------------------------------------------------------------------- the table

S, INT, B, A, R ="string", "integer", "boolean", "array", "rows"

TOOLS: List[Tool] = [
    Tool("suite_auth_status",
         "Show which workspace account and scopes awsuite is using (token values are masked).",
         [], lambda c, a: _auth.status(c.profile), cli=None),

    # ---- mail
    Tool("suite_mail_search",
         "Search Gmail with Gmail query syntax and return message summaries.",
         [Param("query", S, "Gmail search query, e.g. 'is:unread newer_than:2d'.",
                required=True, positional=True),
          Param("max_results", INT, "Maximum messages to return (default 10).")],
         lambda c, a: c.provider.mail_search(a["query"], int(a.get("max_results") or 10)),
         cli=("mail", "search")),
    Tool("suite_mail_read",
         "Read one Gmail message as plain text with its headers and attachment names.",
         [Param("message_id", S, "Gmail message id from suite_mail_search.",
                required=True, positional=True)],
         lambda c, a: c.provider.mail_read(a["message_id"]), cli=("mail", "read")),
    Tool("suite_mail_labels", "List the Gmail labels of the account.", [],
         lambda c, a: c.provider.mail_labels(), cli=("mail", "labels")),
    Tool("suite_mail_draft",
         "Create a Gmail draft (not sent). Requires confirm=true, else returns a preview.",
         [Param("to", A, "Recipient addresses.", required=True),
          Param("subject", S, "Subject line.", required=True),
          Param("body", S, "Plain-text body.", required=True),
          Param("cc", A, "Cc addresses.")],
         lambda c, a: c.provider.mail_draft(a["to"], a["subject"], a["body"], a.get("cc")),
         writes=True, preview=_mail_preview("draft"), cli=("mail", "draft")),
    Tool("suite_mail_send",
         "Send an email from the account. Requires confirm=true, else returns a preview.",
         [Param("to", A, "Recipient addresses.", required=True),
          Param("subject", S, "Subject line.", required=True),
          Param("body", S, "Plain-text body.", required=True),
          Param("cc", A, "Cc addresses.")],
         lambda c, a: c.provider.mail_send(a["to"], a["subject"], a["body"], a.get("cc")),
         writes=True, preview=_mail_preview("send"), cli=("mail", "send")),

    # ---- drive
    Tool("suite_drive_search",
         "Search Google Drive by full text and/or a raw Drive query; newest first.",
         [Param("text", S, "Full-text search terms.", positional=True),
          Param("q", S, "Raw Drive query, e.g. \"mimeType = 'application/pdf'\"."),
          Param("max_results", INT, "Maximum files to return (default 20).")],
         lambda c, a: c.provider.drive_search(a.get("text") or "", a.get("q") or "",
                                              int(a.get("max_results") or 20)),
         cli=("drive", "search")),
    Tool("suite_drive_read",
         "Read a Drive file as text; Docs and Slides export to text, Sheets to CSV.",
         [Param("file_id", S, "Drive file id.", required=True, positional=True),
          Param("max_chars", INT, "Truncate the text to this many characters (default 50000).")],
         lambda c, a: c.provider.drive_read(a["file_id"], int(a.get("max_chars") or 50000)),
         cli=("drive", "read")),
    Tool("suite_drive_upload",
         "Upload a local file or text content to Drive. Requires confirm=true, else previews.",
         [Param("path", S, "Local file to upload (or use content).", positional=True),
          Param("content", S, "Text content to upload instead of a file."),
          Param("name", S, "Name in Drive (default: the file name)."),
          Param("parent", S, "Parent folder id (default: My Drive root)."),
          Param("mime_type", S, "Content type (default: guessed from the name).")],
         lambda c, a: _do_upload(c, a),
         writes=True, preview=_preview_upload, cli=("drive", "upload")),

    # ---- calendar
    Tool("suite_calendar_events",
         "List calendar events in a time window (default: the next 7 days).",
         [Param("time_min", S, "Window start, RFC3339 (default now)."),
          Param("time_max", S, "Window end, RFC3339 (default now + 7 days)."),
          Param("calendar", S, "Calendar id (default primary)."),
          Param("query", S, "Free-text filter on events."),
          Param("max_results", INT, "Maximum events (default 25).")],
         lambda c, a: c.provider.calendar_events(a.get("time_min") or "",
                                                 a.get("time_max") or "",
                                                 a.get("calendar") or "primary",
                                                 a.get("query") or "",
                                                 int(a.get("max_results") or 25)),
         cli=("cal", "events")),
    Tool("suite_calendar_freebusy",
         "Busy intervals for one or more calendars in a time window.",
         [Param("time_min", S, "Window start, RFC3339.", required=True),
          Param("time_max", S, "Window end, RFC3339.", required=True),
          Param("calendars", A, "Calendar ids or addresses (default primary).")],
         lambda c, a: c.provider.calendar_freebusy(a["time_min"], a["time_max"],
                                                   a.get("calendars") or ["primary"]),
         cli=("cal", "freebusy")),
    Tool("suite_calendar_create",
         "Create a calendar event. Requires confirm=true, else returns a preview.",
         [Param("summary", S, "Event title.", required=True),
          Param("start", S, "Start: RFC3339 date-time, or YYYY-MM-DD for all-day.",
                required=True),
          Param("end", S, "End: RFC3339 date-time, or YYYY-MM-DD for all-day.", required=True),
          Param("description", S, "Event description."),
          Param("location", S, "Event location."),
          Param("attendees", A, "Attendee addresses (they are emailed an invite)."),
          Param("calendar", S, "Calendar id (default primary).")],
         lambda c, a: c.provider.calendar_create(a["summary"], a["start"], a["end"],
                                                 a.get("calendar") or "primary",
                                                 a.get("description") or "",
                                                 a.get("location") or "",
                                                 a.get("attendees")),
         writes=True,
         preview=lambda a: {"action": "calendar create", "summary": a.get("summary"),
                            "start": a.get("start"), "end": a.get("end"),
                            "attendees": a.get("attendees") or [],
                            "calendar": a.get("calendar") or "primary"},
         cli=("cal", "create")),

    # ---- docs
    Tool("suite_docs_read", "Read a Google Doc as plain text.",
         [Param("document_id", S, "Google Docs document id.", required=True, positional=True)],
         lambda c, a: c.provider.docs_read(a["document_id"]), cli=("docs", "read")),
    Tool("suite_docs_create",
         "Create an empty Google Doc. Requires confirm=true, else returns a preview.",
         [Param("title", S, "Document title.", required=True, positional=True)],
         lambda c, a: c.provider.docs_create(a["title"]), writes=True,
         preview=lambda a: {"action": "docs create", "title": a.get("title")},
         cli=("docs", "create")),
    Tool("suite_docs_append",
         "Append text to the end of a Google Doc. Requires confirm=true, else previews.",
         [Param("document_id", S, "Google Docs document id.", required=True, positional=True),
          Param("text", S, "Text to append.", required=True)],
         lambda c, a: c.provider.docs_append(a["document_id"], a["text"]), writes=True,
         preview=lambda a: {"action": "docs append", "document_id": a.get("document_id"),
                            "text": _clip(a.get("text"))},
         cli=("docs", "append")),

    # ---- sheets
    Tool("suite_sheets_read", "Read a cell range from a Google Sheet.",
         [Param("spreadsheet_id", S, "Spreadsheet id.", required=True, positional=True),
          Param("range", S, "A1 range, e.g. 'Sheet1!A1:D20'.", required=True, positional=True)],
         lambda c, a: c.provider.sheets_read(a["spreadsheet_id"], a["range"]),
         cli=("sheets", "read")),
    Tool("suite_sheets_append",
         "Append rows to a Google Sheet. Requires confirm=true, else returns a preview.",
         [Param("spreadsheet_id", S, "Spreadsheet id.", required=True, positional=True),
          Param("range", S, "A1 range whose table to append to, e.g. 'Sheet1!A1'.",
                required=True, positional=True),
          Param("rows", R, "Rows to append; each row is a list of cell values.",
                required=True)],
         lambda c, a: c.provider.sheets_append(a["spreadsheet_id"], a["range"], a["rows"]),
         writes=True,
         preview=lambda a: {"action": "sheets append", "spreadsheet_id": a.get("spreadsheet_id"),
                            "range": a.get("range"), "rows": (a.get("rows") or [])[:20],
                            "row_count": len(a.get("rows") or [])},
         cli=("sheets", "append")),

    # ---- directory
    Tool("suite_directory_users",
         "List users in the Workspace directory (admin account required; read-only).",
         [Param("query", S, "Directory query, e.g. 'orgUnitPath=/Sales'."),
          Param("max_results", INT, "Maximum users (default 50)."),
          Param("domain", S, "Limit to one domain (default: the whole customer).")],
         lambda c, a: c.provider.directory_users(a.get("query") or "",
                                                 int(a.get("max_results") or 50),
                                                 a.get("domain") or ""),
         cli=("users", None)),
]

BY_NAME: Dict[str, Tool] = {t.name: t for t in TOOLS}


# --------------------------------------------------------------------------- dispatch


def _check_args(tool: Tool, args: Dict[str, Any]) -> Dict[str, Any]:
    known = {p.name: p for p in tool.all_params}
    clean = {k: v for k, v in (args or {}).items() if v is not None}
    unknown = sorted(set(clean) - set(known))
    if unknown:
        raise ConfigError(f"{tool.name}: unknown argument(s) {', '.join(unknown)}; "
                          f"accepted: {', '.join(known) or '(none)'}")
    missing = [p.name for p in tool.all_params if p.required and clean.get(p.name) in (None, "")]
    if missing:
        raise ConfigError(f"{tool.name}: missing required argument(s) {', '.join(missing)}")
    for name, v in list(clean.items()):
        t = known[name].type
        if t == "array" and isinstance(v, str):
            clean[name] = [x.strip() for x in v.split(",") if x.strip()]
        elif t == "integer" and isinstance(v, str) and v.strip().lstrip("-").isdigit():
            clean[name] = int(v)
    return clean


def call_tool(name: str, args: Optional[Dict[str, Any]] = None,
              ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Run one tool. Returns `{"ok": True, "result": ...}`, a dry-run, or raises SuiteError.

    A write tool without `confirm: true` returns `{"ok": True, "dry_run": True,
    "would": <preview>}` and never builds a provider.
    """
    tool = BY_NAME.get(name)
    if tool is None:
        raise ConfigError(f"unknown tool {name!r}")
    a = _check_args(tool, args or {})
    if tool.writes and a.get("confirm") is not True:
        would = tool.preview(a) if tool.preview else {k: v for k, v in a.items()
                                                       if k != "confirm"}
        return {"ok": True, "dry_run": True, "tool": name, "would": would,
                "note": "Nothing was changed. Re-run with confirm=true to perform this write."}
    ctx = ctx or Context()
    a.pop("confirm", None)
    return {"ok": True, "tool": name, "result": tool.handler(ctx, a)}


def call_tool_safe(name: str, args: Optional[Dict[str, Any]] = None,
                   ctx: Optional[Context] = None) -> Tuple[Dict[str, Any], bool]:
    """`call_tool` that turns a SuiteError into `({"ok": False, ...}, True)`."""
    try:
        return call_tool(name, args, ctx), False
    except SuiteError as exc:
        return {"ok": False, "tool": name, **exc.to_dict()}, True


# --------------------------------------------------------------------------- renderings


def mcp_tools() -> List[Dict[str, Any]]:
    """The MCP `tools/list` payload."""
    return [t.mcp() for t in TOOLS]


def adk_function(tool: Tool, ctx_factory: Optional[Callable[[], Context]] = None) -> Callable:
    """A callable whose signature/docstring an awdk ToolRegistry introspects into
    exactly `tool.input_schema` (keyword-only params, real type annotations,
    `Args:` lines carrying each property description)."""

    def fn(**kwargs: Any) -> str:
        ctx = ctx_factory() if ctx_factory else Context()
        out, _err = call_tool_safe(tool.name, kwargs, ctx)
        return json.dumps(out, default=str)

    params, ann = [], {}
    for p in tool.all_params:
        default = inspect.Parameter.empty if p.required else (False if p.type == "boolean"
                                                               else None)
        params.append(inspect.Parameter(p.name, inspect.Parameter.KEYWORD_ONLY,
                                        default=default, annotation=PY_TYPES[p.type]))
        ann[p.name] = PY_TYPES[p.type]
    ann["return"] = str
    fn.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    fn.__annotations__ = ann
    fn.__name__ = fn.__qualname__ = tool.name
    lines = [tool.description]
    if tool.all_params:
        lines += ["", "Args:"] + [f"    {p.name}: {p.description}" for p in tool.all_params]
    fn.__doc__ = "\n".join(lines)
    return fn


def schema_from_callable(fn: Callable) -> Dict[str, Any]:
    """Re-derive the JSON schema the way awdk's ToolRegistry does (signature +
    type hints + `name:` docstring lines). Used to PROVE parity, not to render."""
    import typing

    try:
        hints = typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001 - mirror awdk's tolerance
        hints = getattr(fn, "__annotations__", {})

    def to_schema(h: Any) -> Dict[str, Any]:
        base = {str: "string", int: "integer", float: "number", bool: "boolean"}
        if h in base:
            return {"type": base[h]}
        if getattr(h, "__origin__", None) is list:
            args = getattr(h, "__args__", (str,))
            return {"type": "array", "items": to_schema(args[0] if args else str)}
        return {"type": "string"}

    props: Dict[str, Any] = {}
    required: List[str] = []
    for name, prm in inspect.signature(fn).parameters.items():
        if prm.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        prop = to_schema(hints.get(name, str))
        for line in (fn.__doc__ or "").split("\n"):
            st = line.strip()
            if st.startswith(f"{name}:") or st.startswith(f"{name} "):
                desc = st.split(":", 1)[-1].strip() if ":" in st else ""
                if desc:
                    prop["description"] = desc
                break
        props[name] = prop
        if prm.default is inspect.Parameter.empty:
            required.append(name)
    out: Dict[str, Any] = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


def register_on(registry: Any, ctx_factory: Optional[Callable[[], Context]] = None) -> int:
    """Register every tool on an awdk-style registry (`registry.register(fn, ...)`)."""
    n = 0
    for tool in TOOLS:
        fn = adk_function(tool, ctx_factory)
        try:
            registry.register(fn, name=tool.name, description=tool.description,
                              action_class="write" if tool.writes else "")
        except TypeError:
            registry.register(fn)
        n += 1
    return n


def profile_from_env() -> Optional[str]:
    return os.environ.get("AWSUITE_PROFILE") or None
