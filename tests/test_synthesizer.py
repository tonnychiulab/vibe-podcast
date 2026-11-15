"""Tests for TTS synthesis, voice consistency, presets, and audio_fx."""

from __future__ import annotations

from pathlib import Path

import pytest

from vibe_podcast.scriptwriter import DialogueTurn, PodcastScript
from vibe_podcast.synthesizer import SynthesisResult, synthesize_podcast


def test_synthesize_podcast_edge_dialogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from vibe_podcast import synthesizer

    async def fake_edge_tts(text: str, voice: str, out_path: str, rate: str = "+4%", **kwargs):
        synthesizer._generate_silence_mp3(0.4, out_path)
        return voice

    monkeypatch.setattr(synthesizer, "_edge_tts_to_file", fake_edge_tts)

    script = PodcastScript(
        title="Unit Test Ep",
        summary="Short audio test.",
        host_a="Alex",
        host_b="Sam",
        turns=[
            DialogueTurn(speaker="Speaker 1", speaker_name="Alex", text="測試第一句。"),
            DialogueTurn(speaker="Speaker 2", speaker_name="Sam", text="測試第二句。"),
        ],
    )
    result = synthesize_podcast(
        script=script,
        output_root=tmp_path,
        episode_id="test-ep-01",
        engine="edge_dialogue",
        language="zh-TW",
    )
    assert isinstance(result, SynthesisResult)
    assert Path(result.audio_path).exists()
    assert Path(result.timeline_path).exists()
    assert (tmp_path / "test-ep-01" / "vibevoice_script.txt").exists()
    assert result.duration_sec > 0.5
    assert len(result.segments) == 2
    assert result.segments[0].start_sec == 0.0
    assert result.segments[1].start_sec > result.segments[0].end_sec


def test_voice_consistency_when_fallback_triggered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from vibe_podcast import synthesizer

    calls = []

    async def flaky_edge_tts(text: str, voice: str, out_path: str, rate: str = "+4%", **kwargs):
        calls.append((text, voice))
        actual = "zh-CN-YunxiNeural" if text == "第三句觸發備援" and voice == "zh-TW-YunJheNeural" else voice
        synthesizer._generate_silence_mp3(0.3, out_path)
        return actual

    monkeypatch.setattr(synthesizer, "_edge_tts_to_file", flaky_edge_tts)

    script = PodcastScript(
        title="Fallback Lock Test",
        summary="Ensure speaker keeps a single voice.",
        host_a="Alex",
        host_b="Sam",
        turns=[
            DialogueTurn(speaker="Speaker 1", speaker_name="Alex", text="第一句正常"),
            DialogueTurn(speaker="Speaker 2", speaker_name="Sam", text="第二句女生"),
            DialogueTurn(speaker="Speaker 1", speaker_name="Alex", text="第三句觸發備援"),
        ],
    )
    synthesizer.synthesize_podcast(
        script=script,
        output_root=tmp_path,
        episode_id="lock-test",
        engine="edge_dialogue",
        language="zh-TW",
    )
    assert ("第一句正常", "zh-CN-YunxiNeural") in calls


def test_interview_presets_and_three_speaker_chapters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from vibe_podcast import synthesizer
    from vibe_podcast.interview import get_preset

    assert get_preset("debate").id == "debate"
    assert get_preset("unknown_format").id == "noodle_chat"

    async def fake_edge_tts(text: str, voice: str, out_path: str, rate: str = "+4%", **kwargs):
        synthesizer._generate_silence_mp3(0.3, out_path)
        return voice

    monkeypatch.setattr(synthesizer, "_edge_tts_to_file", fake_edge_tts)

    script = PodcastScript(
        title="Three Speaker Roundtable",
        summary="Testing chapter structure and guest speaker.",
        host_a="Alex",
        host_b="Sam",
        guest_name="Dr. Lin",
        interview_format="deep_dive",
        turns=[
            DialogueTurn(speaker="Speaker 1", speaker_name="Alex", chapter="hook", text="開場問題"),
            DialogueTurn(speaker="Speaker 2", speaker_name="Sam", chapter="deep_dive", text="原理拆解"),
            DialogueTurn(speaker="Speaker 3", speaker_name="Dr. Lin", chapter="challenge", text="來賓回應"),
            DialogueTurn(speaker="Speaker 1", speaker_name="Alex", chapter="takeaway", text="總結下課"),
        ],
    )
    res = synthesizer.synthesize_podcast(
        script=script,
        output_root=tmp_path,
        episode_id="three-speaker-ep",
        engine="edge_dialogue",
        language="zh-TW",
    )
    assert res.guest_name == "Dr. Lin"
    assert res.interview_format == "deep_dive"
    assert [seg.chapter for seg in res.segments] == ["hook", "deep_dive", "challenge", "takeaway"]
