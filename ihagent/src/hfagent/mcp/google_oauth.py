"""One-click Google sign-in for the workspace-mcp preset.

The end user does not paste a client id or an email address. IHAgent uses a
bundled Desktop OAuth client (or one the user supplied), opens the system
browser, catches the loopback redirect, and stores tokens where workspace-mcp
reads them.

Precedence for the OAuth client:

1. ``--client-id`` / ``--client-secret``
2. ``GOOGLE_OAUTH_CLIENT_ID`` / ``GOOGLE_OAUTH_CLIENT_SECRET`` in the process
   environment or ``~/.hfagent/.env``
3. Build-time constants in this module, then ``google_client.json``

A Desktop client secret is not confidential. Google says installed apps embed
it. Tokens and the secret are still written mode ``0600`` and never printed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from . import config as mcp_config
from .oauth import LoopbackCallback

# Optional build-time injection. Leave both empty in git. A release build may
# assign a Desktop OAuth client here; google_client.json is the other bundled
# source. User flags and GOOGLE_OAUTH_* still win over both.
BUILD_GOOGLE_OAUTH_CLIENT_ID = ""
BUILD_GOOGLE_OAUTH_CLIENT_SECRET = ""

PLACEHOLDER_MESSAGE = (
    "Bundled Google OAuth client is still a placeholder, so the browser was not opened. "
    "Paste a Desktop app client into hfagent/mcp/google_client.json (see README)."
)

GOOGLE_SIGN_IN_TIMEOUT = 180.0
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

# Same scope set workspace-mcp requests for gmail + drive + calendar
# (auth/scopes.py: BASE_SCOPES + those three services). Broader scopes cover
# the narrower ones, and gmail.settings.basic is not implied by gmail.modify.
GOOGLE_WORKSPACE_SCOPES: tuple[str, ...] = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
)

_PLACEHOLDER_MARKERS = ("placeholder", "your_client", "your-client", "replace_me", "changeme")


class GoogleSignInError(Exception):
    """Sign-in failed before any token was shown to the user."""


@dataclass(frozen=True)
class GoogleOAuthClient:
    client_id: str
    client_secret: str
    source: str
    placeholder: bool


@dataclass(frozen=True)
class GoogleSignIn:
    email: str
    refreshable: bool


def google_client_file() -> Path:
    return Path(__file__).resolve().with_name("google_client.json")


def google_credentials_dir() -> Path:
    """Where workspace-mcp reads credential JSON. Under the MCP token dir."""
    return mcp_config.token_dir() / "google-workspace"


def looks_like_email(value: str) -> bool:
    text = value.strip()
    if not text or len(text) > 254 or "@" not in text:
        return False
    if any(ch.isspace() for ch in text) or "/" in text or "\\" in text:
        return False
    local, _, domain = text.partition("@")
    return bool(local) and "." in domain and ".." not in text


def resolve_google_client(flags: dict[str, Any]) -> GoogleOAuthClient:
    """Flags, then env/.env, then the bundled Desktop client."""
    flag_id = str(flags.get("client_id") or "").strip()
    flag_secret = str(flags.get("client_secret") or "").strip()
    if flag_id or flag_secret:
        if not flag_id or not flag_secret:
            raise mcp_config.ConfigError("Pass both --client-id and --client-secret, or neither.")
        return GoogleOAuthClient(
            flag_id,
            flag_secret,
            "flags",
            _is_placeholder_pair(flag_id, flag_secret),
        )
    env_id = mcp_config.user_setting("GOOGLE_OAUTH_CLIENT_ID").strip()
    env_secret = mcp_config.user_setting("GOOGLE_OAUTH_CLIENT_SECRET").strip()
    if env_id or env_secret:
        if not env_id or not env_secret:
            raise mcp_config.ConfigError(
                "Set both GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET, or neither."
            )
        return GoogleOAuthClient(
            env_id,
            env_secret,
            "env",
            _is_placeholder_pair(env_id, env_secret),
        )
    return load_bundled_google_client()


def load_bundled_google_client() -> GoogleOAuthClient:
    """Build-time constants if they are real, otherwise ``google_client.json``."""
    baked_id = BUILD_GOOGLE_OAUTH_CLIENT_ID.strip()
    baked_secret = BUILD_GOOGLE_OAUTH_CLIENT_SECRET.strip()
    if not _is_placeholder_pair(baked_id, baked_secret):
        return GoogleOAuthClient(baked_id, baked_secret, "build", False)
    data = _read_client_file()
    client_id = str(data.get("client_id") or "").strip()
    secret = str(data.get("client_secret") or "").strip()
    placeholder = data.get("placeholder") is True or _is_placeholder_pair(client_id, secret)
    return GoogleOAuthClient(client_id, secret, "bundled", placeholder)


def client_for_config(cfg: mcp_config.ServerConfig) -> GoogleOAuthClient:
    """Saved server env wins when it already has a real client."""
    client_id = str(cfg.env.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
    secret = str(cfg.env.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
    if client_id and secret and not _is_placeholder_pair(client_id, secret):
        return GoogleOAuthClient(client_id, secret, "config", False)
    return resolve_google_client({})


def ensure_google_env(cfg: mcp_config.ServerConfig) -> GoogleOAuthClient:
    """Put the resolved client and credential directory on the server env."""
    client = client_for_config(cfg)
    cfg.env["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    cfg.env["WORKSPACE_MCP_CREDENTIALS_DIR"] = str(google_credentials_dir())
    if client.placeholder:
        cfg.env.pop("GOOGLE_OAUTH_CLIENT_ID", None)
        cfg.env.pop("GOOGLE_OAUTH_CLIENT_SECRET", None)
        return client
    cfg.env["GOOGLE_OAUTH_CLIENT_ID"] = client.client_id
    cfg.env["GOOGLE_OAUTH_CLIENT_SECRET"] = client.client_secret
    return client


def google_preset_env(flags: dict[str, Any]) -> dict[str, str]:
    env: dict[str, str] = {
        "OAUTHLIB_INSECURE_TRANSPORT": "1",
        "WORKSPACE_MCP_CREDENTIALS_DIR": str(google_credentials_dir()),
    }
    client = resolve_google_client(flags)
    if not client.placeholder:
        env["GOOGLE_OAUTH_CLIENT_ID"] = client.client_id
        env["GOOGLE_OAUTH_CLIENT_SECRET"] = client.client_secret
    email = mcp_config.user_setting("USER_GOOGLE_EMAIL") or mcp_config.user_setting("GOOGLE_USER_EMAIL")
    if looks_like_email(email):
        env["USER_GOOGLE_EMAIL"] = email
    return env


def authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    login_hint: str = "",
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_WORKSPACE_SCOPES),
        "state": state,
        "access_type": "offline",
        "prompt": "consent select_account",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "include_granted_scopes": "true",
    }
    if looks_like_email(login_hint):
        params["login_hint"] = login_hint.strip()
    return _AUTH_URL + "?" + urlencode(params)


def sign_in_google(
    client: GoogleOAuthClient,
    *,
    on_status: Callable[[str], None],
    on_notice: Callable[[str], None] | None = None,
    timeout: float = GOOGLE_SIGN_IN_TIMEOUT,
    open_browser: Callable[[str], bool] | None = None,
    login_hint: str = "",
) -> GoogleSignIn:
    """Open the browser, catch the loopback redirect, and store tokens.

    Raises TimeoutError or GoogleSignInError. Neither message contains a
    token or the client secret.
    """
    if client.placeholder:
        raise GoogleSignInError(PLACEHOLDER_MESSAGE)
    opener = _open_browser if open_browser is None else open_browser
    notice = on_notice or on_status
    callback = LoopbackCallback(timeout=timeout, paths=("/", "/callback"))
    try:
        callback.start()
    except OSError as exc:
        raise GoogleSignInError(
            "Google sign-in could not listen on 127.0.0.1. Run /mcp login google to try again."
        ) from exc
    try:
        redirect_uri = f"{callback.origin}/"
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        url = authorization_url(
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=_code_challenge(verifier),
            login_hint=login_hint,
        )
        on_status("Google: opening the browser to sign in…")
        opened = False
        try:
            opened = bool(opener(url))
        except Exception:
            opened = False
        if not opened:
            on_status("Google: the browser did not open.")
            notice("If the browser did not open, sign in at this page:\n" + url)
        on_status("Google: waiting for approval in the browser…")
        try:
            code, got_state, _iss, error = callback.wait_sync()
        except TimeoutError as exc:
            raise TimeoutError(
                "Google sign-in timed out. Run /mcp login google to try again."
            ) from exc
        if error == "access_denied":
            raise GoogleSignInError(
                "Google sign-in was cancelled. Run /mcp login google to try again."
            )
        if error or not code:
            raise GoogleSignInError(
                "Google sign-in did not finish. Run /mcp login google to try again."
            )
        if not got_state or len(got_state) != len(state) or not secrets.compare_digest(got_state, state):
            raise GoogleSignInError(
                "Google sign-in did not match this session. Run /mcp login google to try again."
            )
        payload = exchange_code(
            code=code,
            redirect_uri=redirect_uri,
            client_id=client.client_id,
            client_secret=client.client_secret,
            code_verifier=verifier,
        )
        access = str(payload.get("access_token") or "")
        if not access:
            raise GoogleSignInError(
                "Google did not return an access token. Run /mcp login google to try again."
            )
        email = email_from_id_token(str(payload.get("id_token") or ""))
        if not looks_like_email(email):
            email = fetch_userinfo(access)
        if not looks_like_email(email):
            raise GoogleSignInError(
                "Google sign-in finished, but no account email was returned. "
                "Run /mcp login google to try again."
            )
        refresh = str(payload.get("refresh_token") or "")
        store_workspace_credential(email, payload, client)
        on_status(f"Google: signed in as {email}.")
        return GoogleSignIn(email=email, refreshable=bool(refresh))
    finally:
        callback.stop()


def exchange_code(
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    code_verifier: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST the authorization code. The body is never logged."""
    form = urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")
    request = Request(_TOKEN_URL, data=form, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Accept", "application/json")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except Exception as exc:
        code_name = _http_error_code(exc)
        raise GoogleSignInError(f"Google token exchange failed ({code_name}).") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GoogleSignInError("Google token exchange failed (bad_response).") from exc
    if not isinstance(payload, dict):
        raise GoogleSignInError("Google token exchange failed (bad_response).")
    if payload.get("error"):
        name = str(payload.get("error") or "error")
        if len(name) > 80:
            name = name[:80]
        raise GoogleSignInError(f"Google token exchange failed ({name}).")
    return payload


def fetch_userinfo(access_token: str, timeout: float = 30.0) -> str:
    if not access_token:
        return ""
    request = Request(_USERINFO_URL)
    request.add_header("Authorization", "Bearer " + access_token)
    request.add_header("Accept", "application/json")
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    email = str(payload.get("email") or "").strip()
    return email if looks_like_email(email) else ""


def email_from_id_token(id_token: str) -> str:
    """Read the email claim. The token came from Google over HTTPS."""
    parts = id_token.split(".")
    if len(parts) < 2 or not parts[1]:
        return ""
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, json.JSONDecodeError, UnicodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    email = str(data.get("email") or "").strip()
    return email if looks_like_email(email) else ""


def store_workspace_credential(
    email: str,
    payload: dict[str, Any],
    client: GoogleOAuthClient,
) -> Path:
    """workspace-mcp LocalDirectoryCredentialStore JSON, mode 0600."""
    scopes = payload.get("scope") or ""
    if isinstance(scopes, str):
        scope_list = [item for item in scopes.split() if item]
    elif isinstance(scopes, list):
        scope_list = [str(item) for item in scopes if str(item).strip()]
    else:
        scope_list = []
    if not scope_list:
        scope_list = list(GOOGLE_WORKSPACE_SCOPES)
    try:
        expires_in = int(payload.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    if expires_in < 0:
        expires_in = 3600
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).replace(tzinfo=None)
    body = {
        "token": str(payload.get("access_token") or ""),
        "refresh_token": str(payload.get("refresh_token") or "") or None,
        "token_uri": _TOKEN_URL,
        "client_id": client.client_id,
        "client_secret": client.client_secret,
        "scopes": scope_list,
        "expiry": expiry.isoformat(timespec="seconds"),
    }
    path = google_credentials_dir() / credential_filename(email)
    _write_private_json(path, body)
    return path


def credential_filename(email: str) -> str:
    if not looks_like_email(email):
        raise GoogleSignInError(
            "Google did not return an account email. Run /mcp login google to try again."
        )
    safe = quote(email, safe="@._-")
    if not safe or safe in {".", ".."} or "/" in safe or "\\" in safe:
        raise GoogleSignInError(
            "Google did not return an account email. Run /mcp login google to try again."
        )
    return safe + ".json"


def clear_google_credentials(email: str = "") -> None:
    """Delete IHAgent's copy of the Google tokens, and that account's legacy file."""
    root = google_credentials_dir()
    if root.is_dir():
        for path in root.glob("*.json"):
            if path.is_file() and path.parent.resolve() == root.resolve():
                try:
                    path.unlink()
                except OSError:
                    pass
    if not looks_like_email(email):
        return
    legacy = Path.home() / ".google_workspace_mcp" / "credentials"
    try:
        path = (legacy / credential_filename(email)).resolve()
    except GoogleSignInError:
        return
    try:
        if path.is_file() and path.parent.resolve() == legacy.resolve():
            path.unlink()
    except OSError:
        return


def remember_google_email(cfg: mcp_config.ServerConfig, email: str) -> None:
    """Persist the signed-in address for workspace-mcp and the next launch."""
    cfg.env["USER_GOOGLE_EMAIL"] = email
    mcp_config.upsert_server(cfg)
    try:
        from ..config import save_user_setting

        save_user_setting("USER_GOOGLE_EMAIL", email)
    except OSError:
        return


def forget_saved_google_email() -> None:
    """Drop USER_GOOGLE_EMAIL from ~/.hfagent/.env. Other lines stay."""
    from ..config import user_env_path

    path = user_env_path()
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    kept = [line for line in lines if not line.strip().startswith("USER_GOOGLE_EMAIL=")]
    if kept == lines:
        return
    text = "\n".join(kept).rstrip()
    try:
        path.write_text((text + "\n") if text else "", encoding="utf-8")
    except OSError:
        return


def signed_in_google_note() -> str:
    """System-prompt line with the address discovered at sign-in."""
    try:
        servers = mcp_config.load_servers()
    except Exception:
        return ""
    for cfg in servers.values():
        if not mcp_config.is_google_workspace(cfg):
            continue
        email = str(cfg.env.get("USER_GOOGLE_EMAIL") or "").strip()
        if looks_like_email(email):
            return (
                f"Signed-in Google account: {email}. "
                "Pass this address as user_google_email on Gmail, Calendar, and Drive tools. "
                "Do not ask the user for an email address."
            )
    return ""


def google_tool_hint(env: dict[str, str]) -> str:
    email = str(env.get("USER_GOOGLE_EMAIL") or "").strip()
    if looks_like_email(email):
        return (
            f" Signed-in Google account: {email}. "
            f"Pass user_google_email as {email}. "
            "Do not ask the user for an email address."
        )
    return (
        " Do not ask the user for an email address. "
        "Sign in with /mcp login google and pass the signed-in account."
    )


def apply_google_account_hint(parameters: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Tell the model the address on the user_google_email field only."""
    props = parameters.get("properties")
    if not isinstance(props, dict):
        return parameters
    field = props.get("user_google_email")
    if not isinstance(field, dict):
        return parameters
    email = str(env.get("USER_GOOGLE_EMAIL") or "").strip()
    updated = dict(field)
    if looks_like_email(email):
        updated["description"] = (
            f"The signed-in Google account is {email}. "
            "Pass this exact address. Do not ask the user for an email address."
        )
        updated["default"] = email
    else:
        updated["description"] = (
            "Do not ask the user for an email address. "
            "Sign in with /mcp login google and pass the signed-in account."
        )
    copied = dict(props)
    copied["user_google_email"] = updated
    parameters["properties"] = copied
    return parameters


def bake_google_client_from_env(path: Path | None = None) -> Path:
    """Write google_client.json from GOOGLE_OAUTH_CLIENT_ID/SECRET. Prints nothing secret."""
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if _is_placeholder_pair(client_id, secret):
        raise GoogleSignInError(
            "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET to a Desktop app client, then re-run."
        )
    target = google_client_file() if path is None else path
    body = {
        "client_id": client_id,
        "client_secret": secret,
        "placeholder": False,
        "note": (
            "Desktop app OAuth client baked from the build environment. "
            "The secret is not confidential for installed apps."
        ),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--bake"]:
        try:
            path = bake_google_client_from_env()
        except GoogleSignInError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Wrote bundled Google client to {path}. The secret was not printed.")
        return 0
    print("usage: python -m hfagent.mcp.google_oauth --bake", file=sys.stderr)
    return 2


def _is_placeholder_pair(client_id: str, secret: str) -> bool:
    client_id = client_id.strip()
    secret = secret.strip()
    if not client_id or not secret:
        return True
    blob = f"{client_id}\n{secret}".lower()
    return any(marker in blob for marker in _PLACEHOLDER_MARKERS)


def _read_client_file() -> dict[str, Any]:
    path = google_client_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _open_browser(url: str) -> bool:
    outcome: list[bool] = []

    def run() -> None:
        try:
            outcome.append(bool(webbrowser.open(url, new=1, autoraise=True)))
        except Exception:
            outcome.append(False)

    thread = threading.Thread(target=run, name="ihagent-google-browser", daemon=True)
    thread.start()
    thread.join(5)
    if thread.is_alive():
        return False
    return bool(outcome and outcome[0])


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    payload = json.dumps(data, indent=2) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _http_error_code(exc: BaseException) -> str:
    read = getattr(exc, "read", None)
    raw = b""
    if callable(read):
        try:
            raw = read() or b""
        except Exception:
            raw = b""
    if raw:
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data.get("error"):
            name = str(data.get("error"))
            return name[:80]
    status = getattr(exc, "code", None)
    if isinstance(status, int):
        return f"http_{status}"
    return "unreachable"


if __name__ == "__main__":
    raise SystemExit(main())
