"""awsuite's own doctor checks (the generated `_doctor.py` calls the hooks here).

Checks: Python version, profile token-file permissions, scopes granted vs
requested, a workspace-mode profile's bearer variable, and whether the Google
token endpoint answers within 5 s. Each check reports ok / warn / fail /
unjudged -- an unreachable network is UNJUDGED, never a silent pass.
"""

from __future__ import annotations

import os
import stat
import sys
import urllib.error
import urllib.request
from typing import Dict, List, Tuple

from . import auth as _auth

REACH_URL = "https://oauth2.googleapis.com/token"


def _check_python() -> Dict[str, str]:
    ok = sys.version_info >= (3, 10)
    return {"name": "python", "status": "ok" if ok else "fail",
            "detail": f"{sys.version.split()[0]} (needs >= 3.10)"}


def _check_profiles() -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    names = _auth.list_profiles()
    env = bool(os.environ.get("AWSUITE_ACCESS_TOKEN") or os.environ.get("AWSUITE_TOKEN_CMD"))
    if not names:
        out.append({"name": "profile",
                    "status": "ok" if env else "fail",
                    "detail": ("no profile file; using the platform token from the environment"
                               if env else
                               f"no profile in {_auth.home()}; run `awsuite auth login` "
                               f"(or reuse a workspace's Google connection: `awsuite auth "
                               f"login --workspace <url> --bearer-env NAME`)")})
        return out
    for name in names:
        path = _auth.profile_path(name)
        if os.name == "nt":
            out.append({"name": f"perms:{name}", "status": "ok",
                        "detail": f"{path} (POSIX modes not enforced on Windows; the file "
                                  f"is under your user profile)"})
        else:
            mode = stat.S_IMODE(path.stat().st_mode)
            loose = mode & 0o077
            out.append({"name": f"perms:{name}", "status": "fail" if loose else "ok",
                        "detail": f"{path} mode {oct(mode)}"
                                  + ("; run chmod 600 on it" if loose else "")})
        try:
            st = _auth.status(name)
        except Exception as exc:  # noqa: BLE001 - doctor reports, never crashes
            out.append({"name": f"scopes:{name}", "status": "fail", "detail": str(exc)})
            continue
        if st.get("mode") == "workspace":
            env = st.get("bearer_env") or ""
            have = bool(env and os.environ.get(env, "").strip())
            out.append({"name": f"workspace:{name}", "status": "ok" if have else "warn",
                        "detail": f"borrows the Google connection of {st.get('workspace')}; "
                                  f"bearer from ${env or '(none)'} "
                                  + ("(set)" if have else "(NOT set: export it before a call)")
                                  + "; an admin must allow CLI hand-off in Connectors"})
        missing = st.get("missing_scopes") or []
        if st.get("scopes_granted") is None:
            out.append({"name": f"scopes:{name}", "status": "warn",
                        "detail": f"mode {st.get('mode')}: granted scopes unknown until a call"})
        else:
            out.append({"name": f"scopes:{name}", "status": "warn" if missing else "ok",
                        "detail": (f"{len(st.get('scopes_granted') or [])} granted"
                                   + (f"; NOT granted: {', '.join(missing)}" if missing else
                                      "; all requested scopes granted"))})
    return out


def _check_reach(timeout: float = 5.0) -> Dict[str, str]:
    req = urllib.request.Request(REACH_URL, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code  # any HTTP answer (405/400) proves the endpoint is reachable
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"name": "reach", "status": "unjudged",
                "detail": f"{REACH_URL} unreachable within {int(timeout)} s: {reason}"}
    return {"name": "reach", "status": "ok", "detail": f"{REACH_URL} answered HTTP {code}"}


def collect() -> List[Dict[str, str]]:
    return [_check_python(), *_check_profiles(), _check_reach()]


def exit_code(checks: List[Dict[str, str]]) -> int:
    if any(c["status"] == "fail" for c in checks):
        return 1
    if any(c["status"] == "unjudged" for c in checks):
        return 2
    return 0


_CACHE: List[Dict[str, str]] = []


def _checks() -> List[Dict[str, str]]:
    if not _CACHE:
        _CACHE.extend(collect())
    return _CACHE


def _doctor_local() -> List[str]:
    return [f"{c['name']:<11}{c['status'].upper():<9}{c['detail']}" for c in _checks()]


def _doctor_local_verdict() -> Tuple[List[str], List[str]]:
    cs = _checks()
    return ([f"{c['name']}: {c['detail']}" for c in cs if c["status"] == "fail"],
            [f"{c['name']}: {c['detail']}" for c in cs if c["status"] == "unjudged"])
