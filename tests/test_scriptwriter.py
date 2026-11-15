"""Tests for scriptwriter JSON parsing, prompt formatting, presets, and i18n."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibe_podcast.scriptwriter import DialogueTurn, PodcastScript, extract_json


def test_extract_json_handles_fences_and_prose():
    raw = 'Here is the script:\n```json\n{"title": "Test Ep", "summary": "S", "turns": []}\n```\nEnjoy!'
    parsed = extract_json(raw)
    assert parsed["title"] == "Test Ep"


def test_extract_json_invalid_fence_falls_back():
    raw = '```json\n{not: valid}\n```\n{"title": "ok", "summary": "s", "turns": []}'
    parsed = extract_json(raw)
    assert parsed["title"] == "ok"


def test_podcast_script_vibevoice_format():
    script = PodcastScript(
        title="VibeVoice Deep Dive",
        summary="Exploring 7.5Hz tokenizers.",
        host_a="Alex",
        host_b="Sam",
        turns=[
            DialogueTurn(speaker="Speaker 1", speaker_name="Alex", text="Welcome back!"),
            DialogueTurn(speaker="Speaker 2", speaker_name="Sam", text="Glad to be here."),
        ],
    )
    vv_text = script.to_vibevoice_text()
    assert vv_text == "Speaker 1: Welcome back!\nSpeaker 2: Glad to be here."


def test_i18n_meta_brand_version_and_credits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VIBEPODCAST_EPISODES_DIR", str(tmp_path / "episodes"))
    import importlib
    import vibe_podcast.server as srv
    from vibe_podcast import __version__
    from vibe_podcast.i18n import load_locale

    importlib.reload(srv)
    client = TestClient(srv.app, headers={"Authorization": f"Bearer {srv.SESSION_TOKEN}"})

    meta_zh = client.get("/api/meta?lang=zh-TW")
    assert meta_zh.status_code == 200
    data_zh = meta_zh.json()
    assert data_zh["version"] == __version__
    assert data_zh["brand"]["name"] == "老馬麵攤"
    assert "github.com/microsoft/VibeVoice" in data_zh["brand"]["credits_url"]

    meta_en = client.get("/api/meta?lang=en-US")
    assert meta_en.status_code == 200
    data_en = meta_en.json()
    assert data_en["locale"] == "en-US"
    assert "老馬麵攤" in data_en["brand"]["name"]

    zh_dict = load_locale("zh-TW")
    en_dict = load_locale("en-US")
    for section in ("brand", "ui", "status", "errors"):
        assert set(zh_dict[section].keys()) == set(en_dict[section].keys())

    assert len(data_zh["interview_formats"]) >= 4
    assert set(data_zh["chapter_labels"].keys()) == {"hook", "deep_dive", "challenge", "takeaway"}


def test_podcast_script_normalizes_hallucinated_speaker_names():
    script = PodcastScript.model_validate(
        {
            "title": "Test",
            "summary": "Summary",
            "host_a": "志明",
            "host_b": "春嬌",
            "guest_name": "Dr. Lin",
            "turns": [
                {"speaker": "Speaker 1", "speaker_name": "deep_dive", "text": "第一句", "chapter": "deep_dive"},
                {"speaker": "Speaker 2", "speaker_name": "challenge", "text": "第二句", "chapter": "challenge"},
                {"speaker": "Speaker 3", "speaker_name": "Speaker 3", "text": "第三句", "chapter": "takeaway"},
            ],
        }
    )
    assert [t.speaker_name for t in script.turns] == ["志明", "春嬌", "Dr. Lin"]
