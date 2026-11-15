"""Session token authentication and HTTP security headers middleware."""

from __future__ import annotations

import hmac
import os
import re
import secrets
from pathlib import Path
from typing import Callable, Literal, Optional
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

SESSION_COOKIE_NAME = "vp_session"
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "vibe-podcast" / "session_token"
DEFAULT_LISTEN_TOKEN_FILE = Path.home() / ".config" / "vibe-podcast" / "listen_token"

AuthRole = Literal["admin", "listener"]

_LISTENER_GET_PATHS = frozenset({"/api/meta", "/api/episodes", "/api/rss-url"})
_LISTENER_EPISODE_GET_RE = re.compile(
    r"^/api/episodes/[a-zA-Z0-9_-]+/(?:timeline|audio|srt|shownotes)$"
)


def get_token_file_path() -> Path:
    """Return the path where the first-run local session token is persisted."""
    override = os.environ.get("VIBEPODCAST_TOKEN_FILE")
    return Path(override).expanduser() if override else DEFAULT_TOKEN_FILE


def get_listen_token_file_path() -> Path:
    """Return the path where the read-only listener token is persisted."""
    override = os.environ.get("VIBEPODCAST_LISTEN_TOKEN_FILE")
    return Path(override).expanduser() if override else DEFAULT_LISTEN_TOKEN_FILE


def _load_or_create_token(env_var: str, target: Path) -> str:
    """Return configured env var token, or load/create a persistent local token (0600)."""
    env_token = os.environ.get(env_var, "").strip()
    if env_token:
        return env_token

    try:
        if target.is_file():
            try:
                if (target.stat().st_mode & 0o777) != 0o600:
                    os.chmod(target, 0o600)
            except OSError:
                pass
            existing = target.read_text(encoding="utf-8").strip()
            if existing:
                return existing
    except OSError:
        pass

    token = secrets.token_urlsafe(32)
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
    except OSError:
        pass
    return token


def new_session_token(token_file: Path | None = None) -> str:
    """Return configured VIBEPODCAST_TOKEN, or load/create a persistent local admin token (0600)."""
    return _load_or_create_token("VIBEPODCAST_TOKEN", token_file or get_token_file_path())


def new_listen_token(token_file: Path | None = None) -> str:
    """Return configured VIBEPODCAST_LISTEN_TOKEN, or load/create a persistent listener token (0600)."""
    return _load_or_create_token(
        "VIBEPODCAST_LISTEN_TOKEN", token_file or get_listen_token_file_path()
    )


def _match_token_role(
    candidate: str,
    admin_token: str,
    listen_token: Optional[str] = None,
) -> Optional[AuthRole]:
    if not candidate:
        return None
    candidate_bytes = candidate.encode("utf-8")
    if hmac.compare_digest(candidate_bytes, admin_token.encode("utf-8")):
        return "admin"
    if listen_token and hmac.compare_digest(candidate_bytes, listen_token.encode("utf-8")):
        return "listener"
    return None


def match_token_role(
    candidate: str,
    admin_token: str,
    listen_token: Optional[str] = None,
) -> Optional[AuthRole]:
    """Validate a candidate token string against admin and optional listener tokens."""
    return _match_token_role(candidate, admin_token, listen_token)


def set_session_cookie(response, token: str, request: Request) -> None:
    """Attach the HttpOnly session cookie with attributes matched to transport security."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_is_https_request(request),
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )


def get_auth_role(
    request: Request,
    admin_token: str,
    listen_token: Optional[str] = None,
    allow_query_token: bool = False,
    allow_query_admin: bool = True,
) -> Optional[AuthRole]:
    """Resolve authenticated role ('admin' or 'listener') from Bearer, cookie, or query token."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        role = _match_token_role(auth_header[7:].strip(), admin_token, listen_token)
        if role:
            return role
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    role = _match_token_role(cookie_token, admin_token, listen_token)
    if role:
        return role
    if allow_query_token:
        q_token = request.query_params.get("token", "")
        role = _match_token_role(q_token, admin_token, listen_token)
        if role and (allow_query_admin or role != "admin"):
            return role
    return None


def _is_listener_allowed_api(method: str, path: str) -> bool:
    """Return True if a read-only listener is allowed to call this API endpoint."""
    if method not in {"GET", "HEAD"}:
        return False
    return path in _LISTENER_GET_PATHS or bool(_LISTENER_EPISODE_GET_RE.fullmatch(path))


def _is_https_request(request: Request) -> bool:
    """Detect whether the external client connection is HTTPS via URL, proxy headers, or public URL."""
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if request.url.scheme == "https" or forwarded_proto == "https":
        return True
    public_url = os.environ.get("VIBEPODCAST_PUBLIC_URL", "").strip()
    if public_url.lower().startswith("https://"):
        public_host = (urlparse(public_url).hostname or "").lower()
        req_host = request.headers.get("host", "").split(":", 1)[0].strip().lower()
        return bool(public_host and (not req_host or req_host == public_host))
    return False


def apply_security_headers(response) -> None:
    """Attach defensive browser security headers to HTTP responses."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
        "frame-ancestors 'none'"
    )


async def handle_security_and_auth(
    request: Request,
    call_next: Callable,
    expected_token: str,
    listen_token: Optional[str] = None,
):
    """Enforce dual-role (admin/listener) token auth on /api/*, handle /?token= login, and set headers."""
    path = request.url.path
    if request.method == "GET" and path == "/" and "token" in request.query_params:
        supplied = request.query_params.get("token", "")
        supplied_role = _match_token_role(supplied, expected_token, listen_token)
        if supplied_role is not None:
            resp = RedirectResponse(url="/", status_code=302)
            existing_cookie = request.cookies.get(SESSION_COOKIE_NAME, "").strip()
            existing_role = _match_token_role(existing_cookie, expected_token, listen_token)
            # Prevent Login CSRF downgrade from an active admin session to a listener session.
            if not (existing_role == "admin" and supplied_role == "listener"):
                set_session_cookie(resp, supplied, request)
            resp.headers["Cache-Control"] = "no-store"
            apply_security_headers(resp)
            return resp
        resp = JSONResponse({"detail": "Invalid session token"}, status_code=401)
        resp.headers["Cache-Control"] = "no-store"
        apply_security_headers(resp)
        return resp

    if path.startswith("/api/") and request.method != "OPTIONS":
        allow_query = path.endswith("/audio")
        role = get_auth_role(
            request,
            admin_token=expected_token,
            listen_token=listen_token,
            allow_query_token=allow_query,
            allow_query_admin=False,
        )
        request.state.auth_role = role
        if path not in {"/api/health", "/api/login"}:
            if role is None:
                resp = JSONResponse({"detail": "Authentication required"}, status_code=401)
                resp.headers["Cache-Control"] = "no-store"
                apply_security_headers(resp)
                return resp
            if role != "admin" and not _is_listener_allowed_api(request.method, path):
                resp = JSONResponse({"detail": "Admin permission required"}, status_code=403)
                resp.headers["Cache-Control"] = "no-store"
                apply_security_headers(resp)
                return resp

    response = await call_next(request)
    if (
        path in {"/", "/index.html", "/app.js", "/style.css", "/feed.xml"}
        or path.startswith("/js/")
        or path.startswith("/css/")
        or path.startswith("/api/")
    ):
        response.headers["Cache-Control"] = "no-store"
    apply_security_headers(response)
    return response
