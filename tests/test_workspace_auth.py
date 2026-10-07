"""`workspace` mode: borrow the Google connection made in an Aither workspace."""

from __future__ import annotations

import json

import pytest

from awsuite import auth, cli
from awsuite.errors import AuthError, ConfigError

BASE = "https://workspace.example.com"
BEARER = "ws-session-bearer-" + "q" * 40
GTOKEN = "ya29.workspace-handed-" + "z" * 40


@pytest.fixture
def bearer(monkeypatch):
    monkeypatch.setenv("WS_TOKEN", BEARER)
    return "WS_TOKEN"


def _src(now=lambda: 1000.0):
    return auth.WorkspaceTokenSource(BASE, "WS_TOKEN", "google", now=now)


def test_200_fetches_with_bearer_and_caches_until_expiry(net, bearer):
    clock = [1000.0]
    src = _src(now=lambda: clock[0])
    net.add({"provider": "google", "access_token": GTOKEN, "expires_at": 1000.0 + 600,
             "scopes": ["openid", "https://www.googleapis.com/auth/drive.readonly"]})
    assert src.token() == GTOKEN
    call = net.calls[0]
    assert call.method == "GET" and call.path == BASE + "/api/connectors/google/token"
    assert call.headers["authorization"] == "Bearer " + BEARER
    assert src.granted_scopes() == {"openid", "https://www.googleapis.com/auth/drive.readonly"}
    clock[0] = 1000.0 + 600 - auth.EXPIRY_SKEW - 1
    assert src.token() == GTOKEN and len(net.calls) == 1  # still cached
    clock[0] = 1000.0 + 600 - auth.EXPIRY_SKEW
    net.add({"access_token": "second", "expires_at": None})
    assert src.token() == "second" and len(net.calls) == 2  # refetched at expiry - 60 s


def test_force_refresh_refetches(net, bearer):
    src = _src()
    net.add({"access_token": "one", "expires_at": 99999.0})
    net.add({"access_token": "two", "expires_at": 99999.0})
    assert src.token() == "one"
    assert src.force_refresh() is True and src.token() == "two"


def test_401_is_auth_error_naming_the_variable(net, bearer):
    net.add({"detail": "Sign in first."}, 401)
    with pytest.raises(AuthError, match=r"\$WS_TOKEN \(401\)") as ei:
        _src().token()
    assert BEARER not in str(ei.value)


def test_403_carries_detail_and_names_the_admin_switch(net, bearer):
    net.add({"detail": "token hand-off is disabled for this workspace; an admin can "
                       "enable it in Connectors"}, 403)
    with pytest.raises(AuthError) as ei:
        _src().token()
    msg = str(ei.value)
    assert "hand-off is disabled" in msg and "admin must enable CLI hand-off" in msg
    assert ei.value.status == 403 and type(ei.value) is AuthError


def test_404_points_at_the_connect_route(net, bearer):
    net.add({"detail": "not connected", "connect_url": "/api/auth/google/login"}, 404)
    with pytest.raises(AuthError, match="not connected") as ei:
        _src().token()
    assert f"{BASE}/api/auth/google/login" in str(ei.value)


def test_404_ignores_an_absolute_connect_url(net, bearer):
    net.add({"detail": "not connected", "connect_url": "https://evil.example/x"}, 404)
    with pytest.raises(AuthError) as ei:
        _src().token()
    assert "evil.example" not in str(ei.value)
    assert f"{BASE}/api/auth/google/login" in str(ei.value)


def test_unreachable_and_empty_bearer(net, monkeypatch):
    with pytest.raises(AuthError, match="empty or unset"):
        _src().token()
    assert net.calls == []
    monkeypatch.setenv("WS_TOKEN", BEARER)
    net.add(None, -1)
    with pytest.raises(AuthError, match="unreachable"):
        _src().token()


def test_https_required_except_loopback():
    with pytest.raises(ConfigError, match="plain http"):
        auth.WorkspaceTokenSource("http://workspace.example.com", "WS_TOKEN")
    for ok in ("http://127.0.0.1:8900", "http://localhost:3000/", BASE + "/"):
        assert auth.WorkspaceTokenSource(ok, "WS_TOKEN").base == ok.rstrip("/")
    with pytest.raises(ConfigError):
        auth.check_workspace_base("workspace.example.com")


def test_login_stores_only_the_variable_name_and_status_masks(bearer, capsys, no_net):
    rc = cli.main(["auth", "login", "--workspace", BASE, "--bearer-env", "WS_TOKEN", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    stored = auth.load_profile("default")
    assert stored == {"mode": "workspace", "base": BASE, "bearer_env": "WS_TOKEN",
                      "provider": "google"}
    assert BEARER not in json.dumps(stored) and BEARER not in out
    st = json.loads(out)
    assert st["mode"] == "workspace" and st["workspace"] == BASE and st["bearer_env"] == "WS_TOKEN"
    assert cli.main(["auth", "status"]) == 0
    assert BEARER not in capsys.readouterr().out
    src = auth.load_token_source()
    assert isinstance(src, auth.WorkspaceTokenSource)
    assert BEARER not in json.dumps(src.describe())


def test_login_refuses_bad_inputs(bearer):
    with pytest.raises(ConfigError, match="bearer-env"):
        auth.login("default", "workspace", workspace=BASE, bearer_env="1-not a name")
    with pytest.raises(ConfigError, match="plain http"):
        auth.login("default", "workspace", workspace="http://ws.example.com",
                   bearer_env="WS_TOKEN")
    with pytest.raises(ConfigError, match="provider"):
        auth.login("default", "workspace", workspace=BASE, bearer_env="WS_TOKEN",
                   provider="m365")
    assert auth.load_profile("default") is None


def test_token_value_never_reaches_cli_errors(net, bearer, capsys):
    auth.save_profile("default", {"mode": "workspace", "base": BASE, "bearer_env": "WS_TOKEN",
                                  "provider": "google"})
    net.add({"detail": "token hand-off is disabled for this workspace"}, 403)
    rc = cli.main(["mail", "search", "is:unread"])
    captured = capsys.readouterr()
    assert rc == 2
    assert BEARER not in captured.out + captured.err
    assert "admin" in captured.err


def _adk_auth(tmp_path, monkeypatch, active, token):
    import json as _json
    f = tmp_path / "auth.json"
    f.write_text(_json.dumps({"version": 1, "active_profile": active,
                              "profiles": {active: {"access_token": token}}}))
    monkeypatch.setenv("AWSUITE_ADK_AUTH_FILE", str(f))


def test_no_bearer_env_uses_the_adk_login(net, tmp_path, monkeypatch, capsys):
    _adk_auth(tmp_path, monkeypatch, "cloud", "adk-identity-token")
    auth.login("default", "workspace", workspace=BASE)
    assert auth.load_profile("default")["bearer_env"] == ""
    net.add({"access_token": "ya29.from-adk", "expires_at": None, "scopes": []}, 200)
    src = auth.WorkspaceTokenSource(BASE, "", "google", now=lambda: 1000.0)
    assert src.token() == "ya29.from-adk"
    assert net.calls[-1].headers["authorization"] == "Bearer adk-identity-token"
    st = src.describe()
    assert st["bearer_source"].startswith("adk login") and st["bearer_set"] is True
    assert "adk-identity-token" not in repr(st)


def test_local_root_profile_is_not_an_aither_login(tmp_path, monkeypatch):
    _adk_auth(tmp_path, monkeypatch, "local", "root-token")
    assert auth.adk_login_token() == ""
    auth.login("default", "workspace", workspace=BASE)
    with pytest.raises(AuthError, match="adk login"):
        auth.WorkspaceTokenSource(BASE, "", "google").token()
