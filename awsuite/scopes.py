"""Service -> OAuth scope mapping for Google Workspace.

Read-only is the default. `--write` adds the narrowest write scope each service
offers (e.g. `drive.file`, which only reaches files this app created, rather
than all of Drive). Directory is read-only on purpose: this brick never manages
users.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

PREFIX = "https://www.googleapis.com/auth/"

SERVICES: Tuple[str, ...] = ("mail", "drive", "calendar", "docs", "sheets", "directory")

READ: Dict[str, List[str]] = {
    "mail": ["gmail.readonly"],
    "drive": ["drive.readonly"],
    "calendar": ["calendar.readonly"],
    "docs": ["documents.readonly"],
    "sheets": ["spreadsheets.readonly"],
    "directory": ["admin.directory.user.readonly"],
}

WRITE: Dict[str, List[str]] = {
    "mail": ["gmail.send", "gmail.compose"],
    "drive": ["drive.file"],
    "calendar": ["calendar.events"],
    "docs": ["documents"],
    "sheets": ["spreadsheets"],
    "directory": [],
}


def full(scope: str) -> str:
    """Expand a short scope name to its URL form (idempotent)."""
    if scope.startswith("https://") or scope in ("openid", "email", "profile"):
        return scope
    return PREFIX + scope


def short(scope: str) -> str:
    """Strip the common URL prefix for display."""
    return scope[len(PREFIX):] if scope.startswith(PREFIX) else scope


def parse_services(spec: "str | Iterable[str] | None") -> List[str]:
    """`"mail,drive"` -> `["mail", "drive"]`; `None`/`"all"` -> every service.

    Raises:
        ValueError: on an unknown service name, naming the valid ones.
    """
    if spec is None:
        return list(SERVICES)
    items = spec.split(",") if isinstance(spec, str) else list(spec)
    out: List[str] = []
    for raw in items:
        name = raw.strip().lower()
        if not name:
            continue
        if name == "all":
            return list(SERVICES)
        if name == "gmail":
            name = "mail"
        if name == "cal":
            name = "calendar"
        if name not in SERVICES:
            raise ValueError(f"unknown service {raw!r}; choose from {', '.join(SERVICES)}")
        if name not in out:
            out.append(name)
    return out


def scopes_for(services: Iterable[str], write: bool = False) -> List[str]:
    """The full scope URLs to request for these services."""
    out: List[str] = []
    for svc in services:
        for s in READ[svc] + (WRITE[svc] if write else []):
            u = full(s)
            if u not in out:
                out.append(u)
    return out


#: For each capability a call needs, the scopes that satisfy it (any one is
#: enough). The first entry is the one named in a ScopeError.
NEEDS: Dict[str, Tuple[str, ...]] = {
    "mail.read": ("gmail.readonly", "gmail.modify", "https://mail.google.com/"),
    "mail.compose": ("gmail.compose", "gmail.modify", "https://mail.google.com/"),
    "mail.send": ("gmail.send", "gmail.compose", "gmail.modify", "https://mail.google.com/"),
    "drive.read": ("drive.readonly", "drive"),
    "drive.write": ("drive.file", "drive"),
    "calendar.read": ("calendar.readonly", "calendar.events", "calendar",
                      "calendar.events.readonly"),
    "calendar.write": ("calendar.events", "calendar"),
    "docs.read": ("documents.readonly", "documents", "drive.readonly", "drive"),
    "docs.write": ("documents", "drive.file", "drive"),
    "sheets.read": ("spreadsheets.readonly", "spreadsheets", "drive.readonly", "drive"),
    "sheets.write": ("spreadsheets", "drive.file", "drive"),
    "directory.read": ("admin.directory.user.readonly", "admin.directory.user"),
}


def satisfied(capability: str, granted: "Iterable[str] | None") -> bool:
    """True when `granted` holds any scope satisfying `capability`.

    `granted=None` means "unknown" (a platform-supplied token): the call is let
    through and the provider's own 403 becomes the ScopeError.
    """
    if granted is None:
        return True
    have = {full(g) for g in granted}
    return any(full(s) in have for s in NEEDS[capability])


def primary(capability: str) -> str:
    """The scope a ScopeError should tell the user to grant."""
    return full(NEEDS[capability][0])
