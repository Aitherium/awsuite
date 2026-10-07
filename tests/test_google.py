from __future__ import annotations

import base64
import json

import pytest
from conftest import StaticTokens

from awsuite import scopes
from awsuite.errors import ApiError, AuthError, NotFound, RateLimited, ScopeError
from awsuite.providers import google as g
from awsuite.providers.google import GoogleProvider


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


# ---------------------------------------------------------------- request shapes


def test_mail_search_lists_then_fetches_metadata(provider, net):
    net.add({"messages": [{"id": "m1"}]})
    net.add({"id": "m1", "threadId": "t1", "snippet": "hi", "labelIds": ["INBOX"],
             "payload": {"headers": [{"name": "From", "value": "a@x"},
                                     {"name": "Subject", "value": "S"}]}})
    out = provider.mail_search("is:unread", max_results=5)
    assert out[0]["from"] == "a@x" and out[0]["subject"] == "S"
    c0, c1 = net.calls
    assert c0.method == "GET" and c0.path == g.GMAIL + "/messages"
    assert c0.params == {"q": ["is:unread"], "maxResults": ["5"]}
    assert c0.headers["authorization"] == "Bearer tok0"
    assert c1.path == g.GMAIL + "/messages/m1" and c1.params["format"] == ["metadata"]
    assert c1.params["metadataHeaders"] == ["From", "To", "Subject", "Date"]


def test_mail_read_prefers_plain_text(provider, net):
    net.add({"id": "m1", "payload": {"mimeType": "multipart/alternative", "headers": [], "parts": [
        {"mimeType": "text/plain", "body": {"data": _b64("plain body")}},
        {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
        {"mimeType": "application/pdf", "filename": "a.pdf", "body": {"attachmentId": "x"}}]}})
    out = provider.mail_read("m1")
    assert out["body"] == "plain body" and out["attachments"] == ["a.pdf"]
    assert net.calls[0].params == {"format": ["full"]}


def test_mail_read_html_fallback(provider, net):
    net.add({"id": "m1", "payload": {"mimeType": "text/html", "headers": [],
                                     "body": {"data": _b64("<p>Hello&nbsp;<b>you</b></p>")}}})
    assert provider.mail_read("m1")["body"] == "Hello you"


def test_mail_draft_and_send_bodies(provider, net):
    net.add({"id": "d1", "message": {"id": "m9"}})
    net.add({"id": "m10", "threadId": "t"})
    assert provider.mail_draft(["a@x"], "Subj", "Body", cc=["c@x"]) == {
        "draft_id": "d1", "message_id": "m9"}
    provider.mail_send(["a@x"], "Subj2", "Body2")
    d, s = net.calls
    assert d.method == "POST" and d.path == g.GMAIL + "/drafts"
    raw = base64.urlsafe_b64decode(d.json()["message"]["raw"]).decode()
    assert "Subject: Subj" in raw and "Cc: c@x" in raw and "Body" in raw
    assert s.path == g.GMAIL + "/messages/send"
    assert "Subject: Subj2" in base64.urlsafe_b64decode(s.json()["raw"]).decode()


def test_mail_labels(provider, net):
    net.add({"labels": [{"id": "L", "name": "Work", "type": "user"}]})
    assert provider.mail_labels() == [{"id": "L", "name": "Work", "type": "user"}]
    assert net.calls[0].path == g.GMAIL + "/labels"


def test_drive_search_query_composition(provider, net):
    net.add({"files": [{"id": "f", "name": "N", "mimeType": "text/plain", "size": "3"}]})
    out = provider.drive_search(text="it's q3", q="mimeType = 'x'", max_results=7)
    assert out[0]["size"] == 3
    p = net.calls[0].params
    assert p["q"] == ["((mimeType = 'x') and fullText contains 'it\\'s q3') and trashed = false"]
    assert p["pageSize"] == ["7"] and p["supportsAllDrives"] == ["true"]


def test_drive_read_exports_google_doc(provider, net):
    net.add({"id": "f", "name": "Doc", "mimeType": "application/vnd.google-apps.document"})
    net.add({"id": "f", "name": "Doc", "mimeType": "application/vnd.google-apps.document"})
    net.add(b"hello doc")
    out = provider.drive_read("f", max_chars=5)
    assert out["text"] == "hello" and out["truncated"] and out["exported_as"] == "text/plain"
    exp = net.calls[2]
    assert exp.path == g.DRIVE + "/files/f/export" and exp.params["mimeType"] == ["text/plain"]


def test_drive_read_binary_is_not_downloaded(provider, net):
    net.add({"id": "f", "name": "x.pdf", "mimeType": "application/pdf"})
    out = provider.drive_read("f")
    assert out["text"] is None and "binary" in out["note"] and len(net.calls) == 1


def test_drive_fetch_sheet_as_csv(provider, net):
    net.add({"id": "s", "name": "Budget", "mimeType": "application/vnd.google-apps.spreadsheet"})
    net.add(b"a,b\n1,2\n")
    got = provider.drive_fetch("s")
    assert got["name"] == "Budget.csv" and got["mime_type"] == "text/csv"
    assert net.calls[1].params["mimeType"] == ["text/csv"]


def test_drive_upload_multipart_related(provider, net):
    net.add({"id": "new", "name": "n.txt", "mimeType": "text/plain"})
    out = provider.drive_upload("n.txt", b"DATA", "text/plain", parent="P")
    c = net.calls[0]
    assert out["id"] == "new" and c.method == "POST" and c.path == g.DRIVE_UPLOAD
    assert c.params["uploadType"] == ["multipart"]
    assert c.headers["content-type"].startswith("multipart/related; boundary=")
    assert b'"parents": ["P"]' in c.data and b"DATA" in c.data


def test_calendar_events_window(provider, net):
    net.add({"items": [{"id": "e", "summary": "Sync", "start": {"dateTime": "T1"},
                        "end": {"date": "D2"}, "attendees": [{"email": "a@x"}]}]})
    out = provider.calendar_events(time_min="A", time_max="B", query="sync", max_results=3)
    assert out[0]["start"] == "T1" and out[0]["end"] == "D2" and out[0]["attendees"] == ["a@x"]
    c = net.calls[0]
    assert c.path == g.CALENDAR + "/calendars/primary/events"
    assert c.params["timeMin"] == ["A"] and c.params["singleEvents"] == ["true"]
    assert c.params["orderBy"] == ["startTime"] and c.params["q"] == ["sync"]


def test_calendar_create_body(provider, net, monkeypatch):
    monkeypatch.setenv("AWSUITE_TIMEZONE", "Europe/Paris")
    net.add({"id": "e1"})
    provider.calendar_create("Lunch", "2026-01-02T12:00:00", "2026-01-02T13:00:00+01:00",
                             attendees=["a@x"])
    body = net.calls[0].json()
    assert body["start"] == {"dateTime": "2026-01-02T12:00:00", "timeZone": "Europe/Paris"}
    assert body["end"] == {"dateTime": "2026-01-02T13:00:00+01:00"}
    assert body["attendees"] == [{"email": "a@x"}]
    assert net.calls[0].params["sendUpdates"] == ["all"]


def test_calendar_freebusy(provider, net):
    net.add({"calendars": {"primary": {"busy": [{"start": "a", "end": "b"}]}}})
    assert provider.calendar_freebusy("A", "B", ["primary"]) == {
        "primary": [{"start": "a", "end": "b"}]}
    assert net.calls[0].json()["items"] == [{"id": "primary"}]


def test_docs_read_flattens_paragraphs_and_tables(provider, net):
    net.add({"documentId": "d", "title": "T", "body": {"content": [
        {"paragraph": {"elements": [{"textRun": {"content": "Hello\n"}}]}},
        {"table": {"tableRows": [{"tableCells": [
            {"content": [{"paragraph": {"elements": [{"textRun": {"content": "a"}}]}}]},
            {"content": [{"paragraph": {"elements": [{"textRun": {"content": "b"}}]}}]}]}]}}]}})
    out = provider.docs_read("d")
    assert out["text"] == "Hello\na | b\n" and out["title"] == "T"


def test_docs_append_and_create(provider, net):
    net.add({"documentId": "d"})
    net.add({"documentId": "n", "title": "New"})
    provider.docs_append("d", "more")
    provider.docs_create("New")
    a, c = net.calls
    assert a.path == g.DOCS + "/d:batchUpdate"
    assert a.json() == {"requests": [{"insertText": {"endOfSegmentLocation": {},
                                                     "text": "more"}}]}
    assert c.method == "POST" and c.path == g.DOCS and c.json() == {"title": "New"}


def test_sheets_read_and_append(provider, net):
    net.add({"range": "Sheet1!A1:B2", "values": [["1", "2"]]})
    net.add({"updates": {"updatedRange": "Sheet1!A3:B3", "updatedRows": 1}})
    assert provider.sheets_read("S", "Sheet1!A1:B2")["values"] == [["1", "2"]]
    out = provider.sheets_append("S", "Sheet1!A1", [["x", "y"]])
    r, a = net.calls
    assert "values/Sheet1%21A1%3AB2" in r.url
    assert a.method == "POST" and a.url.split("?")[0].endswith("values/Sheet1%21A1:append")
    assert a.params["valueInputOption"] == ["USER_ENTERED"] and a.json() == {"values": [["x", "y"]]}
    assert out["updated_rows"] == 1


def test_directory_users(provider, net):
    net.add({"users": [{"primaryEmail": "u@x", "name": {"fullName": "U"}, "isAdmin": True}]})
    out = provider.directory_users(query="isAdmin=true")
    assert out[0] == {"email": "u@x", "name": "U", "admin": True, "suspended": False,
                      "org_unit": None, "last_login": None}
    p = net.calls[0].params
    assert p["customer"] == ["my_customer"] and p["query"] == ["isAdmin=true"]


# ---------------------------------------------------------------- error mapping


def test_401_refreshes_once_then_succeeds(net):
    tokens = StaticTokens(can_refresh=True)
    p = GoogleProvider(tokens, sleep=lambda s: None)
    net.add({"error": {"code": 401, "message": "expired"}}, 401)
    net.add({"labels": []})
    assert p.mail_labels() == []
    assert tokens.refreshes == 1 and net.calls[1].headers["authorization"] == "Bearer tok1"


def test_401_without_refresh_is_auth_error(provider, net):
    net.add({"error": {"code": 401, "message": "Invalid Credentials"}}, 401)
    with pytest.raises(AuthError, match="Invalid Credentials") as ei:
        provider.mail_labels()
    assert not isinstance(ei.value, ScopeError)


def test_403_insufficient_scope_names_scope(provider, net):
    net.add({"error": {"code": 403, "message": "Request had insufficient authentication scopes.",
                       "status": "PERMISSION_DENIED",
                       "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}, 403)
    with pytest.raises(ScopeError) as ei:
        provider.mail_send(["a@x"], "s", "b")
    assert ei.value.scope == scopes.full("gmail.send") and "gmail.send" in str(ei.value)


def test_403_other_is_api_error(provider, net):
    net.add({"error": {"code": 403, "message": "The caller does not have permission",
                       "errors": [{"reason": "forbidden"}]}}, 403)
    with pytest.raises(ApiError, match="forbidden"):
        provider.docs_read("d")


def test_404_is_not_found(provider, net):
    net.add({"error": {"code": 404, "message": "File not found: x"}}, 404)
    with pytest.raises(NotFound, match="File not found"):
        provider.drive_get("x")


def test_429_retried_then_rate_limited(provider, net):
    for _ in range(4):
        net.add({"error": {"code": 429, "message": "slow down"}}, 429)
    with pytest.raises(RateLimited, match="slow down"):
        provider.mail_labels()
    assert len(net.calls) == 4 and len(provider.sleeps) == 3
    assert all(s <= provider.backoff_cap + 0.25 for s in provider.sleeps)


def test_429_then_success_honours_retry_after(provider, net):
    net.add({"error": {"code": 429}}, 429, headers={"Retry-After": "2"})
    net.add({"labels": []})
    assert provider.mail_labels() == [] and provider.sleeps == [2.0]


def test_403_rate_limit_reason_is_retried(provider, net):
    net.add({"error": {"code": 403, "errors": [{"reason": "userRateLimitExceeded"}]}}, 403)
    net.add({"labels": []})
    assert provider.mail_labels() == [] and len(provider.sleeps) == 1


def test_503_retried_then_api_error(provider, net):
    for _ in range(4):
        net.add(b"backend error", 503)
    with pytest.raises(ApiError) as ei:
        provider.mail_labels()
    assert ei.value.status == 503 and not isinstance(ei.value, RateLimited)


def test_connection_error_retried(provider, net):
    net.add(status=-1)
    net.add({"labels": []})
    assert provider.mail_labels() == [] and len(provider.sleeps) == 1


def test_scope_preflight_refuses_without_network(no_net):
    p = GoogleProvider(StaticTokens(granted={scopes.full("drive.readonly")}))
    with pytest.raises(ScopeError) as ei:
        p.drive_upload("x", b"y")
    assert ei.value.scope == scopes.full("drive.file") and "--write" in str(ei.value)
    with pytest.raises(ScopeError, match="calendar.readonly"):
        p.calendar_events()


def test_error_info_tolerates_non_json():
    msg, reasons = GoogleProvider._error_info(b"<html>Bad Gateway</html>")
    assert "Bad Gateway" in msg and reasons == []
    assert json.loads(json.dumps(GoogleProvider._error_info(b'{"error":"invalid_grant"}'))) == [
        "invalid_grant", ["invalid_grant"]]
