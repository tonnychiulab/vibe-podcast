"""Dual-host Podcast speech synthesizer supporting VibeVoice Remote and Edge-TTS dialogue."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Literal, Optional, TypeVar, cast

from vibe_podcast.audio_fx import (
    SynthesisResult,
    TimelineSegment,
    generate_silence_mp3 as _generate_silence_mp3,
    merge_turns_to_mp3 as _merge_turns_to_mp3,
    probe_duration_sec,
)
from vibe_podcast.scriptwriter import DialogueTurn, PodcastScript

logger = logging.getLogger("vibe_podcast.synthesizer")

DEFAULT_VOICES_ZH = {
    "Speaker 1": "zh-TW-YunJheNeural",
    "Speaker 2": "zh-TW-HsiaoChenNeural",
    "Speaker 3": "zh-TW-HsiaoYuNeural",
}

FALLBACK_VOICES_ZH = {
    "Speaker 1": ["zh-TW-YunJheNeural", "zh-CN-YunxiNeural", "zh-TW-HsiaoYuNeural"],
    "Speaker 2": ["zh-TW-HsiaoChenNeural", "zh-TW-HsiaoYuNeural", "zh-CN-XiaoxiaoNeural"],
    "Speaker 3": ["zh-TW-HsiaoYuNeural", "zh-CN-XiaoxiaoNeural", "zh-TW-HsiaoChenNeural"],
}

DEFAULT_VOICES_EN = {
    "Speaker 1": "en-US-AndrewMultilingualNeural",
    "Speaker 2": "en-US-AvaMultilingualNeural",
    "Speaker 3": "en-US-BrianMultilingualNeural",
}

AVAILABLE_VOICES: List[Dict[str, str]] = [
    {"id": "zh-TW-YunJheNeural", "label": "台灣男聲 · 雲哲 (YunJhe)", "lang": "zh-TW"},
    {"id": "zh-TW-HsiaoChenNeural", "label": "台灣女聲 · 曉臻 (HsiaoChen)", "lang": "zh-TW"},
    {"id": "zh-TW-HsiaoYuNeural", "label": "台灣女聲 · 曉雨 (HsiaoYu)", "lang": "zh-TW"},
    {"id": "zh-CN-YunxiNeural", "label": "中文男聲 · 雲希 (Yunxi)", "lang": "zh-CN"},
    {"id": "zh-CN-YunjianNeural", "label": "中文男聲 · 雲健 (Yunjian)", "lang": "zh-CN"},
    {"id": "zh-CN-XiaoxiaoNeural", "label": "中文女聲 · 曉曉 (Xiaoxiao)", "lang": "zh-CN"},
    {"id": "en-US-AndrewMultilingualNeural", "label": "US Male · Andrew", "lang": "en-US"},
    {"id": "en-US-AvaMultilingualNeural", "label": "US Female · Ava", "lang": "en-US"},
    {"id": "en-US-BrianMultilingualNeural", "label": "US Male · Brian", "lang": "en-US"},
    {"id": "en-US-EmmaMultilingualNeural", "label": "US Female · Emma", "lang": "en-US"},
]

ALLOWED_VOICE_IDS = frozenset(v["id"] for v in AVAILABLE_VOICES)


def resolve_voice_map(
    voice_map: Optional[Dict[str, str]], language: str = "zh-TW"
) -> Dict[str, str]:
    """Merge user-selected voices with language defaults, validating against allowlist."""
    base = dict(DEFAULT_VOICES_ZH if language.startswith("zh") else DEFAULT_VOICES_EN)
    if not voice_map:
        return base
    for spk in ("Speaker 1", "Speaker 2", "Speaker 3"):
        chosen = (voice_map.get(spk) or "").strip()
        if chosen:
            if chosen not in ALLOWED_VOICE_IDS:
                raise ValueError(f"Unsupported TTS voice: {chosen}")
            base[spk] = chosen
    return base

_CUE_PROSODY_MAP = {
    "enthusiastic": ("+8%", "+2Hz"),
    "excited": ("+10%", "+3Hz"),
    "urgent": ("+8%", "+1Hz"),
    "curious": ("+6%", "+2Hz"),
    "amused": ("+6%", "+2Hz"),
    "thoughtful": ("-2%", "-1Hz"),
    "calm": ("+0%", "-1Hz"),
    "serious": ("-2%", "-2Hz"),
    "skeptical": ("+2%", "-2Hz"),
    "challenge": ("+4%", "-1Hz"),
}


def _cue_to_prosody(cue: Optional[str]) -> tuple[str, str]:
    """Map an optional turn cue keyword to Edge-TTS (rate, pitch) adjustments."""
    if not cue:
        return ("+4%", "+0Hz")
    normalized = cue.strip().lower()
    for key, prosody in _CUE_PROSODY_MAP.items():
        if key in normalized:
            return prosody
    return ("+4%", "+0Hz")


_T = TypeVar("_T")


def _run_async(coro_factory: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Run an async coroutine from sync code even if an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro_factory())).result()


async def _edge_tts_to_file(
    text: str,
    voice: str,
    out_path: str,
    rate: str = "+4%",
    pitch: str = "+0Hz",
    fallback_voices: Optional[List[str]] = None,
) -> str:
    """Synthesize text to out_path and return the voice identifier actually used."""
    import edge_tts

    candidates = [voice] + [v for v in (fallback_voices or []) if v != voice]
    last_err: Optional[Exception] = None
    for candidate in candidates:
        for attempt in range(2):
            try:
                communicate = edge_tts.Communicate(text, candidate, rate=rate, pitch=pitch)
                await communicate.save(out_path)
                return candidate
            except Exception as exc:
                logger.warning("edge-tts failed with voice %s (try %d): %s", candidate, attempt + 1, exc)
                last_err = exc

    if sys.platform == "darwin" and shutil.which("say"):
        aiff_path = f"{out_path}.aiff"
        try:
            subprocess.run(
                ["say", "-o", aiff_path, "--", text],
                capture_output=True,
                check=True,
                timeout=30,
            )
            subprocess.run(
                ["ffmpeg", "-y", "-i", aiff_path, "-acodec", "libmp3lame", "-q:a", "2", out_path],
                capture_output=True,
                check=True,
                timeout=30,
            )
            return "macos-say"
        except Exception as say_exc:
            raise RuntimeError(f"All TTS voices failed: {last_err}; say fallback: {say_exc}")
        finally:
            Path(aiff_path).unlink(missing_ok=True)
    raise RuntimeError(f"All TTS voices failed: {last_err}")


def _finalize_synthesis(
    script: PodcastScript,
    episode_dir: Path,
    episode_id: str,
    engine: str,
    final_audio: Path,
    segments: List[TimelineSegment],
    language: str = "zh-TW",
    voice_map: Optional[Dict[str, str]] = None,
    enable_loudnorm: bool = True,
    enable_sfx: bool = False,
) -> SynthesisResult:
    """Probe audio duration, persist timeline.json, and build SynthesisResult."""
    total_duration = probe_duration_sec(str(final_audio))
    timeline_path = episode_dir / "timeline.json"
    result = SynthesisResult(
        episode_id=episode_id,
        title=script.title,
        summary=script.summary,
        host_a=script.host_a,
        host_b=script.host_b,
        guest_name=script.guest_name,
        interview_format=script.interview_format,
        engine=engine,
        language=language,
        voice_map=voice_map or {},
        enable_loudnorm=enable_loudnorm,
        enable_sfx=enable_sfx,
        audio_path=str(final_audio),
        timeline_path=str(timeline_path),
        duration_sec=total_duration,
        segments=segments,
    )
    timeline_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def _synthesize_edge_dialogue(
    script: PodcastScript,
    episode_dir: Path,
    episode_id: str,
    language: str = "zh-TW",
    pause_sec: float = 0.28,
    voice_map: Optional[Dict[str, str]] = None,
    enable_loudnorm: bool = True,
    enable_sfx: bool = False,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> SynthesisResult:
    """Synthesize dual-host dialogue turn-by-turn with Edge-TTS and merge via ffmpeg."""
    turns_dir = episode_dir / "turns"
    turns_dir.mkdir(parents=True, exist_ok=True)

    voices = resolve_voice_map(voice_map, language=language)
    total_turns = len(script.turns)
    locked_voice: Dict[str, str] = dict(voices)

    async def _run_all() -> List[Path]:
        paths: List[Path] = [turns_dir / f"turn_{idx:03d}.mp3" for idx in range(total_turns)]
        used_voices: Dict[int, str] = {}

        for idx, turn in enumerate(script.turns):
            if on_progress:
                on_progress("synthesizing", idx + 1, total_turns)
            out_file = paths[idx]
            preferred = locked_voice.get(turn.speaker, voices["Speaker 1"])
            fb = FALLBACK_VOICES_ZH.get(turn.speaker, []) if language.startswith("zh") else []
            rate, pitch = _cue_to_prosody(turn.cue)
            actual = await _edge_tts_to_file(
                turn.text, preferred, str(out_file), rate=rate, pitch=pitch, fallback_voices=fb
            )
            used_voices[idx] = actual

            if actual != preferred and actual != "macos-say":
                locked_voice[turn.speaker] = actual
                for prev_idx in range(idx):
                    prev_turn = script.turns[prev_idx]
                    if prev_turn.speaker == turn.speaker and used_voices.get(prev_idx) != actual:
                        prev_rate, prev_pitch = _cue_to_prosody(prev_turn.cue)
                        await _edge_tts_to_file(
                            prev_turn.text,
                            actual,
                            str(paths[prev_idx]),
                            rate=prev_rate,
                            pitch=prev_pitch,
                            fallback_voices=[],
                        )
                        used_voices[prev_idx] = actual
        return paths

    turn_files = _run_async(_run_all)

    if on_progress:
        on_progress("merging", total_turns, total_turns)

    final_audio, segments = _merge_turns_to_mp3(
        turns=script.turns,
        turn_files=turn_files,
        episode_dir=episode_dir,
        turns_dir=turns_dir,
        pause_sec=pause_sec,
        enable_loudnorm=enable_loudnorm,
        enable_sfx=enable_sfx,
    )

    return _finalize_synthesis(
        script=script,
        episode_dir=episode_dir,
        episode_id=episode_id,
        engine="edge_dialogue",
        final_audio=final_audio,
        segments=segments,
        language=language,
        voice_map=locked_voice,
        enable_loudnorm=enable_loudnorm,
        enable_sfx=enable_sfx,
    )


def resynthesize_turn(
    episode_dir: Path,
    episode_id: str,
    turn_index: int,
    new_text: str,
    new_cue: Optional[str] = None,
) -> SynthesisResult:
    """Re-synthesize a single dialogue turn in-place and re-merge episode audio."""
    from vibe_podcast.guardrails import _apply_soft_replacements, check_text_safety

    timeline_path = episode_dir / "timeline.json"
    if not timeline_path.is_file():
        raise FileNotFoundError("Episode timeline not found")

    existing = SynthesisResult.model_validate_json(
        timeline_path.read_text(encoding="utf-8")
    )
    if existing.engine != "edge_dialogue":
        raise ValueError("Single-turn re-synthesis is only supported for edge_dialogue episodes")
    if turn_index < 0 or turn_index >= len(existing.segments):
        raise IndexError("Turn index out of range")

    turns_dir = episode_dir / "turns"
    turn_files = [turns_dir / f"turn_{idx:03d}.mp3" for idx in range(len(existing.segments))]
    if not all(p.is_file() for p in turn_files):
        raise FileNotFoundError("Per-turn audio clips are missing for this episode")

    cleaned_text = _apply_soft_replacements(new_text.strip())
    if not cleaned_text:
        raise ValueError("Turn text must not be blank")
    check_text_safety(cleaned_text, context=f"第 {turn_index + 1} 輪對話")

    seg = existing.segments[turn_index]
    effective_cue = new_cue if new_cue is not None else seg.cue
    sanitized_voice_map = {k: v for k, v in existing.voice_map.items() if v != "macos-say"}
    voices = resolve_voice_map(sanitized_voice_map, language=existing.language)
    voice = voices.get(seg.speaker, voices["Speaker 1"])
    rate, pitch = _cue_to_prosody(effective_cue)
    fb = FALLBACK_VOICES_ZH.get(seg.speaker, []) if existing.language.startswith("zh") else []

    async def _regen() -> str:
        return await _edge_tts_to_file(
            cleaned_text,
            voice,
            str(turn_files[turn_index]),
            rate=rate,
            pitch=pitch,
            fallback_voices=fb,
        )

    _run_async(_regen)

    turns: List[DialogueTurn] = []
    for idx, s in enumerate(existing.segments):
        turns.append(
            DialogueTurn(
                speaker=cast(Literal["Speaker 1", "Speaker 2", "Speaker 3"], s.speaker),
                speaker_name=s.speaker_name,
                text=cleaned_text if idx == turn_index else s.text,
                cue=effective_cue if idx == turn_index else s.cue,
                chapter=cast(Optional[Literal["hook", "deep_dive", "challenge", "takeaway"]], s.chapter),
            )
        )

    script = PodcastScript(
        title=existing.title,
        summary=existing.summary,
        host_a=existing.host_a,
        host_b=existing.host_b,
        guest_name=existing.guest_name,
        interview_format=existing.interview_format,
        turns=turns,
    )
    (episode_dir / "vibevoice_script.txt").write_text(
        script.to_vibevoice_text(), encoding="utf-8"
    )

    final_audio, new_segments = _merge_turns_to_mp3(
        turns=turns,
        turn_files=turn_files,
        episode_dir=episode_dir,
        turns_dir=turns_dir,
        enable_loudnorm=existing.enable_loudnorm,
        enable_sfx=existing.enable_sfx,
    )
    return _finalize_synthesis(
        script=script,
        episode_dir=episode_dir,
        episode_id=episode_id,
        engine=existing.engine,
        final_audio=final_audio,
        segments=new_segments,
        language=existing.language,
        voice_map=voices,
        enable_loudnorm=existing.enable_loudnorm,
        enable_sfx=existing.enable_sfx,
    )


def _estimate_timeline_by_char_length(
    script: PodcastScript, total_duration: float
) -> List[TimelineSegment]:
    """Estimate turn timestamps proportionally by character count."""
    total_chars = max(sum(len(t.text) for t in script.turns), 1)
    cursor = 0.0
    segments: List[TimelineSegment] = []
    for idx, turn in enumerate(script.turns):
        slice_dur = total_duration * (len(turn.text) / total_chars)
        start_sec = round(cursor, 3)
        end_sec = round(min(total_duration, cursor + slice_dur), 3)
        segments.append(
            TimelineSegment(
                index=idx,
                speaker=turn.speaker,
                speaker_name=turn.speaker_name,
                text=turn.text,
                cue=turn.cue,
                chapter=turn.chapter,
                start_sec=start_sec,
                end_sec=end_sec,
            )
        )
        cursor = end_sec
    return segments


def _synthesize_vibevoice_remote(
    script: PodcastScript,
    episode_dir: Path,
    episode_id: str,
    remote_url: str,
) -> SynthesisResult:
    """Send formatted multi-speaker script to a remote VibeVoice GPU server."""
    speakers = [s for s in (script.host_a, script.host_b, script.guest_name) if s]
    payload = json.dumps(
        {
            "script": script.to_vibevoice_text(),
            "speakers": speakers,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{remote_url.rstrip('/')}/synthesize",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    final_audio = episode_dir / "podcast.wav"
    with urllib.request.urlopen(req, timeout=300) as resp:
        final_audio.write_bytes(resp.read())

    total_duration = probe_duration_sec(str(final_audio))
    segments = _estimate_timeline_by_char_length(script, total_duration)

    return _finalize_synthesis(
        script=script,
        episode_dir=episode_dir,
        episode_id=episode_id,
        engine="vibevoice_remote",
        final_audio=final_audio,
        segments=segments,
    )


def synthesize_podcast(
    script: PodcastScript,
    output_root: Path,
    episode_id: str,
    engine: Literal["auto", "edge_dialogue", "vibevoice_remote"] = "auto",
    language: str = "zh-TW",
    remote_url: Optional[str] = None,
    voice_map: Optional[Dict[str, str]] = None,
    enable_loudnorm: bool = True,
    enable_sfx: bool = False,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> SynthesisResult:
    """Synthesize a PodcastScript into an audio file and aligned timeline JSON."""
    episode_dir = Path(output_root) / episode_id
    episode_dir.mkdir(parents=True, exist_ok=True)

    (episode_dir / "vibevoice_script.txt").write_text(
        script.to_vibevoice_text(), encoding="utf-8"
    )

    resolved_remote = remote_url or os.environ.get("VIBEVOICE_REMOTE_URL")
    if engine == "vibevoice_remote" or (engine == "auto" and resolved_remote):
        if not resolved_remote:
            raise ValueError("VIBEVOICE_REMOTE_URL is required for vibevoice_remote engine")
        if os.environ.get("VIBEPODCAST_ALLOW_PRIVATE_REMOTE") != "1":
            from vibe_podcast.connectors import _validate_public_url

            _validate_public_url(resolved_remote)
        if on_progress:
            on_progress("synthesizing", 1, 1)
        return _synthesize_vibevoice_remote(script, episode_dir, episode_id, resolved_remote)

    return _synthesize_edge_dialogue(
        script=script,
        episode_dir=episode_dir,
        episode_id=episode_id,
        language=language,
        voice_map=voice_map,
        enable_loudnorm=enable_loudnorm,
        enable_sfx=enable_sfx,
        on_progress=on_progress,
    )
