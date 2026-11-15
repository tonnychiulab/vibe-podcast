"""Episode listing, playback, export (SRT/Show Notes/RSS), and single-turn re-synthesis routes."""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import ValidationError

from vibe_podcast.auth import get_auth_role
from vibe_podcast.exporters import (
    episodes_to_rss_xml,
    timeline_to_shownotes_md,
    timeline_to_srt,
)
from vibe_podcast.guardrails import ContentSafetyError
from vibe_podcast.i18n import DEFAULT_LOCALE, get_error_message
from vibe_podcast.routes import get_server
from vibe_podcast.schemas import EpisodeSummary, ResynthesizeTurnRequest
from vibe_podcast.synthesizer import SynthesisResult, resynthesize_turn

logger = logging.getLogger("vibe_podcast.routes.episodes")

router = APIRouter()
_EPISODE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_HOST_HEADER_RE = re.compile(r"^(?:\[[0-9a-fA-F:]+\]|[a-zA-Z0-9.-]+)(?::\d{1,5})?$")


def _resolve_episode_dir(episode_id: str, lang: str = DEFAULT_LOCALE) -> Path:
    if not _EPISODE_ID_RE.fullmatch(episode_id):
        raise HTTPException(status_code=400, detail=get_error_message("invalid_episode_id", lang))
    episodes_dir = get_server().EPISODES_DIR
    raw_dir = episodes_dir / episode_id
    if raw_dir.is_symlink():
        raise HTTPException(status_code=404, detail=get_error_message("episode_not_found", lang))
    ep_dir = raw_dir.resolve()
    root_dir = episodes_dir.resolve()
    if ep_dir.parent != root_dir or not ep_dir.is_dir():
        raise HTTPException(status_code=404, detail=get_error_message("episode_not_found", lang))
    return ep_dir


def _load_episode_timeline(ep_dir: Path, lang: str = DEFAULT_LOCALE) -> SynthesisResult:
    timeline_file = ep_dir / "timeline.json"
    if timeline_file.is_symlink() or not timeline_file.resolve().is_relative_to(ep_dir.resolve()):
        raise HTTPException(status_code=404, detail=get_error_message("episode_not_found", lang))
    if not timeline_file.is_file():
        raise HTTPException(status_code=404, detail=get_error_message("episode_not_found", lang))
    try:
        return SynthesisResult.model_validate_json(timeline_file.read_text(encoding="utf-8"))
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=get_error_message("episode_not_found", lang)
        ) from exc


def _load_all_timelines(episodes_dir: Path) -> List[SynthesisResult]:
    episodes_dir.mkdir(parents=True, exist_ok=True)
    root_dir = episodes_dir.resolve()
    results: List[SynthesisResult] = []
    for timeline_file in sorted(episodes_dir.glob("*/timeline.json"), reverse=True):
        if timeline_file.parent.is_symlink() or timeline_file.is_symlink():
            logger.warning("Skipping symlinked timeline path %s", timeline_file)
            continue
        try:
            resolved = timeline_file.resolve()
            if not resolved.is_relative_to(root_dir):
                continue
            results.append(
                SynthesisResult.model_validate_json(resolved.read_text(encoding="utf-8"))
            )
        except Exception as exc:
            logger.warning("Invalid timeline %s: %s", timeline_file, exc)
    return results


@router.get("/api/episodes", response_model=List[EpisodeSummary])
def list_episodes() -> List[EpisodeSummary]:
    """List all synthesized podcast episodes."""
    return [
        EpisodeSummary(
            episode_id=data.episode_id,
            title=data.title,
            summary=data.summary,
            host_a=data.host_a,
            host_b=data.host_b,
            guest_name=data.guest_name,
            engine=data.engine,
            duration_sec=data.duration_sec,
        )
        for data in _load_all_timelines(get_server().EPISODES_DIR)
    ]


@router.get("/api/episodes/{episode_id}/timeline", response_model=SynthesisResult)
def get_episode_timeline(episode_id: str, lang: str = DEFAULT_LOCALE) -> SynthesisResult:
    ep_dir = _resolve_episode_dir(episode_id, lang)
    return _load_episode_timeline(ep_dir, lang)


@router.get("/api/episodes/{episode_id}/audio")
def get_episode_audio(episode_id: str, lang: str = DEFAULT_LOCALE) -> FileResponse:
    ep_dir = _resolve_episode_dir(episode_id, lang)
    ep_root = ep_dir.resolve()
    for candidate, media_type in [
        (ep_dir / "podcast.mp3", "audio/mpeg"),
        (ep_dir / "podcast.wav", "audio/wav"),
    ]:
        if candidate.is_symlink():
            continue
        resolved = candidate.resolve()
        if resolved.is_relative_to(ep_root) and resolved.is_file():
            return FileResponse(resolved, media_type=media_type)
    raise HTTPException(status_code=404, detail=get_error_message("audio_not_found", lang))


@router.get("/api/episodes/{episode_id}/srt")
def get_episode_srt(episode_id: str, lang: str = DEFAULT_LOCALE) -> PlainTextResponse:
    """Export episode timeline as standard SubRip (.srt) subtitles."""
    ep_dir = _resolve_episode_dir(episode_id, lang)
    result = _load_episode_timeline(ep_dir, lang)
    srt_text = timeline_to_srt(result)
    return PlainTextResponse(
        content=srt_text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{episode_id}.srt"'},
    )


@router.get("/api/episodes/{episode_id}/shownotes")
def get_episode_shownotes(episode_id: str, lang: str = DEFAULT_LOCALE) -> PlainTextResponse:
    """Export episode timeline and metadata as Markdown Show Notes."""
    ep_dir = _resolve_episode_dir(episode_id, lang)
    result = _load_episode_timeline(ep_dir, lang)
    md_text = timeline_to_shownotes_md(result)
    return PlainTextResponse(
        content=md_text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{episode_id}-shownotes.md"'},
    )


@router.post("/api/episodes/{episode_id}/turns/{turn_index}", response_model=SynthesisResult)
def update_episode_turn(
    episode_id: str, turn_index: int, req: ResynthesizeTurnRequest
) -> SynthesisResult:
    """Edit and re-synthesize a single turn in-place for an existing episode."""
    srv = get_server()
    srv._acquire_generation_slot(req.ui_lang)
    try:
        ep_dir = _resolve_episode_dir(episode_id, req.ui_lang)
        try:
            return resynthesize_turn(
                episode_dir=ep_dir,
                episode_id=episode_id,
                turn_index=turn_index,
                new_text=req.text,
                new_cue=req.cue,
            )
        except ContentSafetyError as exc:
            raise HTTPException(
                status_code=422,
                detail=get_error_message("safety_blocked", req.ui_lang, detail=str(exc)),
            ) from exc
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="Turn index out of range") from exc
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        srv._GENERATION_SEMAPHORE.release()


@router.delete("/api/episodes/{episode_id}")
def delete_episode(episode_id: str, lang: str = DEFAULT_LOCALE) -> dict:
    """Delete a synthesized podcast episode directory."""
    ep_dir = _resolve_episode_dir(episode_id, lang)
    shutil.rmtree(ep_dir)
    return {"deleted": episode_id}


def _resolve_public_base_url(request: Request) -> str:
    """Resolve external HTTPS base URL from VIBEPODCAST_PUBLIC_URL, validated proxy headers, or request."""
    configured = os.environ.get("VIBEPODCAST_PUBLIC_URL", "").strip().rstrip("/")
    if configured.startswith("https://") or configured.startswith("http://"):
        return configured
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    raw_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip() or request.headers.get("host", "").strip()
    if (
        forwarded_proto in {"http", "https"}
        and raw_host
        and _HOST_HEADER_RE.fullmatch(raw_host)
    ):
        return f"{forwarded_proto}://{raw_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get("/api/rss-url")
def get_rss_subscription_url(request: Request) -> dict[str, str]:
    """Return the authenticated, read-only tokenized RSS subscription URL for external Podcast apps."""
    srv = get_server()
    base_url = _resolve_public_base_url(request)
    return {"url": f"{base_url}/feed.xml?token={srv.LISTEN_TOKEN}"}


@router.get("/feed.xml")
def get_podcast_rss_feed(request: Request) -> Response:
    """Serve an RSS 2.0 + iTunes Podcast feed for all synthesized episodes."""
    srv = get_server()
    role = get_auth_role(
        request,
        srv.SESSION_TOKEN,
        listen_token=srv.LISTEN_TOKEN,
        allow_query_token=True,
        allow_query_admin=False,
    )
    if role is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    episodes = _load_all_timelines(srv.EPISODES_DIR)
    base_url = _resolve_public_base_url(request)
    rss_xml = episodes_to_rss_xml(episodes, base_url=base_url, token=srv.LISTEN_TOKEN)
    return Response(content=rss_xml, media_type="application/rss+xml; charset=utf-8")
