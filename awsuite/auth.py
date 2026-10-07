"""Authentication: four modes, one `TokenSource` interface.

* ``oauth`` (default) -- the installed-app loopback flow with PKCE (RFC 7636,
  S256). A tiny `http.server` listens on 127.0.0.1 at an ephemeral port, the
  browser is opened at Google's consent page, and the code that comes back is
  exchanged together with the PKCE verifier. The refresh token is stored in the
  profile file, written 0600 where the OS honours it.
* ``service-account`` -- a JSON key plus ``--subject`` for domain-wide
  delegation. The RS256 JWT is signed with ``cryptography``, imported lazily;
  without it this mode refuses with an error naming the extra.
* ``token`` -- an access token handed in by a platform: ``AWSUITE_ACCESS_TOKEN``
  or ``--token-cmd`` (a command that prints one). The brick then never holds a
  refresh credential at all, which is the point of the mode.
* ``workspace`` -- borrow the Google connection you already made in an Aither
  workspace. Each fetch asks ``GET <base>/api/connectors/<provider>/token`` with
  your workspace session bearer (read from an environment variable you NAME, so
  only the variable's name is stored) and caches the answer in memory until just
  before it expires. No second OAuth client, no refresh token on this machine.

Token values are never printed. `mask()` is the only way a token reaches output.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import shlex
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from . import scopes as _scopes
from .errors import AuthError, ConfigError

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"

MODES = ("oauth", "service-account", "token", "workspace")

#: Connector providers a workspace can hand a token for (awsuite speaks Google).
WORKSPACE_PROVIDERS = ("google",)
WORKSPACE_TOKEN_PATH = "/api/connectors/{provider}/token"

#: Refresh this many seconds before the recorded expiry, so a token never dies
#: mid-request.
EXPIRY_SKEW = 60


# --------------------------------------------------------------------------- paths


def home() -> Path:
    """The profile directory: ``$AWSUITE_HOME`` or ``~/.aither/awsuite``."""
    env = os.environ.get("AWSUITE_HOME")
    return Path(env).expanduser() if env else Path.home() / ".aither" / "awsuite"


def default_profile() -> str:
    return os.environ.get("AWSUITE_PROFILE") or "default"


def profile_path(profile: str) -> Path:
    if not profile or any(c in profile for c in "/\\:") or profile.startswith("."):
        raise ConfigError(f"invalid profile name {profile!r}")
    return home() / f"{profile}.json"


# --------------------------------------------------------------------------- masking


def mask(value: "str | None") -> str:
    """A display form of a secret that cannot be replayed.

    Shows at most a 4-character prefix (enough to tell `ya29` from a JWT), the
    length and a short SHA-256 fingerprint, so two masked values can be compared
    without either being revealed.
    """
    if not value:
        return "(none)"
    fp = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    if len(value) < 16:
        return f"****[len={len(value)} fp={fp}]"
    return f"{value[:4]}...[len={len(value)} fp={fp}]"


_SECRET_KEYS = ("access_token", "refresh_token", "client_secret", "id_token", "private_key")


def masked(data: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of a profile/token dict with every secret field masked."""
    out: Dict[str, Any] = {}
    for k, v in data.items():
        out[k] = mask(v) if k in _SECRET_KEYS and isinstance(v, str) else v
    return out


# --------------------------------------------------------------------------- PKCE


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce_verifier() -> str:
    """A fresh 43-character code verifier (32 random bytes, base64url, no pad)."""
    return _b64url(secrets.token_bytes(32))


def pkce_challenge(verifier: str) -> str:
    """S256 challenge: BASE64URL(SHA256(ASCII(verifier))) without padding."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


# --------------------------------------------------------------------------- store


def load_profile(profile: str) -> Optional[Dict[str, Any]]:
    """The stored profile dict, or None when there is no file."""
    p = profile_path(profile)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"profile {profile!r} is unreadable ({exc}); "
                          f"`awsuite auth logout --profile {profile}` and log in again") from exc


def save_profile(profile: str, data: Dict[str, Any]) -> Path:
    """Atomically write the profile with mode 0600 (where the OS honours modes)."""
    p = profile_path(profile)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        # Windows and some mounts ignore POSIX modes; doctor reports perms honestly.
        _ = None
    fd, tmp = tempfile.mkstemp(prefix=".awsuite-", dir=str(p.parent))
    try:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            _ = None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            _ = None
        raise
    try:
        os.chmod(p, 0o600)
    except OSError:
        _ = None
    return p


def delete_profile(profile: str) -> bool:
    p = profile_path(profile)
    if p.exists():
        p.unlink()
        return True
    return False


def list_profiles() -> List[str]:
    d = home()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json") if not p.name.startswith("."))


# --------------------------------------------------------------------------- HTTP


def _post_form(url: str, fields: Dict[str, str], timeout: float = 30.0) -> Dict[str, Any]:
    """POST an x-www-form-urlencoded body to a token endpoint; JSON answer.

    Raises:
        AuthError: on any non-2xx, carrying Google's `error_description` (never
            the request body, which holds the secret).
    """
    body = urllib.parse.urlencode(fields).encode("ascii")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            j = json.loads(exc.read() or b"{}")
            detail = j.get("error_description") or j.get("error") or ""
        except (ValueError, AttributeError, OSError):
            detail = ""
        raise AuthError(f"token endpoint refused ({exc.code}): {detail or exc.reason}",
                        exc.code) from exc
    except urllib.error.URLError as exc:
        raise AuthError(f"token endpoint unreachable: {exc.reason}") from exc
    try:
        return json.loads(raw or b"{}")
    except ValueError as exc:
        raise AuthError("token endpoint answered non-JSON") from exc


# --------------------------------------------------------------------------- client


def load_client(client_secret_path: "str | None" = None) -> Dict[str, str]:
    """Resolve the OAuth client: a Google `client_secret.json`, else env vars.

    Raises:
        ConfigError: when neither source provides a client id.
    """
    if client_secret_path:
        try:
            data = json.loads(Path(client_secret_path).expanduser().read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"cannot read client secret file: {exc}") from exc
        block = data.get("installed") or data.get("web") or data
        cid, csec = block.get("client_id"), block.get("client_secret", "")
        if not cid:
            raise ConfigError("client secret file has no client_id "
                              "(expected an 'installed' OAuth client)")
        return {"client_id": cid, "client_secret": csec}
    cid = os.environ.get("AWSUITE_GOOGLE_CLIENT_ID")
    if not cid:
        raise ConfigError("no OAuth client: pass --client-secret <client_secret.json> "
                          "or set AWSUITE_GOOGLE_CLIENT_ID / AWSUITE_GOOGLE_CLIENT_SECRET")
    return {"client_id": cid, "client_secret": os.environ.get("AWSUITE_GOOGLE_CLIENT_SECRET", "")}


# --------------------------------------------------------------------------- oauth flow


def build_auth_url(client_id: str, redirect_uri: str, scope_list: List[str], state: str,
                   challenge: str, login_hint: str = "") -> str:
    """The consent URL. `include_granted_scopes` makes scopes incremental."""
    q = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scope_list),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if login_hint:
        q["login_hint"] = login_hint
    return AUTH_URI + "?" + urllib.parse.urlencode(q)


def parse_callback(path: str) -> Dict[str, str]:
    """Query parameters of the loopback redirect (`/?code=...&state=...`)."""
    qs = urllib.parse.urlparse(path).query
    return {k: v[0] for k, v in urllib.parse.parse_qs(qs).items()}


def exchange_code(client: Dict[str, str], code: str, verifier: str,
                  redirect_uri: str) -> Dict[str, Any]:
    return _post_form(TOKEN_URI, {
        "code": code,
        "client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    })


_DONE_PAGE = (b"<!doctype html><meta charset=utf-8><title>awsuite</title>"
              b"<body style='font-family:system-ui;padding:2rem'>"
              b"<h2>awsuite: signed in.</h2><p>You can close this tab.</p>")


def run_loopback_flow(client: Dict[str, str], scope_list: List[str], *,
                      open_browser: bool = True, timeout: float = 300.0,
                      printer: Callable[[str], None] = print,
                      opener: Optional[Callable[[str], Any]] = None,
                      login_hint: str = "") -> Dict[str, Any]:
    """Run the installed-app loopback flow and return the token response.

    Args:
        client: `{"client_id", "client_secret"}`.
        scope_list: full scope URLs.
        open_browser: open the consent URL in the default browser.
        timeout: seconds to wait for the redirect.
        printer: where the consent URL is printed (always printed).
        opener: override for the browser opener (tests drive the redirect).

    Raises:
        AuthError: state mismatch, consent denied, timeout, or exchange failure.
    """
    result: Dict[str, str] = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            params = parse_callback(self.path)
            if "code" in params or "error" in params:
                result.update(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_DONE_PAGE)

        def log_message(self, *args: Any) -> None:
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    server.timeout = 1.0
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/"
    verifier = pkce_verifier()
    state = secrets.token_urlsafe(16)
    url = build_auth_url(client["client_id"], redirect_uri, scope_list, state,
                         pkce_challenge(verifier), login_hint=login_hint)
    printer("Open this URL to grant access (it is also opened for you):\n  " + url)
    if opener is not None:
        threading.Thread(target=opener, args=(url,), daemon=True).start()
    elif open_browser:
        try:
            webbrowser.open(url, new=1)
        except webbrowser.Error:
            printer("(could not open a browser; open the URL above by hand)")
    deadline = time.monotonic() + timeout
    try:
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not result:
        raise AuthError(f"no redirect within {int(timeout)} s; run login again")
    if result.get("state") != state:
        raise AuthError("state mismatch on the OAuth redirect; refusing the code")
    if "error" in result:
        raise AuthError(f"consent was not granted: {result['error']}")
    return exchange_code(client, result["code"], verifier, redirect_uri)


# --------------------------------------------------------------------------- sources


class TokenSource:
    """Hands out a bearer for API calls. Subclasses implement the modes."""

    mode = "base"

    def token(self) -> str:
        raise NotImplementedError

    def force_refresh(self) -> bool:
        """Drop the cached token and get a new one. False when that is impossible."""
        return False

    def granted_scopes(self) -> Optional[Set[str]]:
        """Scopes known to be granted, or None when unknown (platform tokens)."""
        return None

    def describe(self) -> Dict[str, Any]:
        return {"mode": self.mode}


class OAuthTokenSource(TokenSource):
    mode = "oauth"

    def __init__(self, profile: str, data: Dict[str, Any]) -> None:
        self.profile = profile
        self.data = data

    def _expired(self) -> bool:
        return float(self.data.get("expires_at") or 0) - EXPIRY_SKEW <= time.time()

    def _refresh(self) -> None:
        rt = self.data.get("refresh_token")
        if not rt:
            raise AuthError(f"profile {self.profile!r} has no refresh token; "
                            f"run `awsuite auth login --profile {self.profile}`")
        tok = _post_form(TOKEN_URI, {
            "client_id": self.data.get("client_id", ""),
            "client_secret": self.data.get("client_secret", ""),
            "refresh_token": rt,
            "grant_type": "refresh_token",
        })
        apply_token_response(self.data, tok)
        save_profile(self.profile, self.data)

    def token(self) -> str:
        if not self.data.get("access_token") or self._expired():
            self._refresh()
        return str(self.data["access_token"])

    def force_refresh(self) -> bool:
        if not self.data.get("refresh_token"):
            return False
        self._refresh()
        return True

    def granted_scopes(self) -> Optional[Set[str]]:
        g = self.data.get("scopes_granted")
        return set(g) if isinstance(g, list) else None

    def describe(self) -> Dict[str, Any]:
        return {"mode": self.mode, "profile": self.profile,
                "expires_at": self.data.get("expires_at"),
                "has_refresh_token": bool(self.data.get("refresh_token")),
                "access_token": mask(self.data.get("access_token"))}


class ServiceAccountTokenSource(TokenSource):
    mode = "service-account"

    def __init__(self, key_path: str, subject: str, scope_list: List[str],
                 now: Callable[[], float] = time.time) -> None:
        self.key_path = key_path
        self.subject = subject
        self.scope_list = scope_list
        self._now = now
        self._tok: Optional[str] = None
        self._exp = 0.0

    def _key(self) -> Dict[str, Any]:
        try:
            return json.loads(Path(self.key_path).expanduser().read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"cannot read service-account key: {exc}") from exc

    def assertion(self) -> str:
        """The signed RS256 JWT assertion (needs the `sa` extra)."""
        key = self._key()
        iat = int(self._now())
        claims = {"iss": key.get("client_email"), "scope": " ".join(self.scope_list),
                  "aud": key.get("token_uri") or TOKEN_URI, "iat": iat, "exp": iat + 3600}
        if self.subject:
            claims["sub"] = self.subject
        header = {"alg": "RS256", "typ": "JWT"}
        if key.get("private_key_id"):
            header["kid"] = key["private_key_id"]
        signing_input = (_b64url(json.dumps(header, separators=(",", ":")).encode())
                         + "." + _b64url(json.dumps(claims, separators=(",", ":")).encode()))
        sig = sign_rs256(key.get("private_key", ""), signing_input.encode("ascii"))
        return signing_input + "." + _b64url(sig)

    def token(self) -> str:
        if self._tok and self._exp - EXPIRY_SKEW > self._now():
            return self._tok
        key = self._key()
        tok = _post_form(key.get("token_uri") or TOKEN_URI, {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": self.assertion(),
        })
        if not tok.get("access_token"):
            raise AuthError("service-account exchange returned no access_token")
        self._tok = tok["access_token"]
        self._exp = self._now() + float(tok.get("expires_in") or 3600)
        return self._tok

    def force_refresh(self) -> bool:
        self._tok = None
        return True

    def granted_scopes(self) -> Optional[Set[str]]:
        return set(self.scope_list)

    def describe(self) -> Dict[str, Any]:
        return {"mode": self.mode, "key": self.key_path, "subject": self.subject}


def sign_rs256(pem: str, data: bytes) -> bytes:
    """RSASSA-PKCS1-v1_5 SHA-256 signature. Imports `cryptography` lazily.

    Raises:
        ConfigError: when the optional extra is not installed.
    """
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as exc:
        raise ConfigError("service-account mode needs the optional extra: "
                          "pip install 'awsuite[sa]'  (it adds `cryptography`)") from exc
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    return key.sign(data, padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr]


class CommandTokenSource(TokenSource):
    """`token` mode: a platform hands the agent a short-lived access token.

    Either a fixed token (`AWSUITE_ACCESS_TOKEN`) or a command whose stdout is
    the token, or a JSON object `{"access_token": ..., "expires_in": ...}`.
    """

    mode = "token"

    def __init__(self, token: str = "", command: str = "",
                 now: Callable[[], float] = time.time, ttl: float = 300.0) -> None:
        self._static = token
        self.command = command
        self._now = now
        self._ttl = ttl
        self._tok: Optional[str] = None
        self._exp = 0.0

    def _run(self) -> None:
        argv = self.command if os.name == "nt" else shlex.split(self.command)
        try:
            r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                               timeout=30, check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AuthError(f"--token-cmd failed to run: {exc}") from exc
        if r.returncode != 0:
            raise AuthError(f"--token-cmd exited {r.returncode}: "
                            f"{(r.stderr or '').strip()[:200]}")
        out = (r.stdout or "").strip()
        exp = self._now() + self._ttl
        if out.startswith("{"):
            try:
                j = json.loads(out)
            except ValueError as exc:
                raise AuthError("--token-cmd printed malformed JSON") from exc
            out = str(j.get("access_token") or "")
            if j.get("expires_in"):
                exp = self._now() + float(j["expires_in"])
        if not out:
            raise AuthError("--token-cmd printed no token")
        self._tok, self._exp = out, exp

    def token(self) -> str:
        if self.command:
            if not self._tok or self._exp - EXPIRY_SKEW / 2 <= self._now():
                self._run()
            return str(self._tok)
        if not self._static:
            raise AuthError("token mode: set AWSUITE_ACCESS_TOKEN or pass --token-cmd")
        return self._static

    def force_refresh(self) -> bool:
        if not self.command:
            return False
        self._tok = None
        return True

    def describe(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"mode": self.mode}
        if self.command:
            d["token_cmd"] = self.command
        else:
            d["access_token"] = mask(self._static)
        return d


_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def check_workspace_base(base: str) -> str:
    """Normalise and vet a workspace base URL: https, or http only to loopback.

    Raises:
        ConfigError: not a URL, or plain http to a non-loopback host.
    """
    u = urllib.parse.urlparse((base or "").strip())
    if u.scheme not in ("https", "http") or not u.netloc:
        raise ConfigError(f"--workspace must be a URL like https://workspace.example.com, "
                          f"got {base!r}")
    if u.scheme == "http" and u.hostname not in _LOOPBACK:
        raise ConfigError("refusing to send a workspace session over plain http to a "
                          "non-loopback host; use https")
    return base.strip().rstrip("/")


def adk_login_token() -> str:
    """The Aither Identity token `adk login` saved (~/.aither/auth.json, active profile).

    Read-only, stdlib-only, and never copied anywhere: awsuite only sends it to the
    workspace you named. The local-root profile `adk` provisions on a fresh install is
    not an Identity login, so it does not count. ``AWSUITE_ADK_AUTH_FILE`` overrides the
    path (tests, unusual homes).
    """
    path = Path(os.environ.get("AWSUITE_ADK_AUTH_FILE")
                or (Path.home() / ".aither" / "auth.json"))
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(store, dict):
        return ""
    active = store.get("active_profile") or ""
    prof = (store.get("profiles") or {}).get(active) or {}
    if active in ("", "local") or not isinstance(prof, dict):
        return ""
    return str(prof.get("access_token") or "").strip()


def _env_name_ok(name: str) -> bool:
    return bool(name) and all(c.isalnum() or c == "_" for c in name) and not name[0].isdigit()


class WorkspaceTokenSource(TokenSource):
    """`workspace` mode: borrow the caller's own connection from an Aither workspace.

    The workspace answers only for the signed-in member (never anyone else), and
    only when a workspace admin has turned the CLI hand-off on. The bearer is
    read from the NAMED environment variable on every fetch; neither it nor the
    access token is ever written to disk or printed.
    """

    mode = "workspace"

    def __init__(self, base: str, bearer_env: str, provider: str = "google",
                 now: Callable[[], float] = time.time, ttl: float = 300.0) -> None:
        self.base = check_workspace_base(base)
        self.bearer_env = bearer_env
        self.provider = provider
        self._now = now
        self._ttl = ttl
        self._tok: Optional[str] = None
        self._exp = 0.0
        self._scopes: Optional[Set[str]] = None

    @property
    def url(self) -> str:
        return self.base + WORKSPACE_TOKEN_PATH.format(provider=self.provider)

    def _bearer(self) -> str:
        if not self.bearer_env:
            # No variable named: use the Aither login `adk login` already holds.
            val = adk_login_token()
            if not val:
                raise AuthError("workspace mode: no Aither login found -- run `adk login` "
                                "(or pass --bearer-env NAME)")
            return val
        val = os.environ.get(self.bearer_env, "").strip()
        if val.lower().startswith("bearer "):
            val = val[7:].strip()
        if not val:
            raise AuthError(f"workspace mode: environment variable {self.bearer_env} "
                            f"is empty or unset; export your workspace session bearer in it")
        return val

    def _refused(self, code: int, body: bytes) -> AuthError:
        try:
            j = json.loads(body or b"{}")
        except ValueError:
            j = {}
        if not isinstance(j, dict):
            j = {}
        detail = str(j.get("detail") or "")[:300]
        if code == 401:
            return AuthError(f"the workspace refused the bearer in ${self.bearer_env} (401): "
                             f"sign in to the workspace again and refresh it", 401)
        if code == 403:
            return AuthError(f"the workspace refused the token hand-off (403): "
                             f"{detail or 'forbidden'}. A workspace admin must enable CLI "
                             f"hand-off for this connection (Connectors)", 403)
        if code == 404:
            connect = str(j.get("connect_url") or f"/api/auth/{self.provider}/login")
            if not connect.startswith("/"):
                connect = f"/api/auth/{self.provider}/login"
            return AuthError(f"not connected -- connect Google in the workspace first: "
                             f"{self.base}{connect}", 404)
        return AuthError(f"the workspace token endpoint answered {code}: "
                         f"{detail or 'no detail'}", code)

    def _fetch(self) -> None:
        req = urllib.request.Request(
            self.url, method="GET",
            headers={"Authorization": "Bearer " + self._bearer(),
                     "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read() or b""
            except (OSError, AttributeError):
                body = b""
            raise self._refused(exc.code, body) from None
        except urllib.error.URLError as exc:
            raise AuthError(f"workspace {self.base} unreachable: {exc.reason}") from None
        try:
            j = json.loads(raw or b"{}")
        except ValueError:
            raise AuthError("the workspace token endpoint answered non-JSON") from None
        tok = str((j.get("access_token") if isinstance(j, dict) else "") or "")
        if not tok:
            raise AuthError("the workspace token endpoint returned no access_token")
        exp = j.get("expires_at")
        try:
            self._exp = float(exp) if exp is not None else self._now() + self._ttl
        except (TypeError, ValueError):
            self._exp = self._now() + self._ttl
        self._tok = tok
        sc = j.get("scopes")
        self._scopes = {str(s) for s in sc} if isinstance(sc, list) else None

    def token(self) -> str:
        if not self._tok or self._exp - EXPIRY_SKEW <= self._now():
            self._fetch()
        return str(self._tok)

    def force_refresh(self) -> bool:
        self._tok = None
        self._exp = 0.0
        return True

    def granted_scopes(self) -> Optional[Set[str]]:
        return set(self._scopes) if self._scopes is not None else None

    def describe(self) -> Dict[str, Any]:
        return {"mode": self.mode, "workspace": self.base, "provider": self.provider,
                "bearer_env": self.bearer_env or None,
                "bearer_source": (f"env:{self.bearer_env}" if self.bearer_env
                                  else "adk login (~/.aither/auth.json)"),
                "bearer_set": bool(os.environ.get(self.bearer_env, "").strip()
                                   if self.bearer_env else adk_login_token()),
                "access_token": mask(self._tok)}


# --------------------------------------------------------------------------- glue


def apply_token_response(data: Dict[str, Any], tok: Dict[str, Any]) -> None:
    """Fold a token-endpoint answer into a profile dict (in place)."""
    if not tok.get("access_token"):
        raise AuthError("token endpoint returned no access_token")
    data["access_token"] = tok["access_token"]
    data["expires_at"] = int(time.time() + float(tok.get("expires_in") or 3600))
    if tok.get("refresh_token"):
        data["refresh_token"] = tok["refresh_token"]
    if tok.get("scope"):
        data["scopes_granted"] = sorted(set(str(tok["scope"]).split()))


def load_token_source(profile: "str | None" = None) -> TokenSource:
    """Build the TokenSource for a profile.

    With no profile file, a set `AWSUITE_ACCESS_TOKEN` selects token mode, so a
    platform can run the brick without any local state at all.

    Raises:
        ConfigError: no profile and no platform token.
    """
    name = profile or default_profile()
    data = load_profile(name)
    if data is None:
        env_tok = os.environ.get("AWSUITE_ACCESS_TOKEN", "")
        env_cmd = os.environ.get("AWSUITE_TOKEN_CMD", "")
        if env_tok or env_cmd:
            return CommandTokenSource(token=env_tok, command=env_cmd)
        raise ConfigError(f"no awsuite profile {name!r}: run `awsuite auth login` "
                          f"(or set AWSUITE_ACCESS_TOKEN for a platform-supplied token)")
    mode = data.get("mode", "oauth")
    if mode == "oauth":
        return OAuthTokenSource(name, data)
    if mode == "service-account":
        return ServiceAccountTokenSource(data.get("key_path", ""), data.get("subject", ""),
                                         list(data.get("scopes_requested") or []))
    if mode == "token":
        return CommandTokenSource(token=os.environ.get("AWSUITE_ACCESS_TOKEN", ""),
                                  command=data.get("token_cmd", ""))
    if mode == "workspace":
        return WorkspaceTokenSource(data.get("base", ""), data.get("bearer_env", ""),
                                    data.get("provider") or "google")
    raise ConfigError(f"profile {name!r} has unknown mode {mode!r}")


def login(profile: str, mode: str = "oauth", *, services: "List[str] | None" = None,
          write: bool = False, client_secret: "str | None" = None,
          open_browser: bool = True, key_path: str = "", subject: str = "",
          token_cmd: str = "", printer: Callable[[str], None] = print,
          opener: Optional[Callable[[str], Any]] = None,
          timeout: float = 300.0, workspace: str = "", bearer_env: str = "",
          provider: str = "google") -> Dict[str, Any]:
    """Create or extend a profile. Returns the masked status.

    oauth logins are incremental: scopes already granted to the profile are kept
    and the new ones are added. workspace logins store only the workspace URL,
    the NAME of the bearer variable and the provider -- never a credential.
    """
    if mode not in MODES:
        raise ConfigError(f"unknown mode {mode!r}; choose from {', '.join(MODES)}")
    if mode == "workspace":
        base = check_workspace_base(workspace)
        if bearer_env and not _env_name_ok(bearer_env):
            raise ConfigError(f"--bearer-env must be an environment variable NAME, "
                              f"got {bearer_env!r}")
        if provider not in WORKSPACE_PROVIDERS:
            raise ConfigError(f"workspace mode supports --provider "
                              f"{', '.join(WORKSPACE_PROVIDERS)}, got {provider!r}")
        save_profile(profile, {"mode": "workspace", "base": base, "bearer_env": bearer_env,
                               "provider": provider})
        return status(profile)
    svcs = services if services is not None else _scopes.parse_services(None)
    wanted = _scopes.scopes_for(svcs, write=write)
    existing = load_profile(profile) or {}
    data: Dict[str, Any] = {"provider": "google", "mode": mode}

    if mode == "oauth":
        if client_secret or not existing.get("client_id"):
            client = load_client(client_secret)
        else:
            client = {"client_id": existing["client_id"],
                      "client_secret": existing.get("client_secret", "")}
        if existing.get("mode") == "oauth":
            data.update(existing)
            prior = list(existing.get("scopes_requested") or [])
            wanted = prior + [s for s in wanted if s not in prior]
        data.update(client)
        tok = run_loopback_flow(client, wanted, open_browser=open_browser,
                                printer=printer, opener=opener, timeout=timeout)
        apply_token_response(data, tok)
        data.setdefault("scopes_granted", wanted)
    elif mode == "service-account":
        if not key_path or not subject:
            raise ConfigError("service-account mode needs --key <key.json> and "
                              "--subject <user@domain> (domain-wide delegation)")
        data.update({"key_path": str(Path(key_path).expanduser().resolve()),
                     "subject": subject})
    else:
        data["token_cmd"] = token_cmd
    data["scopes_requested"] = wanted
    save_profile(profile, data)
    return status(profile)


def status(profile: "str | None" = None) -> Dict[str, Any]:
    """Masked, JSON-safe status for a profile (never a token value)."""
    name = profile or default_profile()
    data = load_profile(name)
    if data is None:
        env = bool(os.environ.get("AWSUITE_ACCESS_TOKEN") or os.environ.get("AWSUITE_TOKEN_CMD"))
        return {"profile": name, "configured": env, "mode": "token" if env else None,
                "source": "environment" if env else None, "path": str(profile_path(name))}
    req = list(data.get("scopes_requested") or [])
    granted = data.get("scopes_granted")
    out = {
        "profile": name, "configured": True, "mode": data.get("mode", "oauth"),
        "provider": data.get("provider", "google"), "path": str(profile_path(name)),
        "scopes_requested": [_scopes.short(s) for s in req],
        "scopes_granted": ([_scopes.short(s) for s in granted]
                           if isinstance(granted, list) else None),
        "missing_scopes": ([_scopes.short(s) for s in req if s not in granted]
                           if isinstance(granted, list) else []),
    }
    if out["mode"] == "oauth":
        out.update({"access_token": mask(data.get("access_token")),
                    "refresh_token": mask(data.get("refresh_token")),
                    "client_id": data.get("client_id"),
                    "expires_at": data.get("expires_at"),
                    "expired": float(data.get("expires_at") or 0) <= time.time()})
    elif out["mode"] == "service-account":
        out.update({"key_path": data.get("key_path"), "subject": data.get("subject")})
    elif out["mode"] == "workspace":
        env = str(data.get("bearer_env") or "")
        out.update({"workspace": data.get("base"), "bearer_env": env or None,
                    "bearer": mask(os.environ.get(env, "").strip() or None) if env else "(none)",
                    "scopes_granted": None, "missing_scopes": []})
    else:
        out["token_cmd"] = data.get("token_cmd") or None
    return out


def logout(profile: "str | None" = None, revoke: bool = True) -> Dict[str, Any]:
    """Revoke (best effort, reported) and delete the profile file."""
    name = profile or default_profile()
    data = load_profile(name)
    if data is None:
        return {"profile": name, "removed": False, "revoked": None}
    revoked: Optional[str] = None
    tok = data.get("refresh_token") or data.get("access_token")
    if revoke and tok and data.get("mode", "oauth") == "oauth":
        try:
            _post_form(REVOKE_URI, {"token": tok}, timeout=10.0)
            revoked = "yes"
        except AuthError as exc:
            revoked = f"failed: {exc}"
    removed = delete_profile(name)
    return {"profile": name, "removed": removed, "revoked": revoked}

