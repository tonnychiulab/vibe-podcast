"""Shared pytest fixtures preventing tests from touching real user config files."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_config_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect persisted token, preset, and default report paths to tmp_path during tests."""
    cfg_dir = tmp_path / "vp_cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VIBEPODCAST_TOKEN_FILE", str(cfg_dir / "session_token"))
    monkeypatch.setenv("VIBEPODCAST_LISTEN_TOKEN_FILE", str(cfg_dir / "listen_token"))
    monkeypatch.setenv("VIBEPODCAST_PRESETS_FILE", str(cfg_dir / "custom_presets.json"))
