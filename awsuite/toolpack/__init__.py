"""awsuite toolpack -- register every suite_* tool on an awdk agent.

The awdk loader calls `register(registry) -> int`. Tools are rendered from
awsuite's single tool table (`awsuite.tools.TOOLS`), so this pack, the MCP
server and the CLI cannot disagree about names, schemas or write gating.

When this file is copied to `~/.aitheros/packs/awsuite/` by
`awsuite pack install`, it is file-loaded by path and imports the installed
`awsuite` package; if that is missing it registers nothing and says why.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("awsuite_pack")


def register(registry) -> int:
    """Register every suite_* tool. Returns the number registered."""
    try:
        from awsuite.tools import register_on
    except Exception as exc:  # noqa: BLE001 - a missing package = 0 tools, not a crash
        logger.warning("awsuite pack unavailable (%s): pip install awsuite", exc)
        return 0
    n = register_on(registry)
    logger.info("awsuite pack registered %d suite_* tools", n)
    return n
