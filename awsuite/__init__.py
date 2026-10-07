"""awsuite -- let an agent use a Google Workspace.

Gmail, Drive, Calendar, Docs, Sheets and the Admin Directory, exposed as one
tool table rendered onto every surface: a CLI, an MCP stdio server, an awdk
toolpack and agent skills. Stdlib only at runtime.

    from awsuite.tools import call_tool
    call_tool("suite_mail_search", {"query": "is:unread newer_than:1d"})

Every write tool (send, draft, create, append, upload) returns a dry-run
preview unless it is called with `confirm=True`.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
