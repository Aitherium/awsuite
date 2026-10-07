"""Shared fixtures: an isolated profile home and a fake `urlopen` (no network)."""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_ENV = ("AWSUITE_ACCESS_TOKEN", "AWSUITE_TOKEN_CMD", "AWSUITE_PROFILE",
        "AWSUITE_GOOGLE_CLIENT_ID", "AWSUITE_GOOGLE_CLIENT_SECRET", "AWSUITE_TIMEZONE")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "awsuite-home"
    monkeypatch.setenv("AWSUITE_HOME", str(home))
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    return home


class _Resp:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *a: Any) -> None:
        return None


class Call:
    def __init__(self, req: urllib.request.Request) -> None:
        self.method = req.get_method()
        self.url = req.full_url
        parsed = urllib.parse.urlparse(self.url)
        self.path = parsed.scheme + "://" + parsed.netloc + parsed.path
        self.params = urllib.parse.parse_qs(parsed.query)
        self.headers = {k.lower(): v for k, v in req.header_items()}
        self.data: bytes = req.data or b""

    def json(self) -> Any:
        return json.loads(self.data)

    def form(self) -> Dict[str, str]:
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.data.decode()).items()}


class FakeNet:
    """Queue responses; every request is recorded. An empty queue is a test bug."""

    def __init__(self) -> None:
        self.calls: List[Call] = []
        self.queue: List[tuple] = []

    def add(self, body: Any = None, status: int = 200,
            headers: Optional[Dict[str, str]] = None) -> "FakeNet":
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.queue.append((status, body or b"", headers or {}))
        return self

    def __call__(self, req, timeout=None):
        self.calls.append(Call(req))
        if not self.queue:
            raise AssertionError(f"unexpected request {req.get_method()} {req.full_url}")
        status, body, headers = self.queue.pop(0)
        if status == -1:
            raise urllib.error.URLError("connection refused")
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", headers, io.BytesIO(body))
        return _Resp(body, status)


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


@pytest.fixture
def no_net(monkeypatch):
    def refuse(req, timeout=None):
        raise AssertionError(f"network touched: {req.full_url}")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


class StaticTokens:
    """A TokenSource double: fixed token, optional granted scopes, refresh counter."""

    mode = "test"

    def __init__(self, granted=None, can_refresh=False) -> None:
        self.granted = granted
        self.can_refresh = can_refresh
        self.refreshes = 0

    def token(self) -> str:
        return f"tok{self.refreshes}"

    def force_refresh(self) -> bool:
        if not self.can_refresh:
            return False
        self.refreshes += 1
        return True

    def granted_scopes(self):
        return self.granted

    def describe(self):
        return {"mode": self.mode}


@pytest.fixture
def provider():
    from awsuite.providers.google import GoogleProvider
    sleeps: List[float] = []
    p = GoogleProvider(StaticTokens(), sleep=sleeps.append)
    p.sleeps = sleeps  # type: ignore[attr-defined]
    return p
