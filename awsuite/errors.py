"""Typed errors. Every failure a caller can act on has its own class.

The split matters to an agent: an AuthError means "log in again", a ScopeError
means "log in again WITH this scope", a RateLimited means "wait", and NotFound
means "the id is wrong". Collapsing them into one exception is how an agent ends
up retrying a permission problem forever.
"""

from __future__ import annotations


class SuiteError(Exception):
    """Base class. `status` is the HTTP status when one exists, else 0."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status

    def to_dict(self) -> dict:
        """A JSON-safe description for tool results and `--json` output."""
        return {"error": type(self).__name__, "message": str(self), "status": self.status}


class ConfigError(SuiteError):
    """Local configuration is missing or invalid (no profile, no client id, ...)."""


class AuthError(SuiteError):
    """The credential is missing, expired beyond refresh, or rejected (HTTP 401)."""


class ScopeError(AuthError):
    """The credential is valid but lacks a scope this call needs (HTTP 403)."""

    def __init__(self, message: str, scope: str = "", status: int = 403) -> None:
        super().__init__(message, status)
        self.scope = scope

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["scope"] = self.scope
        return d


class NotFound(SuiteError):
    """The object does not exist, or this account cannot see it (HTTP 404)."""


class RateLimited(SuiteError):
    """HTTP 429 (or a 403 rate-limit reason) that survived every bounded retry."""


class ApiError(SuiteError):
    """Any other non-2xx answer from the provider."""
