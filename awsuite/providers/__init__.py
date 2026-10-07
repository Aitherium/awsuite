"""Workspace providers behind one small protocol.

Only Google is implemented. The seam is `PROVIDERS`: a second suite (for
example Microsoft 365 over Graph) is a module implementing `Provider` and one
line here -- the tool table, CLI, MCP server and toolpack do not change.
"""

from __future__ import annotations

import importlib
from typing import Dict, Optional

from ..errors import ConfigError
from .base import Provider

#: provider name -> "module:Class". Add "microsoft": "...m365:M365Provider" here.
PROVIDERS: Dict[str, str] = {
    "google": "awsuite.providers.google:GoogleProvider",
}


def get_provider(profile: Optional[str] = None, name: Optional[str] = None) -> Provider:
    """Build the provider for a profile (its stored `provider`, default google)."""
    from .. import auth

    prof = profile or auth.default_profile()
    if name is None:
        data = auth.load_profile(prof) or {}
        name = str(data.get("provider") or "google")
    target = PROVIDERS.get(name)
    if target is None:
        raise ConfigError(f"unknown provider {name!r}; available: {', '.join(PROVIDERS)}")
    mod_name, cls_name = target.split(":")
    cls = getattr(importlib.import_module(mod_name), cls_name)
    return cls(auth.load_token_source(prof))


__all__ = ["PROVIDERS", "Provider", "get_provider"]
