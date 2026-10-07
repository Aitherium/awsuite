"""Google Workspace over plain REST (urllib only).

Gmail, Drive, Calendar, Docs, Sheets and the Admin Directory. Each call names
the capability it needs (`scopes.NEEDS`), so a profile known to lack a scope
fails BEFORE the network with a ScopeError that names the scope to grant, and a
403 from Google for the same reason maps to the same error.

Retries: 429, the 403 rate-limit reasons, 5xx and connection errors are retried
with bounded exponential backoff (honouring `Retry-After`, capped). A 401 gets
exactly one forced token refresh and one retry.
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Optional

from .. import scopes as _scopes
from ..auth import TokenSource
from ..errors import ApiError, AuthError, NotFound, RateLimited, ScopeError

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
DRIVE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
CALENDAR = "https://www.googleapis.com/calendar/v3"
DOCS = "https://docs.googleapis.com/v1/documents"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DIRECTORY = "https://admin.googleapis.com/admin/directory/v1"

RETRY_STATUSES = (429, 500, 502, 503, 504)
_RATE_REASONS = ("rateLimitExceeded", "userRateLimitExceeded", "RATE_LIMIT_EXCEEDED")
_SCOPE_MARKERS = ("insufficient authentication scopes", "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
                  "insufficient_scope", "insufficientPermissions")

#: Google-native types and what they export to as text.
EXPORTS: Dict[str, tuple] = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}

_TEXTLIKE = ("text/", "application/json", "application/xml", "application/x-yaml",
             "application/csv", "application/javascript")


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(a, b)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class GoogleProvider:
    """The Google implementation of `awsuite.providers.base.Provider`."""

    name = "google"

    def __init__(self, tokens: TokenSource, *, timeout: float = 30.0, max_retries: int = 3,
                 sleep: Callable[[float], None] = time.sleep,
                 backoff_cap: float = 8.0) -> None:
        self.tokens = tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleep
        self.backoff_cap = backoff_cap

    # ------------------------------------------------------------------ transport

    def _backoff(self, attempt: int, retry_after: "str | None") -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.backoff_cap * 4)
            except ValueError:
                _ = None
        return min(self.backoff_cap, 0.5 * (2 ** attempt)) + random.uniform(0, 0.25)

    @staticmethod
    def _error_info(body: bytes) -> tuple:
        """(message, reasons) from a Google error body; tolerant of non-JSON."""
        try:
            j = json.loads(body or b"{}")
        except ValueError:
            return (body[:200].decode("utf-8", "replace"), [])
        err = j.get("error") if isinstance(j, dict) else None
        if isinstance(err, str):
            return (j.get("error_description") or err, [err])
        if not isinstance(err, dict):
            return ("", [])
        reasons = [e.get("reason", "") for e in err.get("errors") or [] if isinstance(e, dict)]
        reasons += [d.get("reason", "") for d in err.get("details") or [] if isinstance(d, dict)]
        if err.get("status"):
            reasons.append(err["status"])
        return (str(err.get("message") or ""), [r for r in reasons if r])

    def _check_scope(self, need: "str | None") -> None:
        if need and not _scopes.satisfied(need, self.tokens.granted_scopes()):
            scope = _scopes.primary(need)
            svc = need.split(".")[0]
            write = " --write" if need.endswith(("write", "send", "compose")) else ""
            raise ScopeError(
                f"this call needs scope {_scopes.short(scope)!r}, which the profile was "
                f"not granted; run `awsuite auth login --scopes {svc}{write}`", scope=scope)

    def request(self, method: str, url: str, *, params: Optional[Dict[str, Any]] = None,
                json_body: Any = None, data: Optional[bytes] = None,
                content_type: str = "", need: Optional[str] = None,
                raw: bool = False) -> Any:
        """One API call with retries and typed errors. JSON in, JSON (or bytes) out."""
        self._check_scope(need)
        if params:
            clean = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean:
                url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(clean,
                                                                                  doseq=True)
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        attempt = 0
        refreshed = False
        while True:
            headers = {"Authorization": "Bearer " + self.tokens.token(),
                       "Accept": "application/json", "User-Agent": "awsuite"}
            if content_type:
                headers["Content-Type"] = content_type
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                if raw:
                    return body
                return json.loads(body) if body else {}
            except urllib.error.HTTPError as exc:
                status = exc.code
                try:
                    ebody = exc.read() or b""
                except (OSError, AttributeError):
                    ebody = b""
                msg, reasons = self._error_info(ebody)
                hdrs = exc.headers or {}
                www = (hdrs.get("WWW-Authenticate") or "") if hasattr(hdrs, "get") else ""
                if status == 401:
                    if not refreshed and self.tokens.force_refresh():
                        refreshed = True
                        continue
                    raise AuthError(f"unauthorized (401): {msg or 'credential rejected'}; "
                                    f"run `awsuite auth login`", 401) from exc
                rate = status == 429 or (status == 403 and any(r in _RATE_REASONS
                                                               for r in reasons))
                if status == 403 and not rate:
                    blob = " ".join([msg, www] + reasons)
                    if any(m in blob for m in _SCOPE_MARKERS):
                        scope = _scopes.primary(need) if need else ""
                        raise ScopeError(
                            f"insufficient scope (403): {msg}. Grant "
                            f"{_scopes.short(scope) or 'the needed scope'} via "
                            f"`awsuite auth login --scopes ...`", scope=scope) from exc
                    raise ApiError(f"forbidden (403): {msg}", 403) from exc
                if status == 404:
                    raise NotFound(f"not found (404): {msg or url.split('?')[0]}", 404) from exc
                if rate or status in RETRY_STATUSES:
                    if attempt < self.max_retries:
                        ra = hdrs.get("Retry-After") if hasattr(hdrs, "get") else None
                        self._sleep(self._backoff(attempt, ra))
                        attempt += 1
                        continue
                    if rate:
                        raise RateLimited(f"rate limited ({status}) after {attempt + 1} "
                                          f"attempts: {msg}", status) from exc
                raise ApiError(f"HTTP {status}: {msg}", status) from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    self._sleep(self._backoff(attempt, None))
                    attempt += 1
                    continue
                raise ApiError(f"unreachable: {exc.reason}") from exc

    # ------------------------------------------------------------------ gmail

    @staticmethod
    def _headers(payload: Dict[str, Any]) -> Dict[str, str]:
        return {h.get("name", "").lower(): h.get("value", "")
                for h in payload.get("headers") or []}

    @classmethod
    def _body_text(cls, payload: Dict[str, Any]) -> tuple:
        """(plain text, attachment names) from a Gmail payload tree."""
        plain: List[str] = []
        html: List[str] = []
        attachments: List[str] = []

        def walk(part: Dict[str, Any]) -> None:
            mime = part.get("mimeType", "")
            if part.get("filename"):
                attachments.append(part["filename"])
            data = (part.get("body") or {}).get("data")
            if data and not part.get("filename"):
                text = _b64url_decode(data).decode("utf-8", "replace")
                if mime == "text/plain":
                    plain.append(text)
                elif mime == "text/html":
                    html.append(text)
            for p in part.get("parts") or []:
                walk(p)

        walk(payload)
        text = "\n".join(plain) if plain else _strip_html("\n".join(html))
        return text, attachments

    def mail_search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        listing = self.request("GET", f"{GMAIL}/messages", need="mail.read",
                               params={"q": query, "maxResults": max(1, min(max_results, 100))})
        out = []
        for m in listing.get("messages") or []:
            msg = self.request("GET", f"{GMAIL}/messages/{m['id']}", need="mail.read",
                               params={"format": "metadata",
                                       "metadataHeaders": ["From", "To", "Subject", "Date"]})
            h = self._headers(msg.get("payload") or {})
            out.append({"id": msg.get("id"), "thread_id": msg.get("threadId"),
                        "from": h.get("from", ""), "to": h.get("to", ""),
                        "subject": h.get("subject", ""), "date": h.get("date", ""),
                        "snippet": msg.get("snippet", ""), "labels": msg.get("labelIds") or []})
        return out

    def mail_read(self, message_id: str) -> Dict[str, Any]:
        msg = self.request("GET", f"{GMAIL}/messages/{urllib.parse.quote(message_id)}",
                           need="mail.read", params={"format": "full"})
        payload = msg.get("payload") or {}
        h = self._headers(payload)
        text, attachments = self._body_text(payload)
        return {"id": msg.get("id"), "thread_id": msg.get("threadId"),
                "from": h.get("from", ""), "to": h.get("to", ""), "cc": h.get("cc", ""),
                "subject": h.get("subject", ""), "date": h.get("date", ""),
                "labels": msg.get("labelIds") or [], "body": text,
                "attachments": attachments}

    def mail_labels(self) -> List[Dict[str, Any]]:
        res = self.request("GET", f"{GMAIL}/labels", need="mail.read")
        return [{"id": lb.get("id"), "name": lb.get("name"), "type": lb.get("type")}
                for lb in res.get("labels") or []]

    @staticmethod
    def build_raw(to: List[str], subject: str, body: str,
                  cc: Optional[List[str]] = None) -> str:
        """RFC 5322 message, base64url-encoded, as Gmail's `raw` field wants."""
        msg = EmailMessage()
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg.set_content(body)
        return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    def mail_draft(self, to: List[str], subject: str, body: str,
                   cc: Optional[List[str]] = None) -> Dict[str, Any]:
        res = self.request("POST", f"{GMAIL}/drafts", need="mail.compose",
                           json_body={"message": {"raw": self.build_raw(to, subject, body, cc)}})
        return {"draft_id": res.get("id"), "message_id": (res.get("message") or {}).get("id")}

    def mail_send(self, to: List[str], subject: str, body: str,
                  cc: Optional[List[str]] = None) -> Dict[str, Any]:
        res = self.request("POST", f"{GMAIL}/messages/send", need="mail.send",
                           json_body={"raw": self.build_raw(to, subject, body, cc)})
        return {"message_id": res.get("id"), "thread_id": res.get("threadId"),
                "labels": res.get("labelIds") or []}

    # ------------------------------------------------------------------ drive

    _FILE_FIELDS = "id,name,mimeType,size,modifiedTime,webViewLink,owners(emailAddress)"

    @staticmethod
    def _escape_q(text: str) -> str:
        return text.replace("\\", "\\\\").replace("'", "\\'")

    def drive_search(self, text: str = "", q: str = "",
                     max_results: int = 20) -> List[Dict[str, Any]]:
        query = q
        if text:
            clause = f"fullText contains '{self._escape_q(text)}'"
            query = f"({query}) and {clause}" if query else clause
        if "trashed" not in query:
            query = f"({query}) and trashed = false" if query else "trashed = false"
        res = self.request("GET", f"{DRIVE}/files", need="drive.read", params={
            "q": query, "pageSize": max(1, min(max_results, 1000)),
            "fields": f"files({self._FILE_FIELDS}),nextPageToken",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
            "orderBy": "modifiedTime desc" if not text else None,
        })
        return [self._file(f) for f in res.get("files") or []]

    @staticmethod
    def _file(f: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": f.get("id"), "name": f.get("name"), "mime_type": f.get("mimeType"),
                "size": int(f["size"]) if f.get("size") else None,
                "modified": f.get("modifiedTime"), "link": f.get("webViewLink"),
                "owners": [o.get("emailAddress") for o in f.get("owners") or []]}

    def drive_get(self, file_id: str) -> Dict[str, Any]:
        f = self.request("GET", f"{DRIVE}/files/{urllib.parse.quote(file_id)}",
                         need="drive.read",
                         params={"fields": self._FILE_FIELDS, "supportsAllDrives": "true"})
        return self._file(f)

    def drive_fetch(self, file_id: str) -> Dict[str, Any]:
        """Bytes of a file: Google-native types exported, others downloaded."""
        meta = self.drive_get(file_id)
        mime = meta.get("mime_type") or ""
        fid = urllib.parse.quote(file_id)
        if mime in EXPORTS:
            out_mime, ext = EXPORTS[mime]
            content = self.request("GET", f"{DRIVE}/files/{fid}/export", need="drive.read",
                                   params={"mimeType": out_mime}, raw=True)
            name = meta.get("name") or file_id
            if not name.lower().endswith(ext):
                name += ext
            return {"meta": meta, "name": name, "mime_type": out_mime, "content": content,
                    "exported": True}
        if mime.startswith("application/vnd.google-apps."):
            raise ApiError(f"{mime} has no text export; open it in Drive instead")
        content = self.request("GET", f"{DRIVE}/files/{fid}", need="drive.read",
                               params={"alt": "media", "supportsAllDrives": "true"}, raw=True)
        return {"meta": meta, "name": meta.get("name") or file_id, "mime_type": mime,
                "content": content, "exported": False}

    def drive_read(self, file_id: str, max_chars: int = 50000) -> Dict[str, Any]:
        meta = self.drive_get(file_id)
        mime = meta.get("mime_type") or ""
        if mime not in EXPORTS and not mime.startswith(_TEXTLIKE):
            return {**meta, "text": None,
                    "note": f"binary file ({mime}); not decoded -- download it instead"}
        got = self.drive_fetch(file_id)
        text = got["content"].decode("utf-8", "replace")
        return {**meta, "text": text[:max_chars], "truncated": len(text) > max_chars,
                "exported_as": got["mime_type"] if got["exported"] else None}

    def drive_upload(self, name: str, content: bytes, mime_type: str = "",
                     parent: str = "") -> Dict[str, Any]:
        mime_type = mime_type or "application/octet-stream"
        meta: Dict[str, Any] = {"name": name}
        if parent:
            meta["parents"] = [parent]
        boundary = "awsuite-" + uuid.uuid4().hex
        body = b"".join([
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
            json.dumps(meta).encode("utf-8"),
            f"\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n".encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        f = self.request("POST", DRIVE_UPLOAD, need="drive.write", data=body,
                         content_type=f"multipart/related; boundary={boundary}",
                         params={"uploadType": "multipart", "supportsAllDrives": "true",
                                 "fields": self._FILE_FIELDS})
        return self._file(f)

    # ------------------------------------------------------------------ calendar

    @staticmethod
    def _event(e: Dict[str, Any]) -> Dict[str, Any]:
        def when(x: Dict[str, Any]) -> str:
            return (x or {}).get("dateTime") or (x or {}).get("date") or ""
        return {"id": e.get("id"), "summary": e.get("summary", ""),
                "start": when(e.get("start")), "end": when(e.get("end")),
                "location": e.get("location", ""),
                "organizer": (e.get("organizer") or {}).get("email", ""),
                "attendees": [a.get("email") for a in e.get("attendees") or []],
                "link": e.get("htmlLink"), "meet": e.get("hangoutLink"),
                "description": (e.get("description") or "")[:1000],
                "status": e.get("status")}

    def calendar_events(self, time_min: str = "", time_max: str = "", calendar: str = "primary",
                        query: str = "", max_results: int = 25) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        res = self.request(
            "GET", f"{CALENDAR}/calendars/{urllib.parse.quote(calendar)}/events",
            need="calendar.read", params={
                "timeMin": time_min or _rfc3339(now),
                "timeMax": time_max or _rfc3339(now + timedelta(days=7)),
                "singleEvents": "true", "orderBy": "startTime",
                "maxResults": max(1, min(max_results, 2500)), "q": query})
        return [self._event(e) for e in res.get("items") or []]

    def calendar_freebusy(self, time_min: str, time_max: str,
                          calendars: List[str]) -> Dict[str, Any]:
        res = self.request("POST", f"{CALENDAR}/freeBusy", need="calendar.read", json_body={
            "timeMin": time_min, "timeMax": time_max,
            "items": [{"id": c} for c in calendars or ["primary"]]})
        return {cid: (v or {}).get("busy") or [] for cid, v in (res.get("calendars") or {}).items()}

    @staticmethod
    def _when(value: str) -> Dict[str, str]:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return {"date": value}
        out = {"dateTime": value}
        if not re.search(r"(Z|[+-]\d{2}:?\d{2})$", value):
            out["timeZone"] = os.environ.get("AWSUITE_TIMEZONE") or "UTC"
        return out

    def calendar_create(self, summary: str, start: str, end: str, calendar: str = "primary",
                        description: str = "", location: str = "",
                        attendees: Optional[List[str]] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"summary": summary, "start": self._when(start),
                                "end": self._when(end)}
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
        e = self.request("POST", f"{CALENDAR}/calendars/{urllib.parse.quote(calendar)}/events",
                         need="calendar.write", json_body=body,
                         params={"sendUpdates": "all" if attendees else "none"})
        return self._event(e)

    # ------------------------------------------------------------------ docs

    @classmethod
    def _doc_text(cls, content: List[Dict[str, Any]]) -> str:
        out: List[str] = []
        for el in content or []:
            if "paragraph" in el:
                for pe in el["paragraph"].get("elements") or []:
                    out.append((pe.get("textRun") or {}).get("content", ""))
            elif "table" in el:
                for row in el["table"].get("tableRows") or []:
                    cells = [cls._doc_text(c.get("content") or []).strip()
                             for c in row.get("tableCells") or []]
                    out.append(" | ".join(cells) + "\n")
            elif "tableOfContents" in el:
                out.append(cls._doc_text(el["tableOfContents"].get("content") or []))
        return "".join(out)

    def docs_read(self, document_id: str) -> Dict[str, Any]:
        d = self.request("GET", f"{DOCS}/{urllib.parse.quote(document_id)}", need="docs.read")
        return {"document_id": d.get("documentId"), "title": d.get("title", ""),
                "revision_id": d.get("revisionId"),
                "text": self._doc_text((d.get("body") or {}).get("content") or [])}

    def docs_create(self, title: str) -> Dict[str, Any]:
        d = self.request("POST", DOCS, need="docs.write", json_body={"title": title})
        return {"document_id": d.get("documentId"), "title": d.get("title")}

    def docs_append(self, document_id: str, text: str) -> Dict[str, Any]:
        res = self.request(
            "POST", f"{DOCS}/{urllib.parse.quote(document_id)}:batchUpdate", need="docs.write",
            json_body={"requests": [{"insertText": {"endOfSegmentLocation": {},
                                                    "text": text}}]})
        return {"document_id": res.get("documentId", document_id),
                "appended_chars": len(text)}

    # ------------------------------------------------------------------ sheets

    def sheets_read(self, spreadsheet_id: str, range_: str) -> Dict[str, Any]:
        res = self.request(
            "GET", f"{SHEETS}/{urllib.parse.quote(spreadsheet_id)}/values/"
                   f"{urllib.parse.quote(range_, safe='')}", need="sheets.read")
        return {"range": res.get("range", range_), "values": res.get("values") or []}

    def sheets_append(self, spreadsheet_id: str, range_: str,
                      rows: List[List[str]]) -> Dict[str, Any]:
        res = self.request(
            "POST", f"{SHEETS}/{urllib.parse.quote(spreadsheet_id)}/values/"
                    f"{urllib.parse.quote(range_, safe='')}:append", need="sheets.write",
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json_body={"values": rows})
        upd = res.get("updates") or {}
        return {"updated_range": upd.get("updatedRange"),
                "updated_rows": upd.get("updatedRows", len(rows))}

    # ------------------------------------------------------------------ directory

    def directory_users(self, query: str = "", max_results: int = 50,
                        domain: str = "") -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"maxResults": max(1, min(max_results, 500)),
                                  "orderBy": "email", "projection": "basic", "query": query}
        if domain:
            params["domain"] = domain
        else:
            params["customer"] = "my_customer"
        res = self.request("GET", f"{DIRECTORY}/users", need="directory.read", params=params)
        return [{"email": u.get("primaryEmail"), "name": (u.get("name") or {}).get("fullName"),
                 "admin": bool(u.get("isAdmin")), "suspended": bool(u.get("suspended")),
                 "org_unit": u.get("orgUnitPath"), "last_login": u.get("lastLoginTime")}
                for u in res.get("users") or []]
