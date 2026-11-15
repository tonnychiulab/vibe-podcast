"""Pydantic request and response schemas for the VibePodcast Studio API."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from vibe_podcast.interview import DEFAULT_INTERVIEW_FORMAT
from vibe_podcast.scriptwriter import (
    DEFAULT_HOST_A,
    DEFAULT_HOST_B,
    SOURCE_MAX_CHARS,
    PodcastScript,
)


class GenerateRequest(BaseModel):
    """Payload to generate a new dual-host podcast episode."""

    source_text: Optional[str] = Field(default=None, max_length=250_000)
    source_url: Optional[str] = Field(default=None, max_length=2048)
    report_path: Optional[str] = Field(default=None, max_length=512)
    host_a: str = Field(default=DEFAULT_HOST_A, max_length=60)
    host_b: str = Field(default=DEFAULT_HOST_B, max_length=60)
    guest_name: Optional[str] = Field(default=None, max_length=60)
    interview_format: str = DEFAULT_INTERVIEW_FORMAT
    focus_questions: Optional[List[str]] = None
    language: str = "zh-TW"
    ui_lang: str = "zh-TW"
    target_turns: int = Field(default=12, ge=4, le=40)
    engine: Literal["auto", "edge_dialogue", "vibevoice_remote"] = "auto"
    backend: Literal["auto", "agy", "ollama"] = "auto"
    model: Optional[str] = Field(default=None, max_length=120)
    long_text_mode: Literal["map_reduce", "truncate"] = "map_reduce"
    voice_map: Optional[Dict[str, str]] = None
    enable_loudnorm: bool = True
    enable_sfx: bool = False
    enable_show_memory: bool = True


class ScriptDraftResponse(BaseModel):
    """Response for two-stage script drafting before TTS synthesis."""

    script: PodcastScript
    source_chars: int
    truncated: bool
    summarized: bool = False
    max_source_chars: int = SOURCE_MAX_CHARS


class SynthesizeScriptRequest(BaseModel):
    """Payload to synthesize a pre-generated or user-edited PodcastScript."""

    script: PodcastScript
    engine: Literal["auto", "edge_dialogue", "vibevoice_remote"] = "auto"
    language: str = "zh-TW"
    ui_lang: str = "zh-TW"
    voice_map: Optional[Dict[str, str]] = None
    enable_loudnorm: bool = True
    enable_sfx: bool = False
    enable_show_memory: bool = True


class ResynthesizeTurnRequest(BaseModel):
    """Payload to update and re-synthesize a single dialogue turn."""

    text: str = Field(..., min_length=1, max_length=2000)
    cue: Optional[str] = Field(default=None, max_length=60)
    ui_lang: str = "zh-TW"


class UploadSourceRequest(BaseModel):
    """Payload to extract text from an uploaded .md, .txt, or .pdf file."""

    filename: str = Field(..., min_length=1, max_length=255)
    content_base64: str = Field(..., min_length=1, max_length=8_000_000)
    ui_lang: str = "zh-TW"


class EpisodeSummary(BaseModel):
    """Summary item for episode list."""

    episode_id: str
    title: str
    summary: str
    host_a: str
    host_b: str
    guest_name: Optional[str] = None
    engine: str
    duration_sec: float


class CustomPresetPayload(BaseModel):
    """Payload to register a custom interview preset from imported JSON."""

    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name_zh: str = Field(..., min_length=1, max_length=80)
    name_en: Optional[str] = Field(default=None, max_length=80)
    description_zh: str = Field(default="", max_length=240)
    description_en: str = Field(default="", max_length=240)
    host_a_role: str = Field(..., min_length=1, max_length=300)
    host_b_role: str = Field(..., min_length=1, max_length=300)
    guest_role: str = Field(default="", max_length=300)
    pacing_instruction: str = Field(..., min_length=1, max_length=400)
