"""Studio routes: health, metadata, presets, reports, file upload, drafting, and generation."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from vibe_podcast import __version__
from vibe_podcast.auth import get_auth_role, match_token_role, set_session_cookie
from vibe_podcast.connectors import SourceItem
from vibe_podcast.guardrails import (
    ContentSafetyError,
    check_prompt_injection,
    check_text_safety,
    enforce_script_guardrails,
)
from vibe_podcast.i18n import DEFAULT_LOCALE, get_app_meta, get_error_message
from vibe_podcast.interview import InterviewPreset, register_custom_preset
from vibe_podcast.preflight import run_preflight
from vibe_podcast.routes import get_server
from vibe_podcast.schemas import (
    CustomPresetPayload,
    GenerateRequest,
    LoginRequest,
    ScriptDraftResponse,
    SynthesizeScriptRequest,
    UploadSourceRequest,
)
from vibe_podcast.scriptwriter import SOURCE_MAX_CHARS
from vibe_podcast.show_memory import ShowBible, load_show_bible, save_show_bible
from vibe_podcast.synthesizer import AVAILABLE_VOICES, SynthesisResult, synthesize_podcast

logger = logging.getLogger("vibe_podcast.routes.studio")

router = APIRouter()


@router.get("/api/health")
def health(request: Request, probe_network: bool = False) -> dict:
    srv = get_server()
    if get_auth_role(request, srv.SESSION_TOKEN, allow_query_token=False) != "admin":
        return {"status": "ok", "version": __version__}
    episodes_dir = srv.EPISODES_DIR
    report = run_preflight(episodes_dir=episodes_dir, probe_network=probe_network)
    return {
        "status": "ok" if report.ok else "degraded",
        "version": __version__,
        "episodes_dir": str(episodes_dir),
        "ffmpeg_available": report.ffmpeg_available,
        "preflight": report.model_dump(),
    }


@router.post("/api/login")
def login_with_token(payload: LoginRequest, request: Request) -> JSONResponse:
    """Authenticate with a session or listener token via POST JSON body without URL query exposure."""
    srv = get_server()
    token = payload.token.strip()
    role = match_token_role(token, srv.SESSION_TOKEN, srv.LISTEN_TOKEN)
    if role is None:
        raise HTTPException(status_code=401, detail="Invalid session token")
    resp = JSONResponse({"ok": True, "role": role})
    set_session_cookie(resp, token, request)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/api/meta")
def app_meta(request: Request, lang: str = DEFAULT_LOCALE) -> dict:
    """Return localized UI strings, brand metadata, version, role, and available TTS voices."""
    meta = get_app_meta(lang)
    meta["voices"] = AVAILABLE_VOICES
    meta["role"] = getattr(request.state, "auth_role", "admin")
    return meta


@router.post("/api/presets/import")
def import_custom_preset(payload: CustomPresetPayload, lang: str = DEFAULT_LOCALE) -> dict:
    """Validate and register a custom interview preset from JSON template."""
    for required_val in (
        payload.name_zh,
        payload.host_a_role,
        payload.host_b_role,
        payload.pacing_instruction,
    ):
        if not required_val.strip():
            raise HTTPException(status_code=422, detail="Preset fields must not be blank")

    try:
        for field_val, ctx in [
            (payload.name_zh, "範本中文名稱"),
            (payload.name_en or "", "範本英文名稱"),
            (payload.description_zh, "範本中文說明"),
            (payload.description_en, "範本英文說明"),
            (payload.host_a_role, "主持人 A 人設"),
            (payload.host_b_role, "主持人 B 人設"),
            (payload.guest_role, "來賓人設"),
            (payload.pacing_instruction, "節奏指示"),
        ]:
            if field_val:
                check_text_safety(field_val, context=ctx)
                check_prompt_injection(field_val, context=ctx)
    except ContentSafetyError as exc:
        raise HTTPException(
            status_code=422,
            detail=get_error_message("safety_blocked", lang, detail=str(exc)),
        ) from exc

    preset = InterviewPreset(
        id=payload.id.strip(),
        name_zh=payload.name_zh.strip(),
        name_en=(payload.name_en or payload.name_zh).strip(),
        description_zh=payload.description_zh.strip(),
        description_en=(payload.description_en or payload.description_zh).strip(),
        host_a_role=payload.host_a_role.strip(),
        host_b_role=payload.host_b_role.strip(),
        guest_role=payload.guest_role.strip() or "Tech specialist guest",
        pacing_instruction=payload.pacing_instruction.strip(),
    )
    try:
        register_custom_preset(preset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"imported": preset.id, "meta": get_app_meta(lang)}


@router.get("/api/show-bible")
def get_show_bible() -> dict:
    """Return the persisted Show Bible configuration (or defaults)."""
    return load_show_bible(get_server().EPISODES_DIR).model_dump()


@router.put("/api/show-bible")
def update_show_bible(payload: ShowBible, lang: str = DEFAULT_LOCALE) -> dict:
    """Validate and persist Show Bible configuration to episodes/show_bible.json."""
    show_name = payload.show_name.strip()
    if not show_name:
        raise HTTPException(status_code=422, detail="show_name must not be blank")

    callbacks = [c.strip() for c in payload.recurring_callbacks if c and c.strip()][:12]
    cliches = [c.strip() for c in payload.banned_cliches if c and c.strip()][:20]

    try:
        for val, ctx in [
            (show_name, "節目名稱"),
            (payload.host_a_persona, "主持人 A 長期人設"),
            (payload.host_b_persona, "主持人 B 長期人設"),
            *[(c, "節目招牌老梗") for c in callbacks],
            *[(c, "禁用罐頭詞") for c in cliches],
        ]:
            if val.strip():
                check_text_safety(val, context=ctx)
                check_prompt_injection(val, context=ctx)
    except ContentSafetyError as exc:
        raise HTTPException(
            status_code=422,
            detail=get_error_message("safety_blocked", lang, detail=str(exc)),
        ) from exc

    clean_bible = ShowBible(
        show_name=show_name,
        host_a_persona=payload.host_a_persona.strip(),
        host_b_persona=payload.host_b_persona.strip(),
        recurring_callbacks=callbacks,
        banned_cliches=cliches,
    )
    saved = save_show_bible(get_server().EPISODES_DIR, clean_bible)
    return saved.model_dump()


@router.get("/api/reports", response_model=List[SourceItem])
def list_reports() -> List[SourceItem]:
    """List discoverable source reports from registered connectors."""
    return get_server().connector_registry.list_all_items()


MAX_PDF_PAGES = 100
MAX_EXTRACTED_CHARS = 250_000


def _upload_msg(ui_lang: str, zh: str, en: str) -> str:
    return en if (ui_lang or "").lower().startswith("en") else zh


@router.post("/api/upload-source")
def upload_source_file(req: UploadSourceRequest) -> dict:
    """Extract safe plain text from an uploaded .md, .txt, or .pdf file (base64)."""
    ext = Path(req.filename).suffix.lower()
    if ext not in {".md", ".txt", ".pdf"}:
        raise HTTPException(
            status_code=400,
            detail=_upload_msg(req.ui_lang, "僅支援 .md、.txt 或 .pdf 檔案", "Only .md, .txt, or .pdf files are supported."),
        )
    try:
        raw_bytes = base64.b64decode(req.content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=_upload_msg(req.ui_lang, "無效的檔案編碼格式", "Invalid base64 payload."),
        ) from exc
    if len(raw_bytes) > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=_upload_msg(req.ui_lang, "檔案大小不得超過 5 MB", "File size must not exceed 5 MB."),
        )

    extracted = ""
    if ext in {".md", ".txt"}:
        if b"\x00" in raw_bytes:
            raise HTTPException(
                status_code=422,
                detail=_upload_msg(req.ui_lang, "不支援二進位檔案格式", "Binary files are not supported."),
            )
        decoded_ok = False
        for enc in ("utf-8", "cp950", "big5"):
            try:
                extracted = raw_bytes.decode(enc).strip()
                decoded_ok = True
                break
            except UnicodeDecodeError:
                continue
        if not decoded_ok:
            raise HTTPException(
                status_code=422,
                detail=_upload_msg(req.ui_lang, "檔案編碼無法解析，請使用 UTF-8 文字檔", "Unable to decode text file; please upload UTF-8."),
            )
    else:
        from pypdf import PdfReader
        from pypdf.errors import LimitReachedError

        try:
            reader = PdfReader(io.BytesIO(raw_bytes))
            chunks: List[str] = []
            total_chars = 0
            for page in reader.pages[:MAX_PDF_PAGES]:
                part = (page.extract_text() or "").strip()
                if not part:
                    continue
                chunks.append(part)
                total_chars += len(part)
                if total_chars >= MAX_EXTRACTED_CHARS:
                    break
            extracted = "\n\n".join(chunks).strip()
        except LimitReachedError as exc:
            raise HTTPException(
                status_code=422,
                detail=_upload_msg(
                    req.ui_lang,
                    "PDF 結構過於複雜或超過安全限制，已拒絕解析",
                    "PDF exceeds safe structural decompression limits and was rejected.",
                ),
            ) from exc
        except Exception as exc:
            logger.warning("pypdf extraction failed, falling back to pdftotext: %s", exc)
            extracted = ""

        if not extracted:
            pdftotext_bin = shutil.which("pdftotext")
            if not pdftotext_bin:
                raise HTTPException(
                    status_code=422,
                    detail=_upload_msg(
                        req.ui_lang,
                        "無法從 PDF 擷取文字（請確認 PDF 含可選取文字而非純掃描圖片）",
                        "Could not extract text from PDF (ensure it contains selectable text, not only scanned images).",
                    ),
                )
            fd, tmp_pdf = tempfile.mkstemp(suffix=".pdf")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(raw_bytes)
                max_stdout_bytes = MAX_EXTRACTED_CHARS * 4
                deadline = time.monotonic() + 30.0
                proc = subprocess.Popen(
                    [pdftotext_bin, "-f", "1", "-l", str(MAX_PDF_PAGES), tmp_pdf, "-"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                buf = bytearray()
                capped = False
                try:
                    assert proc.stdout is not None
                    while len(buf) < max_stdout_bytes:
                        if time.monotonic() > deadline:
                            raise subprocess.TimeoutExpired(proc.args, 30)
                        chunk = proc.stdout.read(min(16_384, max_stdout_bytes - len(buf)))
                        if not chunk:
                            break
                        buf.extend(chunk)
                    if len(buf) >= max_stdout_bytes:
                        capped = True
                        proc.kill()
                    remaining_wait = max(0.5, deadline - time.monotonic())
                    proc.wait(timeout=remaining_wait)
                    if not capped and proc.returncode != 0:
                        raise subprocess.CalledProcessError(proc.returncode, proc.args)
                    extracted = bytes(buf).decode("utf-8", errors="replace").strip()
                except Exception:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
                    raise
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail=_upload_msg(req.ui_lang, "PDF 文字擷取失敗", "Failed to extract text from PDF."),
                ) from exc
            finally:
                Path(tmp_pdf).unlink(missing_ok=True)

    if len(extracted) > MAX_EXTRACTED_CHARS:
        extracted = extracted[:MAX_EXTRACTED_CHARS].strip()

    if not extracted:
        raise HTTPException(
            status_code=422,
            detail=_upload_msg(req.ui_lang, "無法從檔案擷取到文字內容", "No text content could be extracted from the file."),
        )
    try:
        check_text_safety(extracted, context="上傳檔案內容")
    except ContentSafetyError as exc:
        raise HTTPException(
            status_code=422,
            detail=get_error_message("safety_blocked", req.ui_lang, detail=str(exc)),
        ) from exc
    return {"filename": Path(req.filename).name, "chars": len(extracted), "text": extracted}


def _synthesize_from_request(srv, script, req, on_progress=None) -> SynthesisResult:
    try:
        return synthesize_podcast(
            script=script,
            output_root=srv.EPISODES_DIR,
            episode_id=srv._make_episode_id(script.title),
            engine=req.engine,
            language=req.language,
            voice_map=req.voice_map,
            enable_loudnorm=req.enable_loudnorm,
            enable_sfx=req.enable_sfx,
            on_progress=on_progress,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/api/script", response_model=ScriptDraftResponse)
def draft_episode_script(req: GenerateRequest) -> ScriptDraftResponse:
    """Draft an editable PodcastScript without running TTS synthesis yet."""
    srv = get_server()
    srv._acquire_generation_slot(req.ui_lang)
    try:
        source_text = srv._resolve_generate_source(req)
        script = srv._draft_script_from_request(req, source_text)
        return ScriptDraftResponse(
            script=script,
            source_chars=len(source_text),
            truncated=req.long_text_mode == "truncate" and len(source_text) > SOURCE_MAX_CHARS,
            summarized=req.long_text_mode == "map_reduce" and len(source_text) > SOURCE_MAX_CHARS,
        )
    finally:
        srv._GENERATION_SEMAPHORE.release()


@router.post("/api/synthesize", response_model=SynthesisResult)
def synthesize_edited_script(req: SynthesizeScriptRequest) -> SynthesisResult:
    """Validate guardrails on a user-edited PodcastScript and synthesize audio."""
    srv = get_server()
    srv._acquire_generation_slot(req.ui_lang)
    try:
        try:
            clean_script = enforce_script_guardrails(req.script)
        except ContentSafetyError as exc:
            raise HTTPException(
                status_code=422,
                detail=get_error_message("safety_blocked", req.ui_lang, detail=str(exc)),
            ) from exc
        return _synthesize_from_request(srv, clean_script, req)
    finally:
        srv._GENERATION_SEMAPHORE.release()


@router.post("/api/generate", response_model=SynthesisResult)
def generate_episode(req: GenerateRequest) -> SynthesisResult:
    """Generate a dual-host podcast script and synthesize audio."""
    srv = get_server()
    srv._acquire_generation_slot(req.ui_lang)
    try:
        source_text = srv._resolve_generate_source(req)
        script = srv._draft_script_from_request(req, source_text)
        return _synthesize_from_request(srv, script, req)
    finally:
        srv._GENERATION_SEMAPHORE.release()


class _StreamCancelled(Exception):
    """Internal signal raised when an SSE client disconnects mid-generation."""


@router.post("/api/generate/stream")
async def generate_episode_stream(req: GenerateRequest) -> StreamingResponse:
    """Stream Server-Sent Events (SSE) progress while generating an episode."""
    srv = get_server()
    srv._acquire_generation_slot(req.ui_lang)
    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    cancel_event = threading.Event()

    def _emit(event: str, payload: dict) -> None:
        if cancel_event.is_set() or loop.is_closed():
            return
        line = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        try:
            loop.call_soon_threadsafe(queue.put_nowait, line)
        except RuntimeError:
            pass

    def _worker() -> None:
        try:
            _emit("progress", {"stage": "fetching", "current": 0, "total": 1})
            source_text = srv._resolve_generate_source(req)
            if cancel_event.is_set():
                raise _StreamCancelled()
            _emit("progress", {"stage": "scripting", "current": 0, "total": 1})
            script = srv._draft_script_from_request(req, source_text)
            if cancel_event.is_set():
                raise _StreamCancelled()

            def _on_tts_progress(stage: str, current: int, total: int) -> None:
                if cancel_event.is_set():
                    raise _StreamCancelled()
                _emit("progress", {"stage": stage, "current": current, "total": total})

            result = _synthesize_from_request(srv, script, req, on_progress=_on_tts_progress)
            _emit("done", result.model_dump())
        except _StreamCancelled:
            logger.info("SSE client disconnected; aborted remaining generation stages")
        except HTTPException as exc:
            _emit("error", {"detail": str(exc.detail)})
        except Exception:
            logger.exception("SSE episode generation failed")
            _emit(
                "error",
                {
                    "detail": (
                        "生成過程發生系統錯誤，請查看伺服器日誌。"
                        if req.ui_lang.startswith("zh")
                        else "Generation failed; check server logs for details."
                    )
                },
            )
        finally:
            srv._GENERATION_SEMAPHORE.release()
            if not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, None)
                except RuntimeError:
                    pass

    # Start the worker immediately upon slot acquisition so pre-iteration disconnects still release it.
    task = asyncio.create_task(asyncio.to_thread(_worker))

    async def _event_stream():
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            cancel_event.set()
            if task.done():
                await task

    return StreamingResponse(_event_stream(), media_type="text/event-stream")
