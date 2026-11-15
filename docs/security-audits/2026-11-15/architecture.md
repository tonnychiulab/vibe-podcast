# VibePodcast Studio — Security Architecture Summary

**Target**: `.` (`v0.3.0`, commit `434b429d9aab31f30e34fedc9e532c39179b3724`)  
**Run Profile**: `quick` (scope: `.` — single-pass bounded baseline; partial coverage)

## 1. Product, Principals, Authority, and Protected Resources
VibePodcast Studio ("老馬麵攤 — VibePodcast Studio") is a self-hosted multi-host AI podcast generator and web studio combining a FastAPI backend (`vibe_podcast/server.py`) with a vanilla ES-module browser UI (`web/`). It ingests Markdown reports, pasted text, uploaded `.md`/`.txt`/`.pdf` files, or web/Hacker News URLs, generates structured multi-speaker scripts via `agy` CLI or Ollama, synthesizes speech via Edge-TTS (with macOS `say` fallback) or an optional VibeVoice GPU endpoint, mixes audio with `ffmpeg`/`ffprobe`, and serves playback, `.srt`, Show Notes, and an RSS 2.0 feed.

**Principals & Authority**:
- **Unauthenticated HTTP/Browser Client**: Can fetch static UI assets (`/`), probe minimal `/api/health`, send `OPTIONS` preflight, and exchange a valid token at `GET /?token=...` for an `HttpOnly` `vp_session` cookie.
- **Listener (`LISTEN_TOKEN`)**: Read-only `GET`/`HEAD` access to `/api/meta`, `/api/episodes`, `/api/rss-url`, `/api/episodes/{id}/(timeline|audio|srt|shownotes)`, and `/feed.xml`.
- **Admin (`SESSION_TOKEN`)**: Single-studio operator with full studio authority: `/api/reports`, `/api/show-bible` (`GET`/`PUT`), `/api/presets/import`, `/api/upload-source`, `/api/script`, `/api/synthesize`, `/api/generate`, `/api/generate/stream`, single-turn re-synthesis (`POST /api/episodes/{id}/turns/{idx}`), episode deletion, and full `preflight` diagnostics on `/api/health`.
- **Untrusted Inputs & Third-Party Outputs**: External URLs/HN items (`WebUrlConnector`), uploaded documents, LLM JSON outputs, and remote VibeVoice WAV responses.

**Protected Resources**: `SESSION_TOKEN` / `LISTEN_TOKEN` (`~/.config/vibe-podcast/*`), local filesystem (`REPORTS_DIR`, `EPISODES_DIR`), host `agy` subscription/CLI and subprocess execution (`ffmpeg`, `ffprobe`, `pdftotext`, `say`), outbound SSRF reachability, persistent `show_bible.json` and `_custom_presets.json`, and browser session integrity.

## 2. Comparable-Software Baseline
Local documentation (`README.md:9,15,31-32`) explicitly benchmarks **Google NotebookLM Audio Overview** and **Microsoft Research VibeVoice**. Unlike multi-tenant cloud NotebookLM, VibePodcast uses a single-owner local studio architecture without user accounts or tenant ACLs, delegating LLM inference to the operator's local `agy` CLI or Ollama and optionally exposing read-only episodes and tokenized RSS feeds over Cloudflare Tunnel (`deploy/cloudflared.config.yml.example`). Prompt injection, outbound URL fetching, local-file disclosure, and reverse-proxy exposure remain security-critical on shared or tunneled instances.

## 3. Stack, Deployment Paths, and Execution Limits
- **Stack**: Python `>=3.11` (CPython 3.12 `.venv`), FastAPI, Uvicorn, Pydantic v2, `edge-tts`, `pypdf`, `soundfile`, `numpy`, plus system `ffmpeg`/`ffprobe`, optional `pdftotext`, macOS `say`, and `agy` CLI; zero-dependency browser ES modules + Web Audio API; `.github/workflows/ci.yml` (`uv sync --extra dev`, `pytest -v`).
- **Deployment Paths**: (1) Local server or `scripts/serve_studio.sh` (`uvicorn --host 127.0.0.1 --port 8765 --proxy-headers --forwarded-allow-ips="127.0.0.1"`) fronted optionally by `cloudflared`; (2) `python -m vibe_podcast.preflight` CLI; (3) `scripts/generate_demo.py`.
- **Local Sandbox Blocker**: On this Darwin host inside the outer sandbox, nested `sandbox-exec` (`sandbox_apply`), Linux network/mount namespaces, container VMs, per-sandbox memory caps, and disk quotas are unavailable (`Operation not permitted`). Per policy, **target-controlled execution is blocked**; verification relies on static source proof, with runtime/deployment uncertainties recorded as `needs_validation`.

## 4. Entry Surfaces and Source-to-Sink Paths
- **HTTP / Cookie / Proxy**: `GET /?token=...` -> `vp_session` cookie; Bearer/Cookie/query (`audio`, `feed.xml`) auth middleware; `CORSMiddleware` (`OPTIONS` before auth); `X-Forwarded-Proto`/`X-Forwarded-Host`/`Host` in cookie `Secure` flag and `/api/rss-url` + `/feed.xml`.
- **Content Ingestion -> Prompt / File / Disk**: `source_url` -> `WebUrlConnector` (HTTP/HN API + external article links) -> XML wrapping (`_wrap_untrusted_xml`) -> `agy`/`ollama`; `report_path` -> `MarkdownDirConnector` (`REPORTS_DIR`); `POST /api/upload-source` -> base64 decode -> `pypdf` or temp `.pdf` + `pdftotext`.
- **State, Audio & Subprocess Sinks**: `POST /api/synthesize` (`PodcastScript`), `POST /api/episodes/{id}/turns/{idx}`, `/api/presets/import`, `/api/show-bible`, and `show_memory` continuity flow into `ShowBible`/`_custom_presets.json`, future prompts, `edge_tts`/`say`/`vibevoice_remote`, `ffmpeg`/`ffprobe`, `.srt`/`.md`/RSS XML exports, and browser `innerHTML`/`localStorage`.
- **CI / Supply Chain**: `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `scripts/serve_studio.sh`.

## 5. Trust Boundaries and Strongest Visible Controls
- **Auth & RBAC (`vibe_podcast/auth.py`)**: `0o600` secret tokens, `hmac.compare_digest`, `listener` `GET`/`HEAD` path allowlist, `allow_query_admin=False` on audio/RSS, `HttpOnly` + `SameSite=Lax` cookie, CSP, `X-Frame-Options: DENY`.
- **Proxy & CORS (`server.py`, `routes/episodes.py`)**: `VIBEPODCAST_ALLOWED_ORIGINS` allowlist; `_HOST_HEADER_RE`; `--forwarded-allow-ips="127.0.0.1"` in `serve_studio.sh`.
- **Filesystem (`connectors.py`, `routes/episodes.py`)**: `_EPISODE_ID_RE`, `is_symlink()` + `.resolve().is_relative_to(...)`, `.md`/extension allowlists.
- **Outbound Network (`connectors.py`, `synthesizer.py`)**: `_is_public_ip`, DNS-pinned `_PinnedHTTPConnection`, max 3 redirects, `Content-Type` allowlist, 512 KB cap; `_validate_public_url` on `VIBEVOICE_REMOTE_URL`.
- **LLM & Subprocess (`scriptwriter.py`, `guardrails.py`, `show_memory.py`, `routes/studio.py`)**: randomized `<source_document_*>` wrappers, `check_text_safety` + `check_prompt_injection`, `--disable-slash-commands`, `_MODEL_NAME_RE`, `shell=False` lists, `_GENERATION_SEMAPHORE(2)`, `pypdf`/upload caps.

## 6. Repository-Relative Starting Paths
`vibe_podcast/server.py`, `vibe_podcast/auth.py`, `vibe_podcast/routes/studio.py`, `vibe_podcast/routes/episodes.py`, `vibe_podcast/schemas.py`, `vibe_podcast/connectors.py`, `vibe_podcast/scriptwriter.py`, `vibe_podcast/guardrails.py`, `vibe_podcast/show_memory.py`, `vibe_podcast/interview.py`, `vibe_podcast/synthesizer.py`, `vibe_podcast/audio_fx.py`, `vibe_podcast/exporters.py`, `vibe_podcast/preflight.py`, `web/index.html`, `web/app.js`, `web/js/`, `scripts/serve_studio.sh`, `deploy/cloudflared.config.yml.example`, `.github/workflows/ci.yml`, `pyproject.toml`, `uv.lock`.

## 7. Prior Coverage State
No prior audit runs, `coverage-ledger.json`, or `findings.json` exist for this repository; all in-scope units start fresh (`prior_status: "none"`).

## 8. Companion Selection Summary
- `WEB-PROTOCOL-AND-AUTH.md`: HTTP cookie exchange, query tokens, CORS, `X-Forwarded-*` host/proto trust, and cache/header controls.
- `CLIENT-SIDE.md`: Browser UI `innerHTML`/DOM sinks, `localStorage` state, credentialed CORS, clickjacking, and client navigation.
- `AI-AND-LLM.md`: Prompt construction (`_wrap_untrusted_xml`), `show_memory`/preset persistent context, `agy` CLI boundary, and LLM output rendering/slur filters.
- `RESOURCE-EXHAUSTION-AND-AVAILABILITY.md`: `_GENERATION_SEMAPHORE(2)`, unbounded `PodcastScript`/report/file reads, PDF/ffmpeg/TTS CPU/disk/process work, and URL timeouts.
- `SUPPLY-CHAIN-AND-RELEASE.md`: `pyproject.toml`/`uv.lock` constraints, `.github/workflows/ci.yml`, `scripts/serve_studio.sh`, and runtime binary resolution (`PATH`).
