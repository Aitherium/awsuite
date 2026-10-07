from __future__ import annotations

import base64
import http.client
import json
import os
import sys
import time
import urllib.parse

import pytest

from awsuite import auth, scopes
from awsuite.errors import AuthError, ConfigError


def test_pkce_rfc7636_vector():
    assert (auth.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
            == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")


def test_pkce_verifier_shape_and_fresh():
    a, b = auth.pkce_verifier(), auth.pkce_verifier()
    assert a != b and len(a) == 43 and "=" not in a
    assert set(a) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_build_auth_url_carries_pkce_and_incremental_scopes():
    url = auth.build_auth_url("cid", "http://127.0.0.1:5555/", ["s1", "s2"], "st", "CH")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["code_challenge"] == ["CH"] and q["code_challenge_method"] == ["S256"]
    assert q["scope"] == ["s1 s2"] and q["include_granted_scopes"] == ["true"]
    assert q["access_type"] == ["offline"] and q["state"] == ["st"]


def test_mask_never_reveals():
    tok = "ya29.a0AfH6SMB" + "x" * 100 + "TAIL"
    m = auth.mask(tok)
    assert tok not in m and "TAIL" not in m and "len=118" in m
    assert auth.mask("short") .startswith("****") and auth.mask(None) == "(none)"
    assert auth.mask(tok) == auth.mask(tok)  # fingerprint is stable -> comparable


def test_masked_dict_hides_secret_fields():
    d = auth.masked({"access_token": "a" * 40, "client_secret": "s" * 20, "mode": "oauth"})
    assert "a" * 40 not in json.dumps(d) and "s" * 20 not in json.dumps(d)
    assert d["mode"] == "oauth"


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_profile_written_0600():
    p = auth.save_profile("x", {"mode": "oauth"})
    assert (p.stat().st_mode & 0o777) == 0o600


def test_profile_roundtrip_and_bad_name():
    auth.save_profile("work", {"mode": "token"})
    assert auth.load_profile("work") == {"mode": "token"}
    assert auth.list_profiles() == ["work"]
    with pytest.raises(ConfigError):
        auth.profile_path("../evil")


def test_no_profile_no_env_is_config_error():
    with pytest.raises(ConfigError, match="auth login"):
        auth.load_token_source("default")


def test_env_token_mode(monkeypatch):
    monkeypatch.setenv("AWSUITE_ACCESS_TOKEN", "platform-token-123456789")
    src = auth.load_token_source()
    assert src.mode == "token" and src.token() == "platform-token-123456789"
    assert src.force_refresh() is False
    assert "platform-token-123456789" not in json.dumps(src.describe())


def _py(code: str) -> str:
    exe = sys.executable
    return f'"{exe}" -c "{code}"' if os.name == "nt" else f"'{exe}' -c '{code}'"


def test_token_cmd_plain_and_cached():
    src = auth.CommandTokenSource(command=_py("print(123)"))
    assert src.token() == "123"
    src.command = _py("print(456)")
    assert src.token() == "123"  # cached until expiry
    assert src.force_refresh() and src.token() == "456"


def test_token_cmd_json_with_expiry():
    code = "import json; print(json.dumps(dict(access_token=789, expires_in=5)))"
    src = auth.CommandTokenSource(command=_py(code))
    assert src.token() == "789"
    assert src._exp - time.time() < 10


def test_token_cmd_failure_is_auth_error():
    src = auth.CommandTokenSource(command=_py("import sys; sys.exit(3)"))
    with pytest.raises(AuthError, match="exited 3"):
        src.token()


def test_oauth_refresh_on_expiry(net):
    auth.save_profile("default", {"mode": "oauth", "client_id": "cid", "client_secret": "cs",
                                  "access_token": "old", "refresh_token": "rt",
                                  "expires_at": time.time() - 10})
    net.add({"access_token": "new-access", "expires_in": 3600, "scope": "a b"})
    src = auth.load_token_source()
    assert src.token() == "new-access"
    call = net.calls[0]
    assert call.method == "POST" and call.path == auth.TOKEN_URI
    assert call.form()["grant_type"] == "refresh_token" and call.form()["refresh_token"] == "rt"
    stored = auth.load_profile("default")
    assert stored["access_token"] == "new-access" and stored["scopes_granted"] == ["a", "b"]
    assert src.token() == "new-access" and len(net.calls) == 1  # still valid: no 2nd call


def test_oauth_without_refresh_token_errors():
    auth.save_profile("default", {"mode": "oauth", "expires_at": 0})
    with pytest.raises(AuthError, match="no refresh token"):
        auth.load_token_source().token()


def test_token_endpoint_error_carries_description_not_secret(net):
    net.add({"error": "invalid_grant", "error_description": "Token has been revoked."}, 400)
    with pytest.raises(AuthError, match="revoked") as ei:
        auth._post_form(auth.TOKEN_URI, {"refresh_token": "SECRET-RT"})
    assert "SECRET-RT" not in str(ei.value)


def _browser(code="the-code", state=None):
    """An opener that plays the browser: hit the loopback redirect."""
    def opener(url: str) -> None:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        redir = urllib.parse.urlparse(q["redirect_uri"][0])
        st = state if state is not None else q["state"][0]
        opener.challenge = q["code_challenge"][0]
        conn = http.client.HTTPConnection(redir.hostname, redir.port, timeout=5)
        conn.request("GET", "/?" + urllib.parse.urlencode({"code": code, "state": st}))
        conn.getresponse().read()
        conn.close()
    return opener


def test_loopback_flow_exchanges_code_with_verifier(net):
    net.add({"access_token": "at", "refresh_token": "rt", "expires_in": 3600})
    op = _browser()
    tok = auth.run_loopback_flow({"client_id": "cid", "client_secret": "cs"}, ["s"],
                                 opener=op, printer=lambda s: None, timeout=10)
    assert tok["access_token"] == "at"
    form = net.calls[0].form()
    assert form["code"] == "the-code" and form["grant_type"] == "authorization_code"
    assert form["redirect_uri"].startswith("http://127.0.0.1:")
    assert auth.pkce_challenge(form["code_verifier"]) == op.challenge


def test_loopback_state_mismatch_refused(net):
    with pytest.raises(AuthError, match="state mismatch"):
        auth.run_loopback_flow({"client_id": "cid"}, ["s"], opener=_browser(state="forged"),
                               printer=lambda s: None, timeout=10)
    assert net.calls == []  # the code was never exchanged


def test_login_oauth_writes_profile_then_extends(net, monkeypatch):
    monkeypatch.setenv("AWSUITE_GOOGLE_CLIENT_ID", "cid")
    net.add({"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600})
    st = auth.login("default", services=["mail"], printer=lambda s: None,
                    opener=_browser(), timeout=10)
    assert st["scopes_requested"] == ["gmail.readonly"]
    assert "at1" not in json.dumps(st)
    net.add({"access_token": "at2", "expires_in": 3600})
    st = auth.login("default", services=["drive"], write=True, printer=lambda s: None,
                    opener=_browser(), timeout=10)
    assert st["scopes_requested"] == ["gmail.readonly", "drive.readonly", "drive.file"]
    data = auth.load_profile("default")
    assert data["refresh_token"] == "rt1"  # kept across the incremental login


def test_logout_revokes_and_deletes(net):
    auth.save_profile("default", {"mode": "oauth", "refresh_token": "rt"})
    net.add({})
    out = auth.logout()
    assert out == {"profile": "default", "removed": True, "revoked": "yes"}
    assert net.calls[0].path == auth.REVOKE_URI and auth.load_profile("default") is None


def test_service_account_missing_extra(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "cryptography", None)
    with pytest.raises(ConfigError, match=r"awsuite\[sa\]"):
        auth.sign_rs256("pem", b"x")


def test_service_account_jwt_and_exchange(net, monkeypatch, tmp_path):
    key = tmp_path / "k.json"
    key.write_text(json.dumps({"client_email": "sa@p.iam.gserviceaccount.com",
                               "private_key": "PEM", "private_key_id": "kid1"}))
    monkeypatch.setattr(auth, "sign_rs256", lambda pem, data: b"signature")
    src = auth.ServiceAccountTokenSource(str(key), "boss@example.com", ["s1", "s2"],
                                         now=lambda: 1000.0)
    net.add({"access_token": "sa-at", "expires_in": 3600})
    assert src.token() == "sa-at"
    form = net.calls[0].form()
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    h, c, s = form["assertion"].split(".")
    pad = lambda x: x + "=" * (-len(x) % 4)  # noqa: E731
    header = json.loads(base64.urlsafe_b64decode(pad(h)))
    claims = json.loads(base64.urlsafe_b64decode(pad(c)))
    assert header == {"alg": "RS256", "typ": "JWT", "kid": "kid1"}
    assert claims["sub"] == "boss@example.com" and claims["scope"] == "s1 s2"
    assert claims["exp"] - claims["iat"] == 3600 and claims["aud"] == auth.TOKEN_URI
    assert base64.urlsafe_b64decode(pad(s)) == b"signature"


def test_load_client_sources(tmp_path, monkeypatch):
    f = tmp_path / "cs.json"
    f.write_text(json.dumps({"installed": {"client_id": "ID", "client_secret": "SEC"}}))
    assert auth.load_client(str(f)) == {"client_id": "ID", "client_secret": "SEC"}
    with pytest.raises(ConfigError, match="AWSUITE_GOOGLE_CLIENT_ID"):
        auth.load_client(None)
    monkeypatch.setenv("AWSUITE_GOOGLE_CLIENT_ID", "E")
    assert auth.load_client(None)["client_id"] == "E"


def test_scope_mapping():
    assert scopes.parse_services("gmail, cal") == ["mail", "calendar"]
    ro = scopes.scopes_for(["mail", "directory"])
    assert ro == [scopes.full("gmail.readonly"), scopes.full("admin.directory.user.readonly")]
    rw = scopes.scopes_for(["mail"], write=True)
    assert scopes.full("gmail.send") in rw and scopes.full("gmail.compose") in rw
    with pytest.raises(ValueError, match="unknown service"):
        scopes.parse_services("mail,photos")
    assert scopes.satisfied("mail.read", None)
    assert not scopes.satisfied("mail.send", [scopes.full("gmail.readonly")])
