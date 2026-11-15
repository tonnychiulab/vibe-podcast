"""Tests for cue-driven conversational pacing and overlap mixing in audio_fx."""

from __future__ import annotations

import subprocess
from pathlib import Path

from vibe_podcast.audio_fx import (
    compute_turn_gap_sec,
    generate_silence_mp3,
    merge_turns_to_mp3,
    probe_duration_sec,
    trim_edge_silence,
)
from vibe_podcast.scriptwriter import DialogueTurn


def test_compute_turn_gap_sec_pacing_and_clamping() -> None:
    # Challenge / excited cues produce snappy negative overlap
    assert compute_turn_gap_sec("thoughtful", "challenge", prev_dur=2.0, next_dur=2.0) == -0.12
    assert compute_turn_gap_sec("calm", "excited", prev_dur=2.0, next_dur=2.0) == -0.12
    assert compute_turn_gap_sec("calm", "urgent", prev_dur=2.0, next_dur=2.0) == -0.12
    # Same speaker never overlaps themselves
    assert compute_turn_gap_sec("calm", "challenge", prev_dur=2.0, next_dur=2.0, same_speaker=True) == 0.12
    # Very short clip clamps overlap so it never swallows the turn
    short_gap = compute_turn_gap_sec("calm", "challenge", prev_dur=0.2, next_dur=2.0)
    assert -0.08 <= short_gap < 0.0
    # Reflective cues produce a longer beat
    assert compute_turn_gap_sec("excited", "thoughtful", default_pause=0.25) == 0.38
    # Curious / skeptical cues produce a quick beat
    assert compute_turn_gap_sec("calm", "curious") == 0.08
    assert compute_turn_gap_sec("calm", "skeptical") == 0.08


def test_trim_edge_silence_preserves_pure_silence_and_trims_padded_speech(tmp_path: Path) -> None:
    pure_silence = tmp_path / "silence.mp3"
    generate_silence_mp3(0.8, str(pure_silence))
    kept = trim_edge_silence(pure_silence, tmp_path / "silence_trimmed.mp3")
    assert kept == pure_silence

    # Build 0.4s silence + 0.6s tone + 0.6s silence = 1.6s clip
    padded_tone = tmp_path / "padded_tone.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono:d=0.4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=24000:duration=0.6",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=mono:d=0.6",
            "-filter_complex",
            "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]",
            "-map",
            "[out]",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(padded_tone),
        ],
        capture_output=True,
        check=True,
    )
    trimmed_out = tmp_path / "padded_trimmed.mp3"
    result = trim_edge_silence(padded_tone, trimmed_out)
    assert result == trimmed_out
    trimmed_dur = probe_duration_sec(str(result))
    assert 0.55 <= trimmed_dur <= 0.90


def test_merge_turns_to_mp3_with_overlap_and_sfx(tmp_path: Path) -> None:
    turns_dir = tmp_path / "turns"
    turns_dir.mkdir()
    t0 = turns_dir / "000.mp3"
    t1 = turns_dir / "001.mp3"
    t2 = turns_dir / "002.mp3"
    generate_silence_mp3(0.8, str(t0))
    generate_silence_mp3(0.8, str(t1))
    generate_silence_mp3(0.8, str(t2))

    turns = [
        DialogueTurn(speaker="Speaker 1", speaker_name="老馬", chapter="hook", cue="calm", text="開場"),
        DialogueTurn(speaker="Speaker 2", speaker_name="小陳", chapter="hook", cue="challenge", text="等一下"),
        DialogueTurn(speaker="Speaker 1", speaker_name="老馬", chapter="deep_dive", cue="thoughtful", text="進正題"),
    ]

    final_mp3, segments = merge_turns_to_mp3(
        turns=turns,
        turn_files=[t0, t1, t2],
        episode_dir=tmp_path,
        turns_dir=turns_dir,
        enable_loudnorm=False,
        enable_sfx=True,
    )
    assert final_mp3.is_file()
    assert len(segments) == 3
    # Turn 1 overlaps Turn 0 because cue="challenge"
    assert segments[1].start_sec < segments[0].end_sec
    # Turn 2 is after chapter chime, so it starts cleanly after Turn 1 ends
    assert segments[2].start_sec > segments[1].end_sec
    total_dur = probe_duration_sec(str(final_mp3))
    assert total_dur >= segments[-1].end_sec - 0.1
