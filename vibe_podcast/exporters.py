"""Exporters for VibePodcast episodes: SRT subtitles, Markdown Show Notes, and RSS 2.0 XML."""

from __future__ import annotations

import math
import re
from typing import List
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

from vibe_podcast.interview import CHAPTER_LABELS
from vibe_podcast.synthesizer import SynthesisResult


def _safe_seconds(seconds: float) -> float:
    return seconds if isinstance(seconds, (int, float)) and math.isfinite(seconds) else 0.0


_XML_ILLEGAL_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff\ufffe\uffff]")


def _strip_xml_illegal(value: str) -> str:
    """Remove control characters, surrogates, and non-characters forbidden in XML 1.0."""
    return _XML_ILLEGAL_CTRL_RE.sub("", value or "")


def _xml_text(value: str) -> str:
    return xml_escape(_strip_xml_illegal(value))


def _xml_attr(value: str) -> str:
    return xml_escape(_strip_xml_illegal(value), {'"': "&quot;", "'": "&apos;"})


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(_safe_seconds(seconds) * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _sanitize_cue_line(text: str) -> str:
    return re.sub(r"[\r\n]+", " ", text).strip()


def timeline_to_srt(result: SynthesisResult, include_speaker: bool = True) -> str:
    """Convert a SynthesisResult timeline into standard SubRip (.srt) subtitles."""
    blocks = []
    for idx, seg in enumerate(result.segments, start=1):
        start_sec = max(0.0, _safe_seconds(seg.start_sec))
        end_sec = max(start_sec, _safe_seconds(seg.end_sec))
        start_ts = _format_srt_timestamp(start_sec)
        end_ts = _format_srt_timestamp(end_sec)
        speaker_clean = _sanitize_cue_line(seg.speaker_name)
        text_clean = _sanitize_cue_line(seg.text)
        prefix = f"[{speaker_clean}] " if include_speaker and speaker_clean else ""
        blocks.append(f"{idx}\n{start_ts} --> {end_ts}\n{prefix}{text_clean}\n")
    return "\n".join(blocks)


def _format_mmss(seconds: float) -> str:
    total = max(0, int(round(_safe_seconds(seconds))))
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def timeline_to_shownotes_md(result: SynthesisResult) -> str:
    """Export an episode timeline and metadata as Markdown Show Notes."""
    hosts = f"{result.host_a} & {result.host_b}"
    if result.guest_name:
        hosts += f" (特別來賓: {result.guest_name})"

    lines = [
        f"# {result.title}",
        "",
        f"- **主持陣容**：{hosts}",
        f"- **節目長度**：{_format_mmss(result.duration_sec)}",
        f"- **語音引擎**：`{result.engine}`",
        "",
        "## 本集簡介",
        "",
        result.summary,
        "",
        "## 章節時間軸",
        "",
    ]
    seen_chapters = set()
    for seg in result.segments:
        if seg.chapter and seg.chapter not in seen_chapters:
            seen_chapters.add(seg.chapter)
            label = CHAPTER_LABELS.get(seg.chapter, {}).get("zh-TW", seg.chapter)
            lines.append(f"- `{_format_mmss(seg.start_sec)}` — **{label}**")
    if not seen_chapters:
        lines.append("- `00:00` — 全集對話")

    lines.extend(["", "## 完整逐字稿", ""])
    for seg in result.segments:
        lines.append(f"**[{_format_mmss(seg.start_sec)}] {seg.speaker_name}**：{seg.text}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def episodes_to_rss_xml(
    episodes: List[SynthesisResult],
    base_url: str,
    token: str = "",
) -> str:
    """Generate an RSS 2.0 + iTunes-compatible Podcast feed XML."""
    clean_base = base_url.rstrip("/")
    escaped_base = _xml_attr(clean_base)
    token_qs = f"?token={quote(token, safe='')}" if token else ""
    items_xml: List[str] = []
    for ep in episodes:
        ep_slug = quote(ep.episode_id, safe="-_")
        audio_url = f"{escaped_base}/api/episodes/{_xml_attr(ep_slug)}/audio{_xml_attr(token_qs)}"
        dur_int = max(1, int(round(_safe_seconds(ep.duration_sec))))
        items_xml.append(
            f"""    <item>
      <title>{_xml_text(ep.title)}</title>
      <description>{_xml_text(ep.summary)}</description>
      <guid isPermaLink="false">{_xml_text(ep.episode_id)}</guid>
      <enclosure url="{audio_url}" type="audio/mpeg" length="0"/>
      <itunes:duration>{dur_int}</itunes:duration>
    </item>"""
        )

    items_block = "\n".join(items_xml)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>老馬麵攤 — VibePodcast Studio</title>
    <link>{_xml_text(clean_base)}</link>
    <description>AI 雙人／多人技術 Podcast 工作室</description>
    <language>zh-tw</language>
{items_block}
  </channel>
</rss>
"""
