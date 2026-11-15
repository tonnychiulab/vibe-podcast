"""Internationalization (i18n) and brand/credits loader."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from vibe_podcast import __version__
from vibe_podcast.interview import CHAPTER_LABELS, list_presets_meta

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LOCALE = "zh-TW"
SUPPORTED_LOCALES = ("zh-TW", "en-US")


def normalize_locale(lang: str | None) -> str:
    """Normalize a locale code such as 'en' or 'zh' to a supported locale."""
    if not lang:
        return DEFAULT_LOCALE
    if lang in SUPPORTED_LOCALES:
        return lang
    lowered = lang.lower()
    if lowered.startswith("en"):
        return "en-US"
    if lowered.startswith("zh"):
        return "zh-TW"
    return DEFAULT_LOCALE


@lru_cache(maxsize=8)
def _load_locale_cached(target: str, _mtime_ns: int) -> Dict[str, Any]:
    path = LOCALES_DIR / f"{target}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_locale(lang: str = DEFAULT_LOCALE) -> Dict[str, Any]:
    """Load locale dictionary, reloading automatically when the file mtime changes."""
    target = normalize_locale(lang)
    path = LOCALES_DIR / f"{target}.json"
    return _load_locale_cached(target, path.stat().st_mtime_ns)


def get_error_message(key: str, lang: str = DEFAULT_LOCALE, **kwargs: Any) -> str:
    """Resolve a localized error message by key safely."""
    locale = load_locale(lang)
    template = locale.get("errors", {}).get(key, key)
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError, IndexError):
        return template


def get_app_meta(lang: str = DEFAULT_LOCALE) -> Dict[str, Any]:
    """Return app version, brand metadata, credits, interview presets, and localized UI strings."""
    target = normalize_locale(lang)
    locale = load_locale(target)
    return {
        "version": __version__,
        "locale": target,
        "supported_locales": list(SUPPORTED_LOCALES),
        "brand": dict(locale["brand"]),
        "ui": dict(locale["ui"]),
        "status": dict(locale["status"]),
        "interview_formats": list_presets_meta(target),
        "chapter_labels": {
            k: v.get(target, v[DEFAULT_LOCALE]) for k, v in CHAPTER_LABELS.items()
        },
    }
