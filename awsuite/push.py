"""Push Drive documents into an Aither workspace's document store.

An Aither workspace (any portal exposing the documents API)
ingests documents through `POST <base>/api/documents/upload`, a
multipart form with the file in field `file` and an optional `doc_type`, and
authenticates the caller by their session: a bearer token or a session cookie.
Both are read from an environment variable NAMED on the command line, so the
credential never appears in argv or shell history.

Google-native files are exported first (Docs and Slides to .txt, Sheets to
.csv) because those are formats the ingest side can extract text from; other
files are sent as-is when their extension is one it accepts.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from .errors import ApiError, AuthError, ConfigError

ACCEPTED_SUFFIXES = (".pdf", ".docx", ".doc", ".txt", ".md", ".csv")
UPLOAD_PATH = "/api/documents/upload"


def check_base(base: str) -> str:
    """Normalise and vet the Workspace base URL. https only, except loopback."""
    u = urllib.parse.urlparse(base.strip())
    if u.scheme not in ("https", "http") or not u.netloc:
        raise ConfigError(f"--to must be a URL like https://workspace.example.com, got {base!r}")
    if u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ConfigError("refusing to send a session credential over plain http to a "
                          "non-loopback host; use https")
    return base.strip().rstrip("/")


def credential_headers(bearer_env: str = "", cookie_env: str = "") -> Dict[str, str]:
    """Build the auth header from a NAMED env var; error if it is unset."""
    if bearer_env:
        val = os.environ.get(bearer_env, "")
        if not val:
            raise AuthError(f"environment variable {bearer_env} is empty or unset")
        return {"Authorization": "Bearer " + val.removeprefix("Bearer ").strip()}
    if cookie_env:
        val = os.environ.get(cookie_env, "")
        if not val:
            raise AuthError(f"environment variable {cookie_env} is empty or unset")
        return {"Cookie": val.strip()}
    raise ConfigError("pass --bearer-env NAME (or --cookie-env NAME) naming the variable "
                      "that holds your workspace session")


def multipart(fields: Dict[str, str], file_field: str, filename: str, content: bytes,
              mime: str) -> tuple:
    """(body, content_type) for a multipart/form-data upload."""
    boundary = "awsuite-" + uuid.uuid4().hex
    parts: List[bytes] = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
                     f"{v}\r\n".encode("utf-8"))
    safe = filename.replace('"', "'").replace("\r", " ").replace("\n", " ")
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                 f'filename="{safe}"\r\nContent-Type: {mime}\r\n\r\n'.encode("utf-8"))
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def upload(base: str, headers: Dict[str, str], filename: str, content: bytes,
           mime: str = "application/octet-stream", doc_type: str = "",
           timeout: float = 120.0) -> Dict[str, Any]:
    """POST one document to the workspace; returns its JSON answer."""
    fields = {"doc_type": doc_type} if doc_type else {}
    body, ctype = multipart(fields, "file", filename, content, mime)
    req = urllib.request.Request(check_base(base) + UPLOAD_PATH, data=body, method="POST",
                                 headers={**headers, "Content-Type": ctype,
                                          "Accept": "application/json",
                                          "User-Agent": "awsuite"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = (exc.read() or b"")[:300].decode("utf-8", "replace")
        if exc.code in (401, 403):
            raise AuthError(f"workspace refused the session ({exc.code}): {detail}",
                            exc.code) from exc
        raise ApiError(f"workspace upload failed ({exc.code}): {detail}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"workspace unreachable: {exc.reason}") from exc
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {"raw": raw[:300].decode("utf-8", "replace")}


def push(provider: Any, file_ids: List[str], base: str, headers: Dict[str, str],
         doc_type: str = "", confirm: bool = False) -> Dict[str, Any]:
    """Fetch each Drive file and upload it. Without `confirm`, a dry-run plan."""
    base = check_base(base)
    plan: List[Dict[str, Any]] = []
    for fid in file_ids:
        if not confirm:
            meta = provider.drive_get(fid)
            plan.append({"id": fid, "name": meta.get("name"), "mime_type": meta.get("mime_type"),
                         "action": "would upload"})
            continue
        try:
            got = provider.drive_fetch(fid)
        except ApiError as exc:
            plan.append({"id": fid, "action": "skipped", "reason": str(exc)})
            continue
        name = got["name"]
        if PurePosixPath(name).suffix.lower() not in ACCEPTED_SUFFIXES:
            plan.append({"id": fid, "name": name, "action": "skipped",
                         "reason": f"the workspace extracts text from {', '.join(ACCEPTED_SUFFIXES)}"})
            continue
        res = upload(base, headers, name, got["content"], got["mime_type"], doc_type)
        plan.append({"id": fid, "name": name, "action": "uploaded", "response": res})
    out: Dict[str, Any] = {"target": base + UPLOAD_PATH, "files": plan}
    if not confirm:
        out["dry_run"] = True
        out["note"] = "Nothing was uploaded. Re-run with --confirm to push these files."
    return out


def resolve_ids(provider: Any, ids: Optional[List[str]], query: str, limit: int) -> List[str]:
    """Explicit ids, else the ids of a Drive full-text search."""
    if ids:
        return list(ids)
    if not query:
        raise ConfigError("give --id FILE_ID (repeatable) or --query TEXT")
    return [f["id"] for f in provider.drive_search(text=query, max_results=limit)]
