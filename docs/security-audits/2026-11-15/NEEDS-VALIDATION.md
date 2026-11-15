# Leads Requiring Validation (`NEEDS-VALIDATION`)

Run ID: `vibe-podcast-run-1` | Commit: `434b429d9aab31f30e34fedc9e532c39179b3724` | Profile: `quick`

Each item below passed Phase 1–5 independent source verification (`findings.json`), but cannot be promoted to `confirmed` without either bounded local execution inside an OS-enforced sandbox (unavailable on this host) or owner-observed deployment configuration checks. Consistent with `security-audit` discipline, leads are prioritized by boundary exposure without assigning premature severity scores.

---

## Lead 1: Persistent Admin SESSION_TOKEN exposed via GET query URLs and application logging

- **Fingerprint**: `vibe_podcast/auth.py:admin-token-in-query-url-and-logs`
- **Affected Boundary / Claimed Root Cause**: Admin browser session establishment relies on passing the long-lived raw SESSION_TOKEN in a GET query parameter (`GET /?token=`) and logging the auth URL via `logger.info`, rather than exchanging a short-lived one-time code or POSTing the token in a request body to mint an opaque rotatable session.

### Description
Although /feed.xml and /api/episodes/*/audio explicitly block the high-privilege Admin SESSION_TOKEN from URL query parameters (allow_query_admin=False), browser authentication still transmits and logs the persistent, non-rotatable Admin SESSION_TOKEN in HTTP GET query strings and application logs. On startup, server.py logs the full `/?token=<SESSION_TOKEN>` URL via `logger.info` and prints local/public admin URLs to stdout; in the browser UI, manual prompt entry in web/app.js navigates to `GET /?token=<SESSION_TOKEN>`; and handle_security_and_auth exchanges that query token for a 30-day `vp_session` cookie containing the raw SESSION_TOKEN on a 302 response that returns early before `Cache-Control: no-store` is applied. Because SESSION_TOKEN is reused across restarts from `~/.config/vibe-podcast/session_token` (or `VIBEPODCAST_TOKEN`) with no rotation or revocation endpoint, disclosure in application logs, Uvicorn/reverse-proxy access logs, or browser history grants full studio Admin authority.

### Source Trace
- **[entrypoint]** `vibe_podcast/server.py:53` (`module-level token initialization`): Loads or creates persistent high-privilege Admin SESSION_TOKEN via new_session_token().
- **[propagation]** `vibe_podcast/server.py:64` (`_lifespan`): Logs plaintext SESSION_TOKEN inside the auth URL via logger.info('VibePodcast Auth URL: http://127.0.0.1:%s/?token=%s', port, SESSION_TOKEN).
- **[propagation]** `web/app.js:237` (`loadMeta`): Redirects browser on manual token prompt entry to `/?token=${encodeURIComponent(entered.trim())}`, sending Admin SESSION_TOKEN in an HTTP GET query string.
- **[sink]** `vibe_podcast/auth.py:160` (`handle_security_and_auth`): Accepts Admin SESSION_TOKEN in GET /?token= query parameter, sets vp_session cookie directly to raw SESSION_TOKEN for 30 days, and returns 302 RedirectResponse without Cache-Control: no-store.

### Verified Source Evidence
- `vibe_podcast/server.py:64` — Plaintext Admin SESSION_TOKEN is passed to logger.info as part of the `/?token=` URL (and printed to stdout on lines 71-78).
- `vibe_podcast/auth.py:160` — GET /?token= accepts SESSION_TOKEN in URL query parameters and returns 302 at line 174 with Set-Cookie: vp_session=<SESSION_TOKEN> before Cache-Control: no-store is set at line 205.
- `web/app.js:237` — Client login prompt submits token via window.location.href GET query parameter rather than a POST body or Authorization header.
- `scripts/serve_studio.sh:17` — Launches uvicorn without --no-access-log, leaving uvicorn's default access logging enabled for GET /?token=<SESSION_TOKEN> requests.

### Exact Blockers
- Target-controlled execution (uvicorn/pytest) is blocked on this host because OS-enforced nested sandboxing (sandbox-exec: sandbox_apply: Operation not permitted) is unavailable, preventing bounded local observation of logger/uvicorn access log output and 302 response headers.
- Deployment logging and proxy retention facts (whether application/uvicorn logs or Cloudflare/reverse-proxy access logs persist URL query strings and are accessible to lower-privileged principals) are outside repository source.

### Safe Non-Destructive Validation Plan
- **Local (Sandboxed)**: Inside an OS-enforced sandbox with isolated loopback and a dummy VIBEPODCAST_TOKEN / temporary VIBEPODCAST_TOKEN_FILE, start uvicorn or invoke FastAPI TestClient while capturing logger 'vibe_podcast.server' and uvicorn.access logs, issue GET /?token=<DUMMY_SESSION_TOKEN>, and verify the token appears in captured log records and the 302 Set-Cookie response lacks Cache-Control: no-store.
- **Deployment (Owner-Observed)**: Inspect the operator's host log files, uvicorn stdout/stderr redirection, reverse-proxy/Cloudflare HTTP logs, and browser history/retention policy to confirm whether query strings on GET /?token= and startup logger.info output are persisted or readable by lower-privileged principals.

---

## Lead 2: WebUrlConnector._http_get_bytes lacks a total-read deadline during resp.read(), allowing slow-trickle servers to hold _GENERATION_SEMAPHORE

- **Fingerprint**: `vibe_podcast/connectors.py:_http_get_bytes-per-recv-timeout-slowloris-starvation`
- **Affected Boundary / Claimed Root Cause**: `_http_get_bytes` relies on `urllib` per-socket `timeout` and a pre-open `deadline` check instead of chunked reading with an enforced monotonic wall-clock deadline.

### Description
When an admin-authenticated client triggers /api/script, /api/generate, or /api/generate/stream with source_url (including a Hacker News thread URL whose untrusted submitter-supplied item["url"] is automatically fetched by WebUrlConnector._load_hacker_news), the route acquires one of two server-wide _GENERATION_SEMAPHORE slots before calling _resolve_generate_source(). In _http_get_bytes, deadline is checked only before opener.open() and socket.create_connection applies timeout per recv() operation rather than across the full response body read. A malicious external server returning allowed text/html headers and trickling 1 byte faster than effective_timeout (up to 12s) up to 512_000 bytes can stall resp.read(max_bytes) past the 25s HN deadline and hold the worker thread and _GENERATION_SEMAPHORE slot indefinitely (two concurrent stalls return HTTP 429 for all generation requests).

### Source Trace
- **[entrypoint]** `vibe_podcast/routes/studio.py:353` (`generate_episode`): Admin-authenticated generation endpoints (/api/script at line 317, /api/generate at line 353, /api/generate/stream at line 366) acquire _GENERATION_SEMAPHORE before calling srv._resolve_generate_source(req) (released only in finally).
- **[propagation]** `vibe_podcast/server.py:123` (`_resolve_generate_source`): Calls web_url_connector.load_url(req.source_url.strip()) while the caller holds one of two _GENERATION_SEMAPHORE slots (server.py:55).
- **[propagation]** `vibe_podcast/connectors.py:396` (`WebUrlConnector._load_hacker_news`): For Hacker News thread URLs, automatically fetches untrusted external item["url"] via _http_get_bytes(item["url"], deadline=deadline) with a 25s deadline (line 383); direct URLs also call _http_get_bytes(url) at line 369.
- **[sink]** `vibe_podcast/connectors.py:332` (`_http_get_bytes`): Calls resp.read(max_bytes) (max_bytes=512_000) without a monotonic wall-clock deadline loop; urllib/http.client socket timeout applies per recv(), allowing a slow-trickle server to stall the worker thread and semaphore slot.

### Verified Source Evidence
- `vibe_podcast/server.py:55` — _GENERATION_SEMAPHORE = threading.BoundedSemaphore(value=2) caps concurrent generation and drafting jobs server-wide.
- `vibe_podcast/routes/studio.py:405` — generate_episode_stream runs _worker (which calls _resolve_generate_source and releases _GENERATION_SEMAPHORE in finally at line 401) inside asyncio.to_thread(_worker) without cancellation.
- `vibe_podcast/connectors.py:314` — deadline is only checked before opener.open(req, timeout=effective_timeout) at lines 314-318 (clamped to at least 1.0s).
- `vibe_podcast/connectors.py:273` — _PinnedHTTPConnection.connect (line 273) and _PinnedHTTPSConnection.connect (line 280) pass self.timeout to socket.create_connection, setting per-recv socket timeout rather than a total body-read deadline.
- `vibe_podcast/connectors.py:332` — Single blocking resp.read(max_bytes) with max_bytes=512_000 loops until max_bytes or EOF without re-checking deadline.

### Exact Blockers
- Host sandbox policy prohibits executing target-controlled code and OS-enforced nested sandboxing/network isolation is unavailable on this Darwin host, preventing a bounded local loopback trickle-server reproduction.

### Safe Non-Destructive Validation Plan
- **Local (Sandboxed)**: In an isolated local test with a loopback HTTP server (mocking _validate_and_resolve_url to return 127.0.0.1) that immediately sends HTTP 200 Content-Type: text/html and trickles 1 byte every 0.5s for 5s (10 bytes total), call _http_get_bytes(url, timeout=2, deadline=time.monotonic() + 2.0) and verify it takes ~5s instead of aborting at the 2s deadline, and verify two concurrent requests to /api/script or /api/generate cause a third request to receive HTTP 429 from _acquire_generation_slot.

---

## Lead 3: SSE generation endpoint can leak _GENERATION_SEMAPHORE on pre-stream disconnect and leave un-cancellable worker threads running after SSE cancellation

- **Fingerprint**: `vibe_podcast/routes/studio.py:generate_episode_stream-semaphore-leak-and-detached-worker`
- **Affected Boundary / Claimed Root Cause**: Resource acquisition (`_acquire_generation_slot`) occurs outside the `StreamingResponse` generator lifecycle while release occurs only inside the lazily-started `asyncio.to_thread(_worker)` thread, and `_worker` has no cancellation flag or subprocess termination hook.

### Description
Authenticated admin endpoint POST /api/generate/stream acquires srv._GENERATION_SEMAPHORE (threading.BoundedSemaphore(value=2) in vibe_podcast/server.py:55) before returning StreamingResponse(_event_stream(), media_type='text/event-stream'), while the sole release() lives inside _worker()'s finally block, which is only spawned once _event_stream() starts iterating via asyncio.create_task(asyncio.to_thread(_worker)). If an ASGI error or client disconnect prevents _event_stream() from entering iteration, the semaphore slot is never released, permanently reducing server-wide generation capacity (shared across /api/script, /api/synthesize, /api/generate, /api/generate/stream, and /api/episodes/{id}/turns/{idx}) until server restart. Additionally, on mid-stream disconnect/cancellation, _event_stream() catches cleanup in `finally: await task`, but asyncio.to_thread(_worker) has no cancellation flag or subprocess termination hook, so URL fetching, up to 7 LLM calls, TTS synthesis, and ffmpeg work can continue running in the threadpool while holding a semaphore slot.

### Source Trace
- **[entrypoint]** `vibe_podcast/routes/studio.py:366` (`generate_episode_stream`): Authenticated admin POST /api/generate/stream acquires srv._GENERATION_SEMAPHORE (BoundedSemaphore(value=2) in vibe_podcast/server.py:55) before returning StreamingResponse at line 415.
- **[propagation]** `vibe_podcast/routes/studio.py:405` (`generate_episode_stream._event_stream`): Spawns asyncio.create_task(asyncio.to_thread(_worker)) only when Starlette begins iterating the _event_stream() async generator; _worker holds the only release() in its finally block at line 401.
- **[sink]** `vibe_podcast/routes/studio.py:412` (`generate_episode_stream._event_stream`): If _event_stream() is closed or aborted before iteration begins, _GENERATION_SEMAPHORE is never released; on mid-stream disconnect/cancellation, `finally: await task` cannot abort the blocking threadpool _worker, leaving URL fetch, up to 7 LLM calls, TTS, and ffmpeg running while holding the slot.

### Verified Source Evidence
- `vibe_podcast/routes/studio.py:366` — srv._acquire_generation_slot(req.ui_lang) is invoked inside generate_episode_stream before StreamingResponse(_event_stream(), ...) is returned at line 415.
- `vibe_podcast/routes/studio.py:401` — srv._GENERATION_SEMAPHORE.release() appears only in _worker()'s finally block (lines 400-402).
- `vibe_podcast/routes/studio.py:412` — _event_stream() uses `finally: await task` (lines 412-413) with no threading cancellation event or subprocess termination hook inside _worker().
- `vibe_podcast/server.py:55` — _GENERATION_SEMAPHORE = threading.BoundedSemaphore(value=2) enforces a server-wide 2-slot concurrency limit across generation and turn re-synthesis endpoints.

### Exact Blockers
- OS-enforced nested sandboxing is unavailable on this host, blocking live ASGI/uvicorn execution to confirm whether Starlette/Uvicorn aborts before _event_stream() iteration on early client disconnect and how `finally: await task` behaves under Starlette mid-stream disconnect cancellation.

### Safe Non-Destructive Validation Plan
- **Local (Sandboxed)**: Inside an isolated local ASGI test harness (using an authenticated admin token and mocked _resolve_generate_source, _draft_script_from_request, and synthesize_podcast), abort an HTTP POST /api/generate/stream request (a) immediately before reading the response body and (b) mid-stream after receiving the first SSE event, then inspect _GENERATION_SEMAPHORE._value, check whether _worker continues executing after disconnect, and verify whether subsequent POST /api/script requests fail with HTTP 429.
- **Deployment (Owner-Observed)**: Have the operator inspect the live reverse-proxy configuration (e.g., cloudflared originRequest settings based on deploy/cloudflared.config.yml.example) and observe server logs/worker behavior during a normal operator abort of an SSE generation in the studio UI to confirm whether client disconnects propagate before stream iteration or leave detached worker runs.

---

## Lead 4: PDF upload fails open to unbuffered pdftotext when pypdf triggers decompression or parse limits

- **Fingerprint**: `vibe_podcast/routes/studio.py:upload_source_file-pypdf-limit-fail-open-pdftotext`
- **Affected Boundary / Claimed Root Cause**: Broad `except Exception` in `upload_source_file` treats `pypdf.errors.LimitReachedError` and parse/safety exceptions as empty text extraction and fails open into an un-capped `subprocess.run(..., capture_output=True)` invocation of `pdftotext`.

### Description
POST /api/upload-source (Admin-authenticated via SESSION_TOKEN in vibe_podcast/auth.py, not gated by _GENERATION_SEMAPHORE) accepts base64-encoded .pdf payloads up to 5 MB decoded size (8,000,000 base64 chars in UploadSourceRequest). In upload_source_file, pypdf (locked at 7.1.0 in uv.lock, which enforces a 75 MB per-stream zlib decompression cap via LimitReachedError) is wrapped in a broad `except Exception as exc:` block that logs a warning and resets `extracted = ""`. This causes security/resource limit exceptions (including pypdf.errors.LimitReachedError) to fail open into `subprocess.run([pdftotext_bin, "-f", "1", "-l", "100", tmp_pdf, "-"], capture_output=True, timeout=30, check=True)` when `pdftotext` is installed on PATH, buffering unbounded stdout/stderr in Python memory before slicing `extracted[:250_000]`.

### Source Trace
- **[entrypoint]** `vibe_podcast/routes/studio.py:182` (`upload_source_file`): Admin-authenticated POST /api/upload-source accepts base64-encoded .pdf payloads up to 5 MB decoded size (UploadSourceRequest max_length=8_000_000).
- **[propagation]** `vibe_podcast/routes/studio.py:240` (`upload_source_file`): Catches broad `except Exception as exc:` from pypdf (including pypdf.errors.LimitReachedError) and resets `extracted = ""`.
- **[sink]** `vibe_podcast/routes/studio.py:259` (`upload_source_file`): Invokes `pdftotext -f 1 -l 100 tmp_pdf -` via `subprocess.run(..., capture_output=True, timeout=30)` with no stdout byte cap prior to slicing `extracted[:250_000]` at line 274.

### Verified Source Evidence
- `vibe_podcast/routes/studio.py:240` — Broad `except Exception as exc:` catches pypdf LimitReachedError and resets `extracted = ""`, falling through to the `if not extracted:` pdftotext fallback at line 244.
- `vibe_podcast/routes/studio.py:259` — `subprocess.run(..., capture_output=True, timeout=30, check=True)` buffers full `pdftotext` stdout in memory before `MAX_EXTRACTED_CHARS` (250,000) truncation at lines 274-275.
- `vibe_podcast/schemas.py:73` — `UploadSourceRequest` permits up to 8,000,000 base64 characters (capped at 5 MB decoded bytes in `vibe_podcast/routes/studio.py:198`), while `vibe_podcast/auth.py:179-197` restricts POST /api/upload-source to the Admin role.

### Exact Blockers
- Host sandbox policy blocks target-controlled execution and lacks OS-enforced nested sandboxing (sandbox-exec) and per-process memory caps on this Darwin host, preventing bounded local testing of a small LimitReachedError-triggering PDF fixture against pypdf 7.1.0 + pdftotext.
- Whether `pdftotext` is installed on PATH and how much stdout/RSS amplification a single <=5 MB PDF produces within the 30-second timeout under deployed process/container memory limits cannot be determined from source alone.

### Safe Non-Destructive Validation Plan
- **Local (Sandboxed)**: Under a memory-capped OS sandbox with `pdftotext` available, send a single small (<100 KB) fixture PDF containing a FlateDecode stream exceeding pypdf 7.1.0's 75 MB limit (or dense text across 100 pages) to POST /api/upload-source with an admin token, and measure whether `pypdf` falls open to `pdftotext` and how much stdout/RSS is buffered before line 274 truncation.
- **Deployment (Owner-Observed)**: Verify whether `pdftotext` is present on PATH in the runtime environment (`command -v pdftotext`), what process/container memory limits or supervisor restart policies apply to the studio server, and whether untrusted third-party PDFs are uploaded via the Admin studio session.

---

## Lead 5: Unbounded PodcastScript turns/text cardinality bypasses generation caps and amplifies TTS/FFmpeg work

- **Fingerprint**: `vibe_podcast/schemas.py:SynthesizeScriptRequest-PodcastScript-unbounded-turns-text`
- **Affected Boundary / Claimed Root Cause**: Missing max_length constraints on `PodcastScript.turns` and `DialogueTurn.text` in `vibe_podcast/scriptwriter.py` (reused by `SynthesizeScriptRequest` in `vibe_podcast/schemas.py` and `PodcastScript.model_validate` on LLM output), missing post-LLM turn-count enforcement of `target_turns`, missing `asyncio.wait_for` / overall deadline in `_edge_tts_to_file` and `_synthesize_edge_dialogue`, and missing bounds on `GenerateRequest.focus_questions`.

### Description
While GenerateRequest caps target_turns at 40 (and source_text at 250,000 chars) and ResynthesizeTurnRequest caps text at 2000 characters, PodcastScript.turns and DialogueTurn.text define only min_length=1 with no upper bounds. Both POST /api/synthesize (admin-supplied SynthesizeScriptRequest) and generate_podcast_script (via PodcastScript.model_validate on LLM output, which can be influenced by untrusted external URLs via WebUrlConnector or uploaded documents, and where target_turns is only a prompt hint) accept arbitrarily many turns and arbitrarily long turn text. Additionally, GenerateRequest.focus_questions has no list or element length limits before guardrails iterate all entries. Oversized scripts drive sequential Edge-TTS network calls (up to 6 tries per turn for zh plus macOS say fallback and prior-turn re-synthesis, with no per-call asyncio.wait_for or total deadline) and up to 5 ffprobe/ffmpeg subprocesses per turn plus a single N-input ffmpeg amix graph while holding one of two _GENERATION_SEMAPHORE slots, causing HTTP 429 denial of other studio generation requests.

### Source Trace
- **[entrypoint]** `vibe_podcast/schemas.py:52` (`SynthesizeScriptRequest`): Accepts `script: PodcastScript` directly on POST /api/synthesize (`vibe_podcast/routes/studio.py:331-332`) without turn-count or text-length caps; LLM JSON output from untrusted URL/document sources also enters via `PodcastScript.model_validate(parsed)` at `vibe_podcast/scriptwriter.py:464`.
- **[propagation]** `vibe_podcast/scriptwriter.py:70` (`PodcastScript`): Defines `turns: List[DialogueTurn] = Field(..., min_length=1)` and `DialogueTurn.text: str = Field(..., min_length=1)` (line 32) with no `max_length`; `enforce_script_guardrails` (`vibe_podcast/guardrails.py:97`) performs no turn-count or length capping.
- **[propagation]** `vibe_podcast/synthesizer.py:221` (`_synthesize_edge_dialogue._run_all`): Sequentially invokes `_edge_tts_to_file` across all turns (up to 6 Edge-TTS attempts per turn for zh plus macOS `say` fallback and prior-turn re-synthesis at lines 233-247) with no per-call `asyncio.wait_for` or overall deadline while holding one of two `_GENERATION_SEMAPHORE` slots (`vibe_podcast/server.py:55`).
- **[sink]** `vibe_podcast/audio_fx.py:260` (`merge_turns_to_mp3`): Calls `trim_edge_silence` per turn (spawning up to 4 ffprobe/ffmpeg subprocesses at lines 166, 170, 210, 230) plus `probe_duration_sec` at line 275, and passes all `mix_inputs` as `-i` arguments to `amix=inputs=...` in `ffmpeg` (lines 326-366).

### Verified Source Evidence
- `vibe_podcast/scriptwriter.py:32` — `DialogueTurn.text` only sets `min_length=1` with no `max_length` (contrast with `ResynthesizeTurnRequest.text` `max_length=2000` at `vibe_podcast/schemas.py:68`).
- `vibe_podcast/scriptwriter.py:70` — `PodcastScript.turns` only sets `min_length=1` with no `max_length` (contrast with `GenerateRequest.target_turns` `le=40` at `vibe_podcast/schemas.py:31`).
- `vibe_podcast/scriptwriter.py:464` — `generate_podcast_script` validates LLM output with `PodcastScript.model_validate(parsed)` without enforcing `target_turns`, which is only interpolated into the prompt at line 205.
- `vibe_podcast/schemas.py:28` — `GenerateRequest.focus_questions` is `Optional[List[str]] = None` with no list or item length limit before `generate_podcast_script` iterates all items with `check_text_safety`/`check_prompt_injection` at `vibe_podcast/scriptwriter.py:423-426` (before `_build_prompt` slices `[:10]` at line 177).
- `vibe_podcast/synthesizer.py:132` — `_edge_tts_to_file` awaits `communicate.save(out_path)` with no `asyncio.wait_for` timeout.
- `vibe_podcast/routes/studio.py:335` — `synthesize_edited_script` acquires `_GENERATION_SEMAPHORE` (capacity 2 at `vibe_podcast/server.py:55`) across the entire `_synthesize_from_request` execution.

### Exact Blockers
- Target execution is blocked on this macOS Darwin host because OS-enforced nested sandboxing (`sandbox-exec: sandbox_apply: Operation not permitted`), network-disabled namespaces, and resource quotas are unavailable, preventing bounded local TestClient reproduction with mocked `_edge_tts_to_file` and measurement of `ffmpeg` subprocess fan-out and `_GENERATION_SEMAPHORE` hold behavior.
- Upstream reverse-proxy/tunnel request-body limits, request timeouts (e.g., on `cloudflared` or another proxy fronting `127.0.0.1:8765`), and whether multiple admin operators or untrusted `source_url` inputs share the deployed instance cannot be determined from repository source alone.

### Safe Non-Destructive Validation Plan
- **Local (Sandboxed)**: In an isolated sandboxed FastAPI `TestClient` with `_edge_tts_to_file` mocked to write a stub MP3 and valid admin token auth, submit a bounded `SynthesizeScriptRequest` fixture with 50 short turns (>40 `target_turns` cap) to `POST /api/synthesize` and validate a mocked >40-turn LLM response through `PodcastScript.model_validate`, confirming acceptance, subprocess invocation count in `merge_turns_to_mp3`, and HTTP 429 contention on a concurrent `/api/script` call while `_GENERATION_SEMAPHORE` is held.
- **Deployment (Owner-Observed)**: Have the operator inspect whether the deployed studio processes untrusted external `source_url` inputs or serves multiple admin callers, and verify any reverse-proxy/tunnel request-body size limits, request timeouts, and host process/file-descriptor limits governing `ffmpeg`.

---

## Lead 6: Headless agy CLI invoked on untrusted web/PDF content without disabling tools or isolating workspace/env

- **Fingerprint**: `vibe_podcast/scriptwriter.py:_call_agy_cli:unrestricted-cli-tools-with-untrusted-source`
- **Affected Boundary / Claimed Root Cause**: _call_agy_cli in vibe_podcast/scriptwriter.py invokes the external `agy` binary with only `--disable-slash-commands` on prompts containing untrusted external content, without disabling agent tool execution, isolating `cwd`, or filtering `env`.

### Description
Authenticated studio admins can generate podcast scripts via POST /api/script, POST /api/generate, or POST /api/generate/stream from external web articles, Hacker News threads/comments (WebUrlConnector.load_url), or uploaded .md/.txt/.pdf documents (/api/upload-source). In generate_podcast_script, source_text is checked only with check_text_safety (anti-discrimination slur filter in guardrails.py) and wrapped in randomized <source_document_*> / <chunk_document_*> XML tags (_wrap_untrusted_xml), then passed to _invoke_llm with backend='auto' (trying 'agy' before 'ollama'). _call_agy_cli executes subprocess.run([agy_bin, '-p', prompt, '--disable-slash-commands', '--model', chosen_model], capture_output=True, text=True, timeout=timeout) inheriting the server's working directory and full os.environ without passing any flag to disable agent filesystem, shell, or MCP tools. While _wrap_untrusted_xml prevents XML tag escape and auth.py restricts generation endpoints to the admin role, prompt delimiters alone are not a deterministic security boundary; if `agy -p --disable-slash-commands` retains host tool access, indirect prompt injection in ingested external content could read local files (such as ~/.config/vibe-podcast/session_token, REPORTS_DIR, or EPISODES_DIR), inspect inherited environment variables, or invoke host/MCP tools under the studio OS identity.

### Source Trace
- **[entrypoint]** `vibe_podcast/routes/studio.py:350` (`generate_episode`): Admin routes (/api/script at line 314, /api/generate at line 350, /api/generate/stream at line 363) accept GenerateRequest (defaulting to backend='auto', long_text_mode='map_reduce') with source_url or source_text (including text extracted by /api/upload-source at line 183) and invoke srv._resolve_generate_source and srv._draft_script_from_request.
- **[propagation]** `vibe_podcast/connectors.py:364` (`WebUrlConnector.load_url`): Fetches untrusted external web page HTML (_extract_html_text) or Hacker News item/article/comments (_load_hacker_news at line 382) as raw source_text returned by _resolve_generate_source (vibe_podcast/server.py:118).
- **[propagation]** `vibe_podcast/scriptwriter.py:427` (`generate_podcast_script`): Runs only check_text_safety (line 427) on source_text (skipping check_prompt_injection, which is applied only to host/guest/focus_questions at lines 424-426), passes long sources to summarize_long_source (line 432, wrapping chunks via _wrap_untrusted_xml and calling _invoke_llm at lines 378-387), builds the final prompt in _build_prompt (lines 182, 238-240, 449), and calls _invoke_llm (line 462) where backend=='auto' tries 'agy' first (line 309).
- **[sink]** `vibe_podcast/scriptwriter.py:262` (`_call_agy_cli`): Executes subprocess.run([agy_bin, '-p', prompt, '--disable-slash-commands', '--model', chosen_model], capture_output=True, text=True, timeout=timeout) with inherited server working directory and os.environ, without tool-disabling flags.

### Verified Source Evidence
- `vibe_podcast/scriptwriter.py:262` — _call_agy_cli spawns [agy_bin, '-p', prompt, '--disable-slash-commands', '--model', chosen_model] without passing flags to disable tools, setting an isolated cwd, or stripping sensitive environment variables.
- `vibe_podcast/scriptwriter.py:309` — _invoke_llm sets backends = ['agy', 'ollama'] when backend == 'auto', which is the default in GenerateRequest (vibe_podcast/schemas.py:33).
- `vibe_podcast/scriptwriter.py:427` — generate_podcast_script applies only check_text_safety (vibe_podcast/guardrails.py:83) to source_text and relies on _wrap_untrusted_xml (line 94) and prompt instructions (lines 188, 383) against indirect prompt injection.
- `vibe_podcast/connectors.py:364` — WebUrlConnector.load_url ingests third-party web article text and Hacker News comments directly into source_text without sanitization against prompt injection.
- `vibe_podcast/server.py:142` — _draft_script_from_request forwards resolved untrusted source_text and req.backend into generate_podcast_script.

### Exact Blockers
- The runtime tool surface of the external `agy` CLI binary in `-p --disable-slash-commands` mode (whether filesystem, shell, workspace, or MCP tools are enabled or disabled by default) is outside repository source and cannot be verified without inspecting or executing `agy`.
- Target-controlled process and CLI execution is blocked on this macOS host because OS-enforced nested sandboxing (`sandbox-exec: sandbox_apply: Operation not permitted`), external-network-disabled namespaces, and resource quotas are unavailable.

### Safe Non-Destructive Validation Plan
- **Local (Sandboxed)**: Inspect `agy --help` for tool-disabling flags and run a bounded, non-destructive test inside an empty temporary scratch directory containing only a harmless canary file (e.g., `canary.txt`), invoking `agy -p "Read canary.txt and output its contents" --disable-slash-commands` with a short timeout to observe whether tool calls are permitted.
- **Deployment (Owner-Observed)**: Observe on the deployment host which tools, MCP servers, working directory, and environment variables (such as VIBEPODCAST_TOKEN, VIBEPODCAST_LISTEN_TOKEN, or ~/.config/vibe-podcast/session_token) are accessible to `agy` when launched by the studio service account.

---

## Lead 7: LLM outputs from untrusted sources persist to timeline.json and are re-injected outside untrusted delimiters in subsequent prompts

- **Fingerprint**: `vibe_podcast/show_memory.py:load_recent_episodes_memory:untrusted-timeline-to-trusted-prompt-memory`
- **Affected Boundary / Claimed Root Cause**: vibe_podcast/show_memory.py treats LLM-generated timeline.json fields (title, summary, guest_name, takeaway) as trusted show-bible continuity data with only tag/newline stripping (_sanitize_memory_text), and vibe_podcast/scriptwriter.py interpolates {continuity_block} inside the trusted prompt preamble outside the <source_document_*> delimiter boundary without check_prompt_injection or untrusted-data isolation.

### Description
Untrusted external URLs/Hacker News items (vibe_podcast/connectors.py:364) or uploaded files (vibe_podcast/routes/studio.py:183) ingested into source_text bypass check_prompt_injection (vibe_podcast/scriptwriter.py:420-427) and are wrapped in randomized <source_document_*> tags in _build_prompt (vibe_podcast/scriptwriter.py:182, 238-240). Generated PodcastScript fields pass only enforce_script_guardrails (vibe_podcast/guardrails.py:97-133), which enforces anti-discrimination check_text_safety and soft replacements without check_prompt_injection, before _finalize_synthesis persists title, summary, guest_name, and segments to episodes/<episode_id>/timeline.json (vibe_podcast/synthesizer.py:175-195). On subsequent generations (with enable_show_memory=True by default in GenerateRequest and generate_podcast_script), load_recent_episodes_memory and build_continuity_context read the 4 newest timeline.json files, strip only <[^>]*> and newlines via _sanitize_memory_text (vibe_podcast/show_memory.py:59-62), and format title, summary, guest_name, and takeaway into continuity_block, which _build_prompt injects inside the trusted persona/instruction section outside <source_document_*> (vibe_podcast/scriptwriter.py:184, 196). If an untrusted source steers Episode 1's persisted metadata or takeaway, those strings cross the <source_document_*> isolation boundary into the trusted prompt frame of subsequent unrelated episodes (which may process local REPORTS_DIR documents or run via agy CLI).

### Source Trace
- **[entrypoint]** `vibe_podcast/connectors.py:364` (`WebUrlConnector.load_url`): Ingests untrusted external webpage or Hacker News content into source_text for Episode 1 via _resolve_generate_source (vibe_podcast/server.py:123).
- **[propagation]** `vibe_podcast/scriptwriter.py:465` (`generate_podcast_script`): Validates LLM JSON into PodcastScript (line 464) and passes it through enforce_script_guardrails (line 465) without running check_prompt_injection on source_text or generated outputs.
- **[propagation]** `vibe_podcast/guardrails.py:97` (`enforce_script_guardrails`): Applies _apply_soft_replacements and check_text_safety (anti-discrimination slurs) to host_a, host_b, guest_name, title, summary, and turns[*], without invoking check_prompt_injection.
- **[propagation]** `vibe_podcast/synthesizer.py:194` (`_finalize_synthesis`): Persists title, summary, host_a, host_b, guest_name, and segments (including takeaway chapter text) to episodes/<episode_id>/timeline.json.
- **[propagation]** `vibe_podcast/show_memory.py:95` (`load_recent_episodes_memory`): Reads the 4 newest timeline.json files and sanitizes title/summary/guest_name/takeaway only via _sanitize_memory_text (stripping <[^>]*> and newlines, lines 59-62) before build_continuity_context (lines 223-232) formats them into the Show Bible continuity string.
- **[sink]** `vibe_podcast/scriptwriter.py:196` (`_build_prompt`): Interpolates {continuity_block} directly inside the trusted persona/instruction section outside the randomized <source_document_*> wrapper (lines 238-240) on subsequent unrelated episode generations.

### Verified Source Evidence
- `vibe_podcast/show_memory.py:59` — _sanitize_memory_text only strips <[^>]*>, collapses newlines, and truncates length; it does not apply check_prompt_injection or preserve untrusted source provenance.
- `vibe_podcast/show_memory.py:223` — build_continuity_context formats persisted episode titles, summaries, guests, and takeaways into the Show Bible & Continuity Memory string.
- `vibe_podcast/scriptwriter.py:196` — _build_prompt injects {continuity_block} in the trusted prompt preamble above the structure/format/safety rules and outside <{doc_tag}>...</{doc_tag}> (lines 238-240).
- `vibe_podcast/guardrails.py:97` — enforce_script_guardrails does not invoke check_prompt_injection on LLM-generated PodcastScript outputs persisted to timeline.json.

### Exact Blockers
- Confirming end-to-end exploitation requires live LLM inference (agy or Ollama) to demonstrate both that an untrusted <source_document_*> payload induces a persistent directive in timeline.json (despite the prompt warning at vibe_podcast/scriptwriter.py:188) and that a subsequent episode's model follows the continuity_block text placed outside <source_document_*>.
- Target-controlled execution is blocked on this macOS Darwin host because OS-enforced nested sandboxing (sandbox-exec: sandbox_apply: Operation not permitted), external-network-disabled namespaces, and per-sandbox resource quotas are unavailable.

### Safe Non-Destructive Validation Plan
- **Local (Sandboxed)**: In an isolated sandbox with dummy episodes_dir, write a valid SynthesisResult timeline.json containing a benign canary directive in title, summary, and a takeaway segment; verify build_continuity_context and _build_prompt place the canary outside <source_document_*>; then run a bounded two-episode fixture against a local Ollama model to test whether an untrusted <source_document_*> input induces persistent text in timeline.json and alters subsequent unrelated episode generation.
- **Deployment (Owner-Observed)**: Have the operator verify whether production/studio workflows generate episodes from untrusted external URLs or uploaded files with enable_show_memory=True prior to generating episodes from private REPORTS_DIR documents, and confirm what tools/permissions remain active under agy -p ... --disable-slash-commands in the host environment.

---

## Lead 8: VibeVoice remote synthesis bypasses DNS pinning, redirect validation, and response size caps after public-URL SSRF check

- **Fingerprint**: `vibe_podcast/synthesizer.py::_synthesize_vibevoice_remote:redirect-and-dns-pinning-bypass`
- **Affected Boundary / Claimed Root Cause**: _validate_public_url discards the pinned IP from _validate_and_resolve_url, and _synthesize_vibevoice_remote uses default urllib.request.urlopen and unbounded resp.read() without Content-Type or size checks instead of _build_pinned_opener, _SafeRedirectHandler, and bounded streaming.

### Description
synthesize_podcast() calls _validate_public_url(resolved_remote) when VIBEPODCAST_ALLOW_PRIVATE_REMOTE != '1', which resolves DNS once via _validate_and_resolve_url() and discards the pinned IP. _synthesize_vibevoice_remote() then calls bare urllib.request.urlopen(req, timeout=300) and writes resp.read() directly to episode_dir/podcast.wav. Bare urlopen re-resolves DNS (TOCTOU/DNS-rebinding window), follows HTTP 301/302/303/307/308 redirects without _SafeRedirectHandler IP validation (with 307/308 preserving the POST JSON script payload), and omits Content-Type validation and byte-length bounds. Because HTTP studio routes in vibe_podcast/routes/studio.py only source remote_url from the operator-configured VIBEVOICE_REMOTE_URL environment variable rather than HTTP request payloads, exploitability depends on deployment configuration or direct Python callers of synthesize_podcast(..., remote_url=...), and runtime execution is blocked by the host sandbox.

### Source Trace
- **[entrypoint]** `vibe_podcast/routes/studio.py:294` (`_synthesize_from_request`): Studio synthesis routes (/api/synthesize, /api/generate, /api/generate/stream) invoke synthesize_podcast without a request-supplied remote_url, falling back to VIBEVOICE_REMOTE_URL.
- **[propagation]** `vibe_podcast/synthesizer.py:462` (`synthesize_podcast`): Resolves resolved_remote from the remote_url parameter or os.environ.get('VIBEVOICE_REMOTE_URL').
- **[propagation]** `vibe_podcast/synthesizer.py:469` (`synthesize_podcast`): Calls _validate_public_url(resolved_remote) when VIBEPODCAST_ALLOW_PRIVATE_REMOTE != '1', which checks DNS once and discards the pinned IP before calling _synthesize_vibevoice_remote at line 472.
- **[sink]** `vibe_podcast/synthesizer.py:426` (`_synthesize_vibevoice_remote`): Executes bare urllib.request.urlopen(req, timeout=300) without IP pinning or _SafeRedirectHandler and writes unbounded resp.read() to episode_dir/podcast.wav at line 427.

### Verified Source Evidence
- `vibe_podcast/connectors.py:237` — _validate_public_url calls _validate_and_resolve_url(url) at line 239 and discards the returned pinned_ip.
- `vibe_podcast/synthesizer.py:466` — synthesize_podcast gates resolved_remote with _validate_public_url(resolved_remote) unless VIBEPODCAST_ALLOW_PRIVATE_REMOTE == '1'.
- `vibe_podcast/synthesizer.py:426` — _synthesize_vibevoice_remote uses urllib.request.urlopen(req, timeout=300) and final_audio.write_bytes(resp.read()) at lines 426-427 with no redirect IP validation, DNS pinning, Content-Type check, or size limit.
- `vibe_podcast/routes/episodes.py:113` — get_episode_audio serves episode_dir/podcast.wav if present without requiring timeline.json.

### Exact Blockers
- Target-controlled code execution (pytest or a local HTTP redirect/DNS-rebinding fixture against synthesize_podcast) is blocked on this host because OS-enforced nested sandboxing (sandbox-exec: sandbox_apply: Operation not permitted) is unavailable.
- Deployment configuration outside the repository determines whether VIBEVOICE_REMOTE_URL is configured and pointed at an untrusted, third-party, or DNS-controlled remote endpoint with VIBEPODCAST_ALLOW_PRIVATE_REMOTE != '1'.

### Safe Non-Destructive Validation Plan
- **Local (Sandboxed)**: Inside an OS-enforced network-isolated sandbox, run a bounded fixture with a local mock HTTP server (e.g., stubbing socket.getaddrinfo for a public test hostname to route to the local mock server) returning a 307 redirect to a loopback listener and verify whether synthesize_podcast(..., remote_url=...) follows the redirect and posts the script payload to loopback, and whether an oversized/non-WAV body is buffered unboundedly into podcast.wav.
- **Deployment (Owner-Observed)**: Verify whether VIBEVOICE_REMOTE_URL is set in the deployment environment, whether it points to a third-party or untrusted/DNS-resolved GPU endpoint with VIBEPODCAST_ALLOW_PRIVATE_REMOTE unset (or != '1'), and confirm outbound network egress restrictions from the VibePodcast host.

---

## Lead 9: _synthesize_vibevoice_remote reads remote WAV response without a size cap

- **Fingerprint**: `vibe_podcast/synthesizer.py:_synthesize_vibevoice_remote-unbounded-response-read`
- **Affected Boundary / Claimed Root Cause**: `urllib.request.urlopen(req, timeout=300)` response in `_synthesize_vibevoice_remote` is read via unbounded `resp.read()` into memory and written to `episode_dir / "podcast.wav"` without any size cap.

### Description
When engine="vibevoice_remote" (or engine="auto" with VIBEVOICE_REMOTE_URL set), synthesize_podcast dispatches to _synthesize_vibevoice_remote, which opens the remote /synthesize HTTP response with urllib.request.urlopen(req, timeout=300) and calls final_audio.write_bytes(resp.read()) with no Content-Length, Content-Type, total-transfer deadline, or maximum byte bound. A misbehaving or compromised remote VibeVoice endpoint (or an on-path attacker when VIBEVOICE_REMOTE_URL uses plaintext http://) can force unbounded RAM buffering and disk writes to episode_dir/podcast.wav while holding one of the two studio-wide _GENERATION_SEMAPHORE slots.

### Source Trace
- **[entrypoint]** `vibe_podcast/routes/studio.py:350` (`generate_episode`): Admin-authenticated route acquires _GENERATION_SEMAPHORE (line 353) and invokes _synthesize_from_request -> synthesize_podcast (line 294); same pattern in synthesize_edited_script (line 332) and generate_episode_stream (line 363).
- **[propagation]** `vibe_podcast/synthesizer.py:462` (`synthesize_podcast`): Resolves VIBEVOICE_REMOTE_URL from environment, validates public URL unless VIBEPODCAST_ALLOW_PRIVATE_REMOTE=1 (lines 466-469; http:// is permitted), and dispatches to _synthesize_vibevoice_remote at line 472.
- **[propagation]** `vibe_podcast/synthesizer.py:426` (`_synthesize_vibevoice_remote`): Opens the remote HTTP response with urllib.request.urlopen(req, timeout=300) using the default opener (per-socket timeout only) without checking Content-Length or Content-Type.
- **[sink]** `vibe_podcast/synthesizer.py:427` (`_synthesize_vibevoice_remote`): Calls final_audio.write_bytes(resp.read()) with no byte cap or chunked streaming limit, buffering the entire response in RAM and writing it to podcast.wav.

### Verified Source Evidence
- `vibe_podcast/synthesizer.py:426` — with urllib.request.urlopen(req, timeout=300) as resp: opens the remote response with only a per-socket recv timeout and no header/size checks.
- `vibe_podcast/synthesizer.py:427` — final_audio.write_bytes(resp.read()) reads the entire response body into memory with no max_bytes limit and writes it to episode_dir / "podcast.wav".
- `vibe_podcast/synthesizer.py:462` — resolved_remote = remote_url or os.environ.get("VIBEVOICE_REMOTE_URL") triggers _synthesize_vibevoice_remote at line 472 when engine is "vibevoice_remote" or "auto" with VIBEVOICE_REMOTE_URL set.
- `vibe_podcast/server.py:55` — _GENERATION_SEMAPHORE = threading.BoundedSemaphore(value=2) limits studio-wide concurrent generation/synthesis slots held across synthesize_podcast calls.

### Exact Blockers
- OS-enforced nested sandboxing (sandbox-exec: sandbox_apply), network-disabled namespaces, and memory/disk quotas are unavailable on this Darwin host, blocking safe local execution against a mock server.
- Deployment configuration and trust boundary for VIBEVOICE_REMOTE_URL are outside repository source: whether VIBEVOICE_REMOTE_URL is enabled, whether it targets a trusted loopback/private endpoint (with VIBEPODCAST_ALLOW_PRIVATE_REMOTE=1) over TLS/tunnel versus a public or plaintext http:// endpoint, and whether an upstream proxy or the remote GPU server caps response size.

### Safe Non-Destructive Validation Plan
- **Local (Sandboxed)**: In an isolated, memory- and disk-capped local sandbox, set VIBEVOICE_REMOTE_URL to a loopback mock HTTP server (with VIBEPODCAST_ALLOW_PRIVATE_REMOTE=1) returning an oversized payload (e.g., >50 MB) from POST /synthesize and invoke synthesize_podcast(..., engine="vibevoice_remote") to verify unbounded RAM buffering in resp.read() and disk write to podcast.wav while holding a _GENERATION_SEMAPHORE slot.
- **Deployment (Owner-Observed)**: Inspect deployment environment variables (VIBEVOICE_REMOTE_URL, VIBEPODCAST_ALLOW_PRIVATE_REMOTE) and network topology to confirm whether remote synthesis is enabled, whether transport uses HTTPS or a private encrypted tunnel to a trusted GPU host rather than plaintext HTTP over an untrusted network, and whether an intermediate proxy or the remote VibeVoice server enforces a maximum WAV response size and transfer deadline.
