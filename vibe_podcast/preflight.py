"""Zero-quota preflight and --dry-run dependency checker for VibePodcast."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel


class CheckItem(BaseModel):
    """Single dependency or environment check result."""

    name: str
    ok: bool
    required: bool = True
    detail: str
    hint: Optional[str] = None


class PreflightReport(BaseModel):
    """Aggregated preflight report for CLI --dry-run and /api/health."""

    ok: bool
    ffmpeg_available: bool
    ffprobe_available: bool
    agy_available: bool
    ollama_reachable: bool
    vibevoice_remote_configured: bool
    checks: List[CheckItem]


def _probe_http_ok(url: str, timeout: float = 1.5, *, enforce_public: bool = False) -> bool:
    try:
        from urllib.parse import urlparse

        parsed_basic = urlparse(url)
        if parsed_basic.scheme.lower() not in {"http", "https"} or not parsed_basic.hostname:
            return False
        if enforce_public:
            from vibe_podcast.connectors import _open_pinned_opener, _resolve_pinned_candidates

            parsed, pinned_ips = _resolve_pinned_candidates(url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            deadline = time.monotonic() + timeout
            opener = _open_pinned_opener({(parsed.hostname.lower(), port): pinned_ips}, deadline=deadline)
            req = urllib.request.Request(url, method="GET")
            with opener.open(req, timeout=timeout) as resp:
                return 200 <= resp.status < 500

        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except Exception:
        return False


def _is_dir_writable(target: Path) -> bool:
    tmp_path: Optional[Path] = None
    try:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=target, prefix=".preflight_", suffix=".tmp", mode="w", encoding="utf-8", delete=False
        ) as fh:
            tmp_path = Path(fh.name)
            fh.write("ok")
        return True
    except Exception:
        return False
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def run_preflight(
    episodes_dir: Optional[Path] = None,
    probe_network: bool = True,
) -> PreflightReport:
    """Run zero-quota system, storage, and LLM/TTS readiness checks."""
    checks: List[CheckItem] = []

    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    ffmpeg_ok = ffmpeg_bin is not None
    ffprobe_ok = ffprobe_bin is not None
    checks.append(
        CheckItem(
            name="ffmpeg",
            ok=ffmpeg_ok,
            required=True,
            detail=ffmpeg_bin or "not found in PATH",
            hint=None if ffmpeg_ok else "Install ffmpeg (macOS: brew install ffmpeg)",
        )
    )
    checks.append(
        CheckItem(
            name="ffprobe",
            ok=ffprobe_ok,
            required=True,
            detail=ffprobe_bin or "not found in PATH",
            hint=None if ffprobe_ok else "Install ffprobe (bundled with ffmpeg)",
        )
    )

    edge_ok = importlib.util.find_spec("edge_tts") is not None
    checks.append(
        CheckItem(
            name="edge_tts",
            ok=edge_ok,
            required=True,
            detail="importable" if edge_ok else "module not found",
            hint=None if edge_ok else "Run `uv sync` to install Python dependencies",
        )
    )

    resolved_ep_dir = episodes_dir or Path(
        os.environ.get("VIBEPODCAST_EPISODES_DIR", Path(__file__).resolve().parent.parent / "episodes")
    )
    dir_ok = _is_dir_writable(resolved_ep_dir)
    checks.append(
        CheckItem(
            name="episodes_dir",
            ok=dir_ok,
            required=True,
            detail=f"{resolved_ep_dir} ({'writable' if dir_ok else 'not writable'})",
            hint=None if dir_ok else "Check directory permissions or VIBEPODCAST_EPISODES_DIR",
        )
    )

    agy_bin = shutil.which("agy")
    agy_ok = agy_bin is not None
    ollama_base = (os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1").rstrip("/")
    ollama_ok = _probe_http_ok(f"{ollama_base}/models") if probe_network else False

    available_backends = [
        name
        for name, ready in (
            ("agy (subscription)", agy_ok),
            ("ollama (local)", ollama_ok),
        )
        if ready
    ]
    llm_ok = len(available_backends) > 0
    checks.append(
        CheckItem(
            name="llm_backend",
            ok=llm_ok,
            required=True,
            detail=", ".join(available_backends) if llm_ok else "no backend ready",
            hint=None if llm_ok else "Authenticate `agy` CLI via subscription login or start local Ollama",
        )
    )

    remote_url = (os.environ.get("VIBEVOICE_REMOTE_URL") or "").strip()
    if remote_url:
        enforce_public_remote = os.environ.get("VIBEPODCAST_ALLOW_PRIVATE_REMOTE") != "1"
        remote_ok = _probe_http_ok(remote_url, enforce_public=enforce_public_remote) if probe_network else True
        checks.append(
            CheckItem(
                name="vibevoice_remote",
                ok=remote_ok,
                required=False,
                detail=f"{remote_url} ({'reachable' if remote_ok else 'unreachable'})",
                hint=None if remote_ok else "Ensure remote VibeVoice GPU server is running",
            )
        )

    overall_ok = all(item.ok for item in checks if item.required)
    return PreflightReport(
        ok=overall_ok,
        ffmpeg_available=ffmpeg_ok,
        ffprobe_available=ffprobe_ok,
        agy_available=agy_ok,
        ollama_reachable=ollama_ok,
        vibevoice_remote_configured=bool(remote_url),
        checks=checks,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vibe_podcast.preflight",
        description="Run zero-quota dry-run dependency checks for VibePodcast.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify dependencies and backend readiness without calling any LLM or writing episodes.",
    )
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="Skip local/remote HTTP probes (Ollama and VIBEVOICE_REMOTE_URL).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON.",
    )
    args = parser.parse_args(argv)

    report = run_preflight(probe_network=not args.no_network)
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        for item in report.checks:
            tag = "PASS" if item.ok else ("FAIL" if item.required else "WARN")
            line = f"[{tag}] {item.name}: {item.detail}"
            if item.hint:
                line += f" -> {item.hint}"
            print(line)
        print(f"\nPreflight status: {'OK' if report.ok else 'FAILED'}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
