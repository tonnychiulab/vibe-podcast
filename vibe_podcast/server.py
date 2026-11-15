"""FastAPI server entry point for VibePodcast studio & visualizer."""

from __future__ import annotations

import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from vibe_podcast import __version__
from vibe_podcast.auth import (
    get_listen_token_file_path,
    get_token_file_path,
    handle_security_and_auth,
    new_listen_token,
    new_session_token,
)
from vibe_podcast.connectors import (
    ConnectorRegistry,
    MarkdownDirConnector,
    WebUrlConnector,
)
from vibe_podcast.guardrails import ContentSafetyError
from vibe_podcast.i18n import DEFAULT_LOCALE, get_error_message
from vibe_podcast.routes.episodes import router as episodes_router
from vibe_podcast.routes.studio import router as studio_router
from vibe_podcast.schemas import GenerateRequest
from vibe_podcast.scriptwriter import PodcastScript, generate_podcast_script

logger = logging.getLogger("vibe_podcast.server")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
EPISODES_DIR = Path(os.environ.get("VIBEPODCAST_EPISODES_DIR", PROJECT_ROOT / "episodes"))
REPORTS_DIR = Path(
    os.environ.get("REPORTS_DIR")
    or os.environ.get("YTAUTOCUT_OUTPUT_DIR")
    or (PROJECT_ROOT / "reports")
)

connector_registry = ConnectorRegistry()
connector_registry.register(MarkdownDirConnector(REPORTS_DIR))
web_url_connector = WebUrlConnector()

SESSION_TOKEN: str = new_session_token()
LISTEN_TOKEN: str = new_listen_token()
_GENERATION_SEMAPHORE = threading.BoundedSemaphore(value=2)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    port = os.environ.get("PORT", "8765")
    public_url = os.environ.get("VIBEPODCAST_PUBLIC_URL", "").strip().rstrip("/")
    token_file = get_token_file_path()
    listen_file = get_listen_token_file_path()
    logger.info("VibePodcast Auth URL: http://127.0.0.1:%s/?token=%s", port, SESSION_TOKEN)
    public_lines = (
        f"🌐 Public Admin URL: {public_url}/?token={SESSION_TOKEN}\n"
        f"🎧 Public Listen-Only URL: {public_url}/?token={LISTEN_TOKEN}\n"
        if public_url
        else ""
    )
    print(
        f"\n🔐 VibePodcast Admin Token（{token_file}）：{SESSION_TOKEN}\n"
        f"🎧 VibePodcast Listen-Only Token（{listen_file}）：{LISTEN_TOKEN}\n"
        f"🔐 Local Admin URL: http://127.0.0.1:{port}/?token={SESSION_TOKEN}\n"
        f"🎧 Local Listen-Only URL: http://127.0.0.1:{port}/?token={LISTEN_TOKEN}\n"
        f"{public_lines}",
        flush=True,
    )
    yield


def _build_allowed_origins() -> list[str]:
    """Combine localhost origins with optional VIBEPODCAST_PUBLIC_URL and VIBEPODCAST_ALLOWED_ORIGINS."""
    origins = ["http://127.0.0.1:8765", "http://localhost:8765"]
    extra_raw = os.environ.get("VIBEPODCAST_ALLOWED_ORIGINS", "")
    public_url = os.environ.get("VIBEPODCAST_PUBLIC_URL", "").strip().rstrip("/")
    candidates = [item.strip().rstrip("/") for item in extra_raw.split(",") if item.strip()]
    if public_url:
        candidates.append(public_url)
    for origin in candidates:
        if (origin.startswith("https://") or origin.startswith("http://")) and origin not in origins:
            origins.append(origin)
    return origins


app = FastAPI(title="老馬麵攤 — VibePodcast Studio API", version=__version__, lifespan=_lifespan)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:32] or "episode"


def _make_episode_id(title: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid4().hex[:6]}-{_slugify(title)}"


def _resolve_report_path(report_path: str, lang: str = DEFAULT_LOCALE) -> str:
    try:
        return connector_registry.load_text(report_path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=get_error_message("forbidden_report", lang)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=get_error_message("report_not_found", lang)) from exc


def _resolve_generate_source(req: GenerateRequest) -> str:
    """Resolve source text from URL, local report, or manual textarea."""
    source_text = (req.source_text or "").strip()
    if req.source_url and req.source_url.strip():
        try:
            source_text = web_url_connector.load_url(req.source_url.strip())
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("URL fetch failed for %s: %s", req.source_url, exc)
            raise HTTPException(
                status_code=502,
                detail=get_error_message("url_fetch_failed", req.ui_lang, detail=type(exc).__name__),
            ) from exc
    elif req.report_path:
        source_text = _resolve_report_path(req.report_path, req.ui_lang)

    if not source_text:
        raise HTTPException(status_code=400, detail=get_error_message("empty_source", req.ui_lang))
    return source_text


def _draft_script_from_request(req: GenerateRequest, source_text: str) -> PodcastScript:
    """Invoke scriptwriter and map ContentSafetyError to HTTP 422."""
    try:
        return generate_podcast_script(
            source_text=source_text,
            host_a=req.host_a,
            host_b=req.host_b,
            language=req.language,
            target_turns=req.target_turns,
            backend=req.backend,
            model=(req.model or "").strip() or None,
            interview_format=req.interview_format,
            guest_name=(req.guest_name or "").strip() or None,
            focus_questions=req.focus_questions,
            long_text_mode=req.long_text_mode,
            episodes_dir=EPISODES_DIR,
            enable_show_memory=req.enable_show_memory,
        )
    except ContentSafetyError as exc:
        raise HTTPException(
            status_code=422,
            detail=get_error_message("safety_blocked", req.ui_lang, detail=str(exc)),
        ) from exc


def _acquire_generation_slot(lang: str = DEFAULT_LOCALE) -> None:
    if not _GENERATION_SEMAPHORE.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail=(
                "目前生成任務已達上限，請稍候再試。"
                if lang.startswith("zh")
                else "Too many concurrent generation jobs; please retry shortly."
            ),
        )


@app.middleware("http")
async def _security_and_auth_middleware(request: Request, call_next):
    return await handle_security_and_auth(
        request, call_next, SESSION_TOKEN, listen_token=LISTEN_TOKEN
    )


# Register CORS outermost (after @app.middleware) so browser OPTIONS preflight passes before auth.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(studio_router)
app.include_router(episodes_router)

if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
