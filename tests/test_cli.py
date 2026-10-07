from __future__ import annotations

import json

import pytest

from awsuite import _selftest, auth, cli
from awsuite import push as push_mod
from awsuite.errors import ConfigError
from awsuite.providers import google as g


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_tools_json_lists_table(capsys):
    code, out, _ = _run(capsys, "tools", "--json")
    rows = json.loads(out)
    assert code == 0 and len(rows) == 18
    assert {"name": "suite_mail_send", "writes": True}.items() <= rows[5].items()


def test_send_without_confirm_is_dry_run_and_offline(capsys, no_net, monkeypatch):
    monkeypatch.setenv("AWSUITE_ACCESS_TOKEN", "t" * 20)
    code, out, _ = _run(capsys, "mail", "send", "--to", "a@x", "--to", "b@x,c@x",
                        "--subject", "Hi", "--body", "Yo")
    assert code == 0 and out.startswith("DRY RUN")
    assert '"b@x"' in out and '"c@x"' in out


def test_mail_search_json_through_fake_net(capsys, net, monkeypatch):
    monkeypatch.setenv("AWSUITE_ACCESS_TOKEN", "t" * 20)
    net.add({"messages": []})
    code, out, _ = _run(capsys, "--json", "mail", "search", "is:unread", "--max-results", "4")
    assert code == 0 and json.loads(out)["result"] == []
    assert net.calls[0].params["maxResults"] == ["4"]


def test_sheets_append_rows_flag(capsys, net, monkeypatch):
    monkeypatch.setenv("AWSUITE_ACCESS_TOKEN", "t" * 20)
    net.add({"updates": {"updatedRows": 2}})
    code, _, _ = _run(capsys, "sheets", "append", "S", "Sheet1!A1", "--row", "a,b",
                      "--row", "c,d", "--confirm")
    assert code == 0 and net.calls[0].json() == {"values": [["a", "b"], ["c", "d"]]}


def test_auth_status_without_profile_exits_2(capsys):
    code, out, _ = _run(capsys, "auth", "status", "--json")
    assert code == 2 and json.loads(out)["configured"] is False


def test_profile_flag_anywhere(capsys):
    auth.save_profile("work", {"mode": "token", "token_cmd": "echo x"})
    code, out, _ = _run(capsys, "auth", "status", "--profile", "work", "--json")
    assert code == 0 and json.loads(out)["profile"] == "work"


def test_not_logged_in_is_exit_2(capsys, no_net):
    code, _, err = _run(capsys, "docs", "read", "abc")
    assert code == 2 and "auth login" in err


def test_api_failure_is_exit_1(capsys, net, monkeypatch):
    monkeypatch.setenv("AWSUITE_ACCESS_TOKEN", "t" * 20)
    net.add({"error": {"code": 404, "message": "nope"}}, 404)
    code, out, _ = _run(capsys, "docs", "read", "abc", "--json")
    assert code == 1 and json.loads(out)["error"] == "NotFound"


def test_drive_push_dry_run_uploads_nothing(capsys, net, monkeypatch):
    monkeypatch.setenv("AWSUITE_ACCESS_TOKEN", "t" * 20)
    monkeypatch.setenv("WORKSPACE_TOKEN", "session-bearer-value")
    net.add({"id": "f1", "name": "Plan", "mimeType": "application/vnd.google-apps.document"})
    code, out, _ = _run(capsys, "drive", "push", "--id", "f1", "--to",
                        "https://workspace.example.com", "--bearer-env", "WORKSPACE_TOKEN", "--json")
    res = json.loads(out)
    assert code == 0 and res["dry_run"] is True and res["files"][0]["action"] == "would upload"
    assert len(net.calls) == 1 and "session-bearer-value" not in out


def test_drive_push_confirm_posts_multipart(capsys, net, monkeypatch):
    monkeypatch.setenv("AWSUITE_ACCESS_TOKEN", "t" * 20)
    monkeypatch.setenv("WORKSPACE_TOKEN", "Bearer sess")
    net.add({"id": "f1", "name": "Plan", "mimeType": "application/vnd.google-apps.document"})
    net.add(b"plan text")
    net.add({"id": "doc-1", "chunk_count": 1})
    code, out, _ = _run(capsys, "drive", "push", "--id", "f1", "--to",
                        "https://workspace.example.com/", "--bearer-env", "WORKSPACE_TOKEN", "--doc-type",
                        "proposal", "--confirm", "--json")
    assert code == 0 and json.loads(out)["files"][0]["action"] == "uploaded"
    post = net.calls[2]
    assert post.method == "POST" and post.url == "https://workspace.example.com/api/documents/upload"
    assert post.headers["authorization"] == "Bearer sess"
    assert post.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="file"; filename="Plan.txt"' in post.data and b"plan text" in post.data
    assert b'name="doc_type"\r\n\r\nproposal' in post.data
    assert net.calls[1].path == g.DRIVE + "/files/f1/export"


def test_push_refuses_plain_http_and_missing_env(monkeypatch):
    with pytest.raises(ConfigError, match="plain http"):
        push_mod.check_base("http://workspace.example.com")
    assert push_mod.check_base("http://127.0.0.1:8080/") == "http://127.0.0.1:8080"
    with pytest.raises(Exception, match="NOPE"):
        push_mod.credential_headers("NOPE")


def test_pack_and_skills_install_commands(capsys, tmp_path):
    code, out, _ = _run(capsys, "pack", "install", "--dir", str(tmp_path / "p"), "--json")
    assert code == 0 and (tmp_path / "p" / "awsuite" / ".toolpack.yaml").is_file()
    code, out, _ = _run(capsys, "skills", "install", "--dir", str(tmp_path / "s"), "--json")
    assert code == 0 and len(json.loads(out)["skills"]) == 4


def test_doctor_json_reports_unconfigured_and_unreachable(capsys, net):
    net.add(status=-1)
    code, out, _ = _run(capsys, "doctor", "--json")
    checks = {c["name"]: c for c in json.loads(out)}
    assert checks["python"]["status"] == "ok"
    assert checks["profile"]["status"] == "fail"
    assert checks["reach"]["status"] == "unjudged"
    assert code == 1


def test_doctor_reach_ok_on_http_answer(capsys, net, monkeypatch):
    monkeypatch.setenv("AWSUITE_ACCESS_TOKEN", "t" * 20)
    net.add({"error": "invalid_request"}, 400)
    code, out, _ = _run(capsys, "doctor", "--json")
    assert code == 0 and all(c["status"] == "ok" for c in json.loads(out))
    assert net.calls[0].path == "https://oauth2.googleapis.com/token"


def test_self_test_passes(capsys, no_net):
    assert _selftest.run() == 0
    assert "self-test: PASS" in capsys.readouterr().out
