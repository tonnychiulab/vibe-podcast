"""Cross-episode show memory and LaoMa Noodle Stand Show Bible loader."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("vibe_podcast.show_memory")


class ShowBible(BaseModel):
    """Persistent persona, recurring callbacks, and anti-cliché rules for the show."""

    show_name: str = Field(default="老馬麵攤", min_length=1, max_length=80)
    host_a_persona: str = Field(
        default="好奇心強、務實的全端工程師，代表聽眾追問「這到底怎麼落地、成本多少」，並偶爾吐槽產業 hype。",
        max_length=300,
    )
    host_b_persona: str = Field(
        default="技術老饕、資深系統架構師，毒舌但務實，喜歡用煮麵、街邊攤與廚房火候比喻複雜架構。",
        max_length=300,
    )
    recurring_callbacks: List[str] = Field(
        default_factory=lambda: [
            "「先別管那些花俏的，麵先下鍋再說」",
            "「這架構跟加了太多味精的湯頭一樣，第一口很爽，後面口渴」",
        ],
        max_length=12,
    )
    banned_cliches: List[str] = Field(
        default_factory=lambda: [
            "歡迎來到本集節目",
            "今天我們要來聊聊",
            "總而言之，這是一項令人興奮的技術",
            "讓我們拭目以待",
        ],
        max_length=20,
    )

    @field_validator("recurring_callbacks", "banned_cliches")
    @classmethod
    def _bound_string_items(cls, items: List[str]) -> List[str]:
        return [item.strip()[:120] for item in items if item and item.strip()]


class EpisodeMemoryItem(BaseModel):
    """Compact summary of a past episode used for cross-episode continuity."""

    episode_id: str
    title: str
    summary: str
    guest_name: Optional[str] = None
    interview_format: str = "noodle_chat"
    takeaway: str = ""


_MAX_MEMORY_JSON_BYTES = 512 * 1024


def _is_safe_regular_file(path: Path, base_dir: Path) -> bool:
    """Return True if path is a non-symlink regular file under base_dir within size limits."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        resolved_base = base_dir.resolve()
        resolved_target = path.resolve()
        if not resolved_target.is_relative_to(resolved_base):
            return False
        return resolved_target.stat().st_size <= _MAX_MEMORY_JSON_BYTES
    except OSError:
        return False


def _sanitize_memory_text(value: str, limit: int = 220) -> str:
    """Strip control/tag-like sequences and collapse whitespace for safe prompt injection."""
    no_ctrl = re.sub(r"[\x00-\x1f\x7f]+", " ", value or "")
    cleaned = re.sub(r"<[^>]*>", "", no_ctrl)
    return " ".join(cleaned.splitlines()).strip()[:limit]


def load_show_bible(episodes_dir: Path) -> ShowBible:
    """Load `episodes_dir/show_bible.json` if present, otherwise return defaults."""
    bible_path = episodes_dir / "show_bible.json"
    if not _is_safe_regular_file(bible_path, episodes_dir):
        return ShowBible()
    try:
        data = json.loads(bible_path.read_text(encoding="utf-8"))
        return ShowBible.model_validate(data)
    except Exception as exc:
        logger.warning("Failed to load show_bible.json (%s); using defaults.", exc)
        return ShowBible()


def save_show_bible(episodes_dir: Path, bible: ShowBible) -> ShowBible:
    """Persist `bible` to `episodes_dir/show_bible.json` atomically."""
    validated = ShowBible.model_validate(bible)
    episodes_dir.mkdir(parents=True, exist_ok=True)
    bible_path = episodes_dir / "show_bible.json"
    payload = validated.model_dump_json(indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=str(episodes_dir), prefix=".show_bible_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, bible_path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return validated


def load_recent_episodes_memory(
    episodes_dir: Path, limit: int = 4
) -> List[EpisodeMemoryItem]:
    """Load compact summaries of the most recent episodes from `episodes_dir`."""
    if not episodes_dir.is_dir() or limit <= 0:
        return []

    items: List[EpisodeMemoryItem] = []
    # Episode directory names begin with YYYYMMDD-HHMMSS, so reverse sort yields newest first.
    candidates = sorted(
        (p for p in episodes_dir.iterdir() if p.is_dir() and not p.is_symlink()),
        key=lambda p: p.name,
        reverse=True,
    )
    for ep_dir in candidates:
        timeline_path = ep_dir / "timeline.json"
        if not _is_safe_regular_file(timeline_path, episodes_dir):
            continue
        try:
            payload = json.loads(timeline_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Skipping unreadable timeline %s: %s", timeline_path, exc)
            continue
        if not isinstance(payload, dict):
            continue

        title = _sanitize_memory_text(str(payload.get("title") or ""), 120)
        summary = _sanitize_memory_text(str(payload.get("summary") or ""), 200)
        if not title:
            continue

        raw_segments = payload.get("segments")
        segments = raw_segments if isinstance(raw_segments, list) else []
        takeaway_texts = [
            _sanitize_memory_text(str(seg.get("text") or ""), 120)
            for seg in segments
            if isinstance(seg, dict) and seg.get("chapter") == "takeaway" and seg.get("text")
        ]
        if not takeaway_texts and segments:
            takeaway_texts = [
                _sanitize_memory_text(str(seg.get("text") or ""), 120)
                for seg in segments[-2:]
                if isinstance(seg, dict) and seg.get("text")
            ]

        items.append(
            EpisodeMemoryItem(
                episode_id=ep_dir.name,
                title=title,
                summary=summary,
                guest_name=_sanitize_memory_text(str(payload.get("guest_name") or ""), 60) or None,
                interview_format=_sanitize_memory_text(str(payload.get("interview_format") or "noodle_chat"), 60)
                or "noodle_chat",
                takeaway=" / ".join(takeaway_texts[:2]),
            )
        )
        if len(items) >= limit:
            break
    return items


_CONTINUITY_TEMPLATES = {
    "zh": {
        "header": "節目聖經與延續記憶（{show}）：",
        "persona": "- Speaker {num} ({host}) 長期靈魂設定：{persona}",
        "cb_sep": "；",
        "callbacks": "- 節目招牌口頭禪/老梗（每集最多自然偷渡 1 句，勿硬塞）：{items}",
        "cl_sep": "、",
        "cliches": "- 嚴禁出現的 AI 罐頭開場/結尾詞：{items}",
        "recent_hdr": "近期已播出集數記憶（若與本集主題相關，可自然吐槽或呼應「我們之前聊過…」）：",
        "guest": "（來賓：{name}）",
        "takeaway": " | 當時結論：{text}",
        "ep_line": "  * 《{title}》{guest}：{summary}{takeaway}",
    },
    "en": {
        "header": "Show Bible & Continuity Memory ({show}):",
        "persona": "- Speaker {num} ({host}) Persistent Persona: {persona}",
        "cb_sep": "; ",
        "callbacks": "- Recurring Show Callbacks (weave in at most 1 naturally if it fits, never force): {items}",
        "cl_sep": ", ",
        "cliches": "- Avoid generic AI clichés: {items}",
        "recent_hdr": "Recent Episodes (reference naturally if relevant, never force it):",
        "guest": " (Guest: {name})",
        "takeaway": " | Takeaway: {text}",
        "ep_line": "  * {title}{guest}: {summary}{takeaway}",
    },
}


def build_continuity_context(
    episodes_dir: Optional[Path],
    language: str = "zh-TW",
    limit: int = 4,
    host_a: str = "Speaker 1",
    host_b: str = "Speaker 2",
    interview_format: str = "noodle_chat",
) -> str:
    """Build a prompt block containing the Show Bible and recent episode callbacks."""
    if episodes_dir is None:
        return ""

    bible = load_show_bible(episodes_dir)
    recent = load_recent_episodes_memory(episodes_dir, limit=limit)
    t = _CONTINUITY_TEMPLATES["zh" if language.startswith("zh") else "en"]
    safe_show = _sanitize_memory_text(bible.show_name, 60)
    lines = [t["header"].format(show=safe_show)]

    if interview_format == "noodle_chat":
        for num, host, raw_persona in (
            (1, _sanitize_memory_text(host_a, 40) or "Speaker 1", bible.host_a_persona),
            (2, _sanitize_memory_text(host_b, 40) or "Speaker 2", bible.host_b_persona),
        ):
            if raw_persona.strip():
                lines.append(
                    t["persona"].format(num=num, host=host, persona=_sanitize_memory_text(raw_persona, 220))
                )

    if bible.recurring_callbacks:
        callbacks = t["cb_sep"].join(
            _sanitize_memory_text(c, 80) for c in bible.recurring_callbacks[:5] if c.strip()
        )
        if callbacks:
            lines.append(t["callbacks"].format(items=callbacks))
    if bible.banned_cliches:
        banned = t["cl_sep"].join(
            _sanitize_memory_text(c, 60) for c in bible.banned_cliches[:8] if c.strip()
        )
        if banned:
            lines.append(t["cliches"].format(items=banned))
    if recent:
        lines.append(t["recent_hdr"])
        for ep in recent:
            guest_note = t["guest"].format(name=ep.guest_name) if ep.guest_name else ""
            takeaway_note = t["takeaway"].format(text=ep.takeaway) if ep.takeaway else ""
            lines.append(
                t["ep_line"].format(
                    title=ep.title, guest=guest_note, summary=ep.summary, takeaway=takeaway_note
                )
            )
    return "\n".join(lines) if len(lines) > 1 else ""
