"""FFmpeg audio processing, timeline models, silence/SFX synthesis, and loudnorm merging."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from vibe_podcast.scriptwriter import DialogueTurn

logger = logging.getLogger("vibe_podcast.audio_fx")


class TimelineSegment(BaseModel):
    """A single time-aligned dialogue segment in the generated podcast."""

    index: int
    speaker: str
    speaker_name: str
    text: str
    cue: Optional[str] = None
    chapter: Optional[str] = None
    start_sec: float = Field(..., allow_inf_nan=False)
    end_sec: float = Field(..., allow_inf_nan=False)


class SynthesisResult(BaseModel):
    """Output metadata from synthesizing a podcast episode."""

    episode_id: str
    title: str
    summary: str
    host_a: str
    host_b: str
    guest_name: Optional[str] = None
    interview_format: str = "noodle_chat"
    engine: str
    language: str = "zh-TW"
    voice_map: Dict[str, str] = Field(default_factory=dict)
    enable_loudnorm: bool = True
    enable_sfx: bool = False
    audio_path: str
    timeline_path: str
    duration_sec: float = Field(..., allow_inf_nan=False)
    segments: List[TimelineSegment]


def probe_duration_sec(audio_path: str) -> float:
    """Measure audio file duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
    return round(float(proc.stdout.strip()), 3)


def generate_silence_mp3(duration_sec: float, out_path: str) -> None:
    """Generate a silent MP3 clip of given duration using ffmpeg."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=24000:cl=mono",
        "-t",
        f"{duration_sec:.3f}",
        "-q:a",
        "9",
        "-acodec",
        "libmp3lame",
        out_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=30)


def generate_transition_chime_mp3(out_path: str) -> None:
    """Generate a gentle two-tone chapter transition chime MP3 using ffmpeg."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=523.25:sample_rate=24000:duration=0.18",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=659.25:sample_rate=24000:duration=0.26",
        "-filter_complex",
        "[0:a]volume=0.14,afade=t=in:st=0:d=0.02,afade=t=out:st=0.12:d=0.06[a0];"
        "[1:a]volume=0.14,afade=t=in:st=0:d=0.02,afade=t=out:st=0.16:d=0.10[a1];"
        "[a0][a1]concat=n=2:v=0:a=1[out]",
        "-map",
        "[out]",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "4",
        out_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=30)


_SNAPPY_OVERLAP_CUES = frozenset({"challenge", "excited", "enthusiastic", "urgent"})
_QUICK_BEAT_CUES = frozenset({"curious", "skeptical", "amused", "chuckling", "animated"})
_REFLECTIVE_CUES = frozenset({"thoughtful", "serious", "calm"})

_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)")


def _cue_matches(cue: str, vocab: frozenset[str]) -> bool:
    return cue in vocab or any(word in cue for word in vocab)


def compute_turn_gap_sec(
    prev_cue: Optional[str],
    next_cue: Optional[str],
    default_pause: float = 0.28,
    prev_dur: float = 1.0,
    next_dur: float = 1.0,
    same_speaker: bool = False,
) -> float:
    """Compute inter-turn gap in seconds; negative values create natural conversational overlap."""
    p_cue = (prev_cue or "").strip().lower()
    n_cue = (next_cue or "").strip().lower()

    if _cue_matches(n_cue, _SNAPPY_OVERLAP_CUES):
        raw_gap = -0.12
    elif _cue_matches(n_cue, _QUICK_BEAT_CUES):
        raw_gap = 0.08
    elif _cue_matches(p_cue, _REFLECTIVE_CUES) or _cue_matches(n_cue, _REFLECTIVE_CUES):
        raw_gap = max(default_pause, 0.38)
    else:
        raw_gap = default_pause

    if raw_gap < 0:
        if same_speaker:
            return 0.12
        max_overlap = min(0.15, 0.35 * min(max(prev_dur, 0.0), max(next_dur, 0.0)))
        return round(-min(abs(raw_gap), max_overlap), 3)
    return round(raw_gap, 3)


def trim_edge_silence(
    in_path: Path,
    out_path: Path,
    pad_head_sec: float = 0.04,
    pad_tail_sec: float = 0.06,
) -> Path:
    """Trim excess leading/trailing silence from speech clips while preserving pure-silence test clips."""
    try:
        dur = probe_duration_sec(str(in_path))
        if dur < 0.25:
            return in_path

        proc = subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(in_path),
                "-af",
                "silencedetect=noise=-38dB:d=0.04",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        stderr = proc.stderr or ""
        starts = [float(m.group(1)) for m in _SILENCE_START_RE.finditer(stderr)]
        ends = [float(m.group(1)) for m in _SILENCE_END_RE.finditer(stderr)]
        if not starts:
            return in_path

        # Pure-silence clip (one continuous silence block spanning start to end)
        if starts[0] <= 0.03 and (not ends or (len(starts) == 1 and dur - ends[0] <= 0.06)):
            return in_path

        trim_start = 0.0
        if starts[0] <= 0.03 and ends:
            trim_start = max(0.0, ends[0] - pad_head_sec)

        trim_end = dur
        if len(starts) > len(ends):
            trim_end = min(dur, starts[-1] + pad_tail_sec)
        elif ends and (dur - ends[-1]) <= 0.06:
            trim_end = min(dur, starts[-1] + pad_tail_sec)

        if (trim_end - trim_start) < 0.15:
            return in_path
        if trim_start < 0.03 and (dur - trim_end) < 0.05:
            return in_path

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{trim_start:.3f}",
                "-to",
                f"{trim_end:.3f}",
                "-i",
                str(in_path),
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(out_path),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        if out_path.is_file() and probe_duration_sec(str(out_path)) >= 0.10:
            return out_path
    except Exception as exc:
        logger.debug("Edge silence trim skipped for %s: %s", in_path, exc)
    return in_path


def merge_turns_to_mp3(
    turns: List[DialogueTurn],
    turn_files: List[Path],
    episode_dir: Path,
    turns_dir: Path,
    pause_sec: float = 0.28,
    enable_loudnorm: bool = True,
    enable_sfx: bool = False,
    output_path: Optional[Path] = None,
) -> tuple[Path, List[TimelineSegment]]:
    """Mix per-turn MP3 clips with cue-driven pacing/overlaps, chapter chimes, and loudnorm."""
    if not turns or len(turns) != len(turn_files):
        raise ValueError("turns and turn_files must be non-empty and match in length")

    chime_path = turns_dir / "chime.mp3"
    chime_sec = 0.0
    if enable_sfx:
        if not chime_path.is_file() or chime_path.stat().st_size == 0:
            generate_transition_chime_mp3(str(chime_path))
        chime_sec = probe_duration_sec(str(chime_path))

    final_audio = output_path or (episode_dir / "podcast.mp3")
    with tempfile.TemporaryDirectory(prefix="vp_trim_") as trim_tmp:
        trim_dir = Path(trim_tmp)
        effective_files = [
            trim_edge_silence(turn_file, trim_dir / f"turn_{idx:03d}.mp3")
            for idx, turn_file in enumerate(turn_files)
        ]

        mix_inputs: List[tuple[Path, float]] = []
        segments: List[TimelineSegment] = []
        cursor = 0.0
        prev_chapter: Optional[str] = None
        prev_cue: Optional[str] = None
        prev_speaker: Optional[str] = None
        prev_dur = 0.0
        prev_start_sec = -1.0

        for idx, (turn, clip_file) in enumerate(zip(turns, effective_files)):
            dur = probe_duration_sec(str(clip_file))
            chapter_changed = (
                enable_sfx
                and idx > 0
                and turn.chapter
                and prev_chapter
                and turn.chapter != prev_chapter
            )

            if chapter_changed:
                chime_start = round(cursor + 0.12, 3)
                mix_inputs.append((chime_path.resolve(), chime_start))
                cursor = round(chime_start + chime_sec + 0.12, 3)
                start_sec = cursor
            elif idx == 0:
                start_sec = 0.0
            else:
                gap = compute_turn_gap_sec(
                    prev_cue=prev_cue,
                    next_cue=turn.cue,
                    default_pause=pause_sec,
                    prev_dur=prev_dur,
                    next_dur=dur,
                    same_speaker=(prev_speaker == turn.speaker),
                )
                start_sec = round(max(0.0, prev_start_sec + 0.05, cursor + gap), 3)

            end_sec = round(start_sec + dur, 3)
            mix_inputs.append((clip_file.resolve(), start_sec))
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
            prev_chapter = turn.chapter
            prev_cue = turn.cue
            prev_speaker = turn.speaker
            prev_dur = dur
            prev_start_sec = start_sec

        input_args: List[str] = []
        filter_parts: List[str] = []
        mix_labels: List[str] = []
        for i, (clip_path, start_sec) in enumerate(mix_inputs):
            input_args.extend(["-i", str(clip_path)])
            delay_ms = max(0, int(round(start_sec * 1000)))
            norm_prefix = f"[{i}:a]aresample=24000,aformat=channel_layouts=mono"
            if delay_ms > 0:
                filter_parts.append(f"{norm_prefix},adelay={delay_ms}|all=1[a{i}]")
            else:
                filter_parts.append(f"{norm_prefix}[a{i}]")
            mix_labels.append(f"[a{i}]")

        if len(mix_inputs) == 1:
            filter_parts.append("[a0]anull[mixed]")
        else:
            filter_parts.append(
                f"{''.join(mix_labels)}amix=inputs={len(mix_inputs)}:normalize=0:dropout_transition=0,"
                "alimiter=limit=0.95[mixed]"
            )

        base_filter = ";".join(filter_parts)
        fd, tmp_name = tempfile.mkstemp(dir=str(episode_dir), prefix=".podcast_", suffix=".tmp.mp3")
        os.close(fd)
        tmp_audio = Path(tmp_name)
        tail_cmd = ["-c:a", "libmp3lame", "-q:a", "2", str(tmp_audio)]

        try:
            if enable_loudnorm:
                try:
                    loudnorm_filter = f"{base_filter};[mixed]loudnorm=I=-16:TP=-1.5:LRA=11[out]"
                    subprocess.run(
                        ["ffmpeg", "-y", *input_args, "-filter_complex", loudnorm_filter, "-map", "[out]", *tail_cmd],
                        capture_output=True,
                        check=True,
                        timeout=120,
                    )
                    os.replace(tmp_audio, final_audio)
                    return final_audio, segments
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                    logger.warning("ffmpeg loudnorm failed; falling back to plain mix: %s", getattr(exc, "stderr", exc))

            plain_filter = f"{base_filter};[mixed]anull[out]"
            subprocess.run(
                ["ffmpeg", "-y", *input_args, "-filter_complex", plain_filter, "-map", "[out]", *tail_cmd],
                capture_output=True,
                check=True,
                timeout=120,
            )
            os.replace(tmp_audio, final_audio)
            return final_audio, segments
        except Exception:
            tmp_audio.unlink(missing_ok=True)
            raise
