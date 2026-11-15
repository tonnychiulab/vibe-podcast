"""Tests for cross-episode show memory and Show Bible prompt injection."""

from __future__ import annotations

import json
from pathlib import Path

from vibe_podcast import scriptwriter, show_memory


def test_show_bible_defaults_and_custom_override(tmp_path: Path) -> None:
    default_bible = show_memory.load_show_bible(tmp_path)
    assert default_bible.show_name == "老馬麵攤"
    assert len(default_bible.banned_cliches) > 0

    custom = {
        "show_name": "老馬深夜麵攤",
        "host_a_persona": "專拆底層架構的老馬",
        "host_b_persona": "專問帳單多少錢的小陳",
        "recurring_callbacks": ["「先看 P99 延遲再說」"],
        "banned_cliches": ["歡迎收聽"],
    }
    (tmp_path / "show_bible.json").write_text(json.dumps(custom, ensure_ascii=False), encoding="utf-8")
    loaded = show_memory.load_show_bible(tmp_path)
    assert loaded.show_name == "老馬深夜麵攤"
    assert loaded.recurring_callbacks == ["「先看 P99 延遲再說」"]


def test_recent_episodes_memory_and_prompt_injection(tmp_path: Path) -> None:
    ep1 = tmp_path / "20261114-100000-ep1"
    ep2 = tmp_path / "20261114-200000-ep2"
    ep1.mkdir()
    ep2.mkdir()
    (ep1 / "timeline.json").write_text(
        json.dumps(
            {
                "title": "第一集：Redis 快取",
                "summary": "聊快取雪崩與熱點鍵。",
                "segments": [
                    {"chapter": "hook", "text": "開場"},
                    {"chapter": "takeaway", "text": "記得加隨機 TTL"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (ep2 / "timeline.json").write_text(
        json.dumps(
            {
                "title": "第二集：<script>alert(1)</script>Postgres 索引",
                "summary": "聊 B-Tree 與 GIN。",
                "guest_name": "阿明",
                "segments": [{"chapter": "takeaway", "text": "先看 EXPLAIN ANALYZE"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    recent = show_memory.load_recent_episodes_memory(tmp_path, limit=2)
    assert [r.episode_id for r in recent] == ["20261114-200000-ep2", "20261114-100000-ep1"]
    assert "<script>" not in recent[0].title
    assert "先看 EXPLAIN ANALYZE" in recent[0].takeaway

    ctx = show_memory.build_continuity_context(tmp_path, language="zh-TW", limit=2)
    assert "老馬麵攤" in ctx
    assert "第一集：Redis 快取" in ctx
    assert "記得加隨機 TTL" in ctx

    prompt = scriptwriter._build_prompt(
        source_text="測試技術文章內容",
        continuity_context=ctx,
    )
    assert "節目聖經與延續記憶" in prompt
    assert "第一集：Redis 快取" in prompt
    assert '"skeptical"' in prompt


def test_show_bible_save_format_scoping_and_english_callbacks(tmp_path: Path) -> None:
    saved = show_memory.save_show_bible(
        tmp_path,
        show_memory.ShowBible(
            show_name="測試技術麵攤",
            host_a_persona="愛追問的工程師",
            host_b_persona="講人話的架構師",
            recurring_callbacks=["先看基準測試再說"],
            banned_cliches=["歡迎收聽"],
        ),
    )
    assert saved.show_name == "測試技術麵攤"
    assert show_memory.load_show_bible(tmp_path).host_a_persona == "愛追問的工程師"

    # noodle_chat injects dynamic host names and personas
    noodle_ctx = show_memory.build_continuity_context(
        tmp_path,
        language="zh-TW",
        host_a="志明",
        host_b="春嬌",
        interview_format="noodle_chat",
    )
    assert "Speaker 1 (志明)" in noodle_ctx
    assert "愛追問的工程師" in noodle_ctx

    # debate keeps callbacks & banned cliches but skips noodle_chat host personas
    debate_ctx = show_memory.build_continuity_context(
        tmp_path,
        language="zh-TW",
        host_a="志明",
        host_b="春嬌",
        interview_format="debate",
    )
    assert "愛追問的工程師" not in debate_ctx
    assert "先看基準測試再說" in debate_ctx
    assert "歡迎收聽" in debate_ctx

    # English branch also includes recurring_callbacks
    en_ctx = show_memory.build_continuity_context(
        tmp_path,
        language="en-US",
        host_a="Alex",
        host_b="Sam",
        interview_format="noodle_chat",
    )
    assert "Recurring Show Callbacks" in en_ctx
    assert "先看基準測試再說" in en_ctx
