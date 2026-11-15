"""Pluggable source connectors for decoupling input providers from VibePodcast."""

from __future__ import annotations

import fnmatch
import html
import ipaddress
import json
import logging
import os
import re
import socket
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Protocol

from pydantic import BaseModel

logger = logging.getLogger("vibe_podcast.connectors")


class SourceItem(BaseModel):
    """Metadata for a discoverable source document."""

    id: str
    connector: str
    name: str
    path: str
    preview: str


class SourceConnector(Protocol):
    """Protocol for extensible input source connectors."""

    name: str

    def list_items(self) -> List[SourceItem]:
        """List available source items from this connector."""
        ...

    def load_text(self, item_path: str) -> str:
        """Load full text content for a selected item."""
        ...


class MarkdownDirConnector:
    """Scans a directory for Markdown reports (*.md) safely within root bounds."""

    name = "markdown_dir"

    def __init__(self, root_dir: Path, filename_filter: Optional[str] = None) -> None:
        self.root_dir = Path(root_dir)
        self.filename_filter = filename_filter

    def list_items(self) -> List[SourceItem]:
        items: List[SourceItem] = []
        if not self.root_dir.exists():
            return items

        allowed_root = self.root_dir.resolve()
        pattern = self.filename_filter or "*.md"
        max_depth = 5
        candidate_paths: List[Path] = []
        for dirpath, dirnames, filenames in os.walk(allowed_root, followlinks=False):
            current = Path(dirpath)
            rel_parts = current.relative_to(allowed_root).parts
            if len(rel_parts) >= max_depth:
                dirnames[:] = []
            else:
                dirnames[:] = sorted(
                    d for d in dirnames if not (current / d).is_symlink()
                )
            for fname in sorted(filenames):
                if fnmatch.fnmatch(fname, pattern):
                    fpath = current / fname
                    if not fpath.is_symlink() and fpath.is_file():
                        candidate_paths.append(fpath)

        for path in sorted(candidate_paths):
            try:
                resolved = path.resolve()
                if not resolved.is_relative_to(allowed_root):
                    continue
                with resolved.open("r", encoding="utf-8", errors="replace") as fh:
                    preview_raw = fh.read(300)
                rel = resolved.relative_to(allowed_root)
                label = str(rel.parent) if str(rel.parent) != "." else rel.stem
                items.append(
                    SourceItem(
                        id=f"{self.name}:{rel}",
                        connector=self.name,
                        name=label if label != "." else resolved.name,
                        path=str(resolved),
                        preview=preview_raw[:180].replace("\n", " "),
                    )
                )
            except Exception as exc:
                logger.warning("Failed to read %s: %s", path, exc)
        return items

    def load_text(self, item_path: str) -> str:
        allowed_root = self.root_dir.resolve()
        raw_input = Path(item_path)
        raw_path = raw_input if raw_input.is_absolute() else (allowed_root / raw_input)
        probe = allowed_root
        try:
            rel_parts = raw_path.relative_to(allowed_root).parts if raw_path.is_absolute() else ()
        except ValueError:
            rel_parts = ()
        if raw_path.is_symlink() or any((probe := probe / part).is_symlink() for part in rel_parts):
            raise PermissionError("Symlinks are not allowed in reports directory")
        candidate = raw_path.resolve()
        if not candidate.is_relative_to(allowed_root) or candidate.suffix.lower() != ".md":
            raise PermissionError("Path is outside allowed reports directory")
        if not candidate.is_file():
            raise FileNotFoundError(f"Source file not found: {candidate}")
        if candidate.stat().st_size > 2 * 1024 * 1024:
            raise PermissionError("Report file exceeds maximum allowed size")
        return candidate.read_text(encoding="utf-8").strip()


class ConnectorRegistry:
    """Holds registered source connectors for unified discovery and loading."""

    def __init__(self) -> None:
        self._connectors: Dict[str, SourceConnector] = {}

    def register(self, connector: SourceConnector) -> None:
        self._connectors[connector.name] = connector

    def list_all_items(self) -> List[SourceItem]:
        items: List[SourceItem] = []
        for conn in self._connectors.values():
            items.extend(conn.list_items())
        return items

    def load_text(self, item_path: str) -> str:
        last_err: Optional[Exception] = None
        for conn in self._connectors.values():
            try:
                return conn.load_text(item_path)
            except (PermissionError, FileNotFoundError) as exc:
                last_err = exc
        if last_err:
            raise last_err
        raise FileNotFoundError("No connector could load the requested path")


class _ReadableTextParser(HTMLParser):
    """Lightweight stdlib HTML-to-text extractor skipping boilerplate tags."""

    _SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._in_title = False
        self.title_parts: List[str] = []
        self.text_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS:
            self._skip_depth += 1
        elif t == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif t == "title":
            self._in_title = False
        elif t in {"p", "h1", "h2", "h3", "li", "br", "div", "article"} and self._skip_depth == 0:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self.text_parts.append(stripped + " ")


_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


def _is_public_ip(raw_addr: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Parse an IP address (stripping IPv6 zone IDs) and verify it is globally routable."""
    clean_addr = raw_addr.split("%", 1)[0]
    raw_ip = ipaddress.ip_address(clean_addr)
    ip = raw_ip.ipv4_mapped if isinstance(raw_ip, ipaddress.IPv6Address) and raw_ip.ipv4_mapped else raw_ip
    if (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NET)
    ):
        raise PermissionError("基於安全考量，禁止存取本機或內網 IP 位址")
    return ip


def _validate_and_resolve_url(url: str) -> tuple[urllib.parse.ParseResult, str]:
    """Validate URL scheme, resolve DNS once, and return (parsed, pinned_ip)."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("僅支援 http:// 或 https:// 公開網址")

    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"}:
        raise PermissionError("基於安全考量，禁止存取本機或內網位址")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"無法解析網域名稱：{host}") from exc

    pinned_ip: str | None = None
    for _, _, _, _, sockaddr in infos:
        ip = _is_public_ip(sockaddr[0])
        if pinned_ip is None:
            pinned_ip = str(ip)

    if pinned_ip is None:
        raise ValueError(f"無法解析網域名稱：{host}")

    return parsed, pinned_ip


def _validate_public_url(url: str) -> urllib.parse.ParseResult:
    """Validate URL scheme and block private/loopback/link-local targets (SSRF guard)."""
    parsed, _ = _validate_and_resolve_url(url)
    return parsed


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate every HTTP redirect target against SSRF rules and cap hops."""

    def __init__(self, pinned_ips: dict[tuple[str, int], str] | None = None, max_redirects: int = 3):
        super().__init__()
        self._pinned_ips = pinned_ips
        self._max_redirects = max_redirects
        self._redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._redirect_count += 1
        if self._redirect_count > self._max_redirects:
            raise PermissionError("重導向次數過多，已中止擷取")
        parsed, ip = _validate_and_resolve_url(newurl)
        if self._pinned_ips is not None:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            self._pinned_ips[(parsed.hostname.lower(), port)] = ip
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_pinned_opener(pinned_ips: dict[tuple[str, int], str]) -> urllib.request.OpenerDirector:
    """Build a urllib opener that connects directly to validated IPs (prevents DNS rebinding)."""
    import http.client
    import ssl

    class _PinnedHTTPConnection(http.client.HTTPConnection):
        def connect(self) -> None:
            target_ip = pinned_ips.get((self.host.lower(), self.port))
            if not target_ip:
                raise PermissionError("Missing pinned IP for target host")
            self.sock = socket.create_connection((target_ip, self.port), self.timeout, self.source_address)

    class _PinnedHTTPSConnection(http.client.HTTPSConnection):
        def connect(self) -> None:
            target_ip = pinned_ips.get((self.host.lower(), self.port))
            if not target_ip:
                raise PermissionError("Missing pinned IP for target host")
            sock = socket.create_connection((target_ip, self.port), self.timeout, self.source_address)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)

    class _PinnedHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(_PinnedHTTPConnection, req)

    class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
        def __init__(self):
            super().__init__(context=ssl.create_default_context())

        def https_open(self, req):
            return self.do_open(_PinnedHTTPSConnection, req, context=self._context)

    return urllib.request.build_opener(
        _PinnedHTTPHandler(), _PinnedHTTPSHandler(), _SafeRedirectHandler(pinned_ips)
    )


_ALLOWED_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xhtml+xml",
    "application/xml",
)


def _read_bounded_stream(
    resp,
    max_bytes: int,
    deadline: Optional[float] = None,
    *,
    exceeded_error: Optional[str] = None,
) -> bytes:
    """Read an HTTP response stream in chunks while enforcing size and deadline limits."""
    chunks: List[bytes] = []
    total = 0
    chunk_size = 16_384
    while True:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("External fetch deadline exceeded")
        remaining = max_bytes - total
        to_read = min(chunk_size, remaining) if exceeded_error is None else min(chunk_size, remaining + 1)
        if to_read <= 0:
            break
        part = resp.read(to_read)
        if not part:
            break
        total += len(part)
        if total > max_bytes:
            if exceeded_error is not None:
                raise ValueError(exceeded_error)
            chunks.append(part[: max_bytes - (total - len(part))])
            break
        chunks.append(part)
        if exceeded_error is None and total >= max_bytes:
            break
    return b"".join(chunks)


def _http_get_bytes(
    url: str,
    timeout: int = 12,
    max_bytes: int = 512_000,
    deadline: Optional[float] = None,
) -> bytes:
    effective_timeout = float(timeout)
    read_deadline = deadline if deadline is not None else (time.monotonic() + effective_timeout)
    remaining = read_deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("External fetch deadline exceeded")
    effective_timeout = min(effective_timeout, max(1.0, remaining))

    parsed, pinned_ip = _validate_and_resolve_url(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    opener = _build_pinned_opener({(parsed.hostname.lower(), port): pinned_ip})
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "LaoMaVibePodcast/0.3.0 (+https://github.com/tonnychiulab/vibe-podcast)"},
    )
    with opener.open(req, timeout=effective_timeout) as resp:
        headers = getattr(resp, "headers", None)
        ctype = ((headers.get("Content-Type") if headers else None) or "").split(";", 1)[0].strip().lower()
        if ctype and not ctype.startswith(_ALLOWED_CONTENT_TYPES):
            raise ValueError(f"不支援的網頁內容格式：{ctype}")
        return _read_bounded_stream(resp, max_bytes=max_bytes, deadline=read_deadline)


def _decode_http_bytes(raw_bytes: bytes) -> str:
    """Decode HTML/text bytes using declared meta charset or common CJK/UTF-8 fallbacks."""
    head = raw_bytes[:2048].decode("ascii", errors="ignore")
    match = re.search(r"charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)", head, re.IGNORECASE)
    candidates: List[str] = []
    if match:
        candidates.append(match.group(1))
    candidates.extend(["utf-8", "cp950", "big5"])
    for enc in candidates:
        try:
            return raw_bytes.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _extract_html_text(raw_html: str) -> tuple[str, str]:
    parser = _ReadableTextParser()
    parser.feed(raw_html)
    title = html.unescape("".join(parser.title_parts)).strip()
    body = re.sub(r"\n{3,}", "\n\n", html.unescape("".join(parser.text_parts))).strip()
    return title, body


class WebUrlConnector:
    """Fetches public web articles and Hacker News threads/frontpage as podcast source text."""

    name = "web_url"

    def load_url(self, url: str) -> str:
        pre_parsed = urllib.parse.urlparse((url or "").strip())
        if (pre_parsed.hostname or "").lower() == "news.ycombinator.com":
            return self._load_hacker_news(_validate_public_url(url))

        raw = _decode_http_bytes(_http_get_bytes(url))
        title, body = _extract_html_text(raw)
        if not body:
            raise ValueError("無法從該網址擷取到足夠的文字內容")
        return f"# {title}\n\n{body[:11000]}" if title else body[:11000]

    def _fetch_hn_item(self, item_id: int, deadline: Optional[float] = None) -> dict:
        data = _http_get_bytes(
            f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json",
            deadline=deadline,
        )
        return json.loads(data.decode("utf-8"))

    def _load_hacker_news(self, parsed: urllib.parse.ParseResult) -> str:
        deadline = time.monotonic() + 25.0
        qs = urllib.parse.parse_qs(parsed.query)
        if "id" in qs and qs["id"][0].isascii() and qs["id"][0].isdigit():
            item_id = int(qs["id"][0])
            item = self._fetch_hn_item(item_id, deadline=deadline)
            if not isinstance(item, dict):
                raise ValueError(f"找不到 Hacker News 貼文 #{item_id}")
            title = item.get("title", f"Hacker News #{item_id}")
            lines = [f"# Hacker News 熱門討論：{title}"]
            if item.get("url"):
                lines.append(f"原文連結：{item['url']}")
                try:
                    article_raw = _decode_http_bytes(
                        _http_get_bytes(item["url"], deadline=deadline)
                    )
                    _, article_body = _extract_html_text(article_raw)
                    if article_body:
                        lines.append(f"\n## 原文摘要內容\n{article_body[:6000]}")
                except Exception as exc:
                    logger.info("Skipped external HN article fetch (%s): %s", item["url"], exc)
            if item.get("text"):
                _, ask_text = _extract_html_text(item["text"])
                lines.append(f"\n## 樓主說明\n{ask_text[:2000]}")

            kids = item.get("kids") or []
            comments: List[str] = []
            for kid_id in kids[:8]:
                if time.monotonic() >= deadline:
                    break
                try:
                    c = self._fetch_hn_item(int(kid_id), deadline=deadline)
                    if c and not c.get("deleted") and c.get("text"):
                        _, c_text = _extract_html_text(c["text"])
                        author = c.get("by", "anon")
                        comments.append(f"- {author}: {c_text[:500]}")
                except Exception:
                    continue
            if comments:
                lines.append("\n## Hacker News 社群高讚留言觀點\n" + "\n".join(comments))
            return "\n".join(lines)[:12000]

        raw_top = json.loads(
            _http_get_bytes(
                "https://hacker-news.firebaseio.com/v0/topstories.json",
                deadline=deadline,
            ).decode("utf-8")
        )
        top_ids = raw_top[:5] if isinstance(raw_top, list) else []
        lines = ["# 今日 Hacker News 熱門科技頭條彙整\n"]
        for rank, sid in enumerate(top_ids, start=1):
            if time.monotonic() >= deadline:
                break
            try:
                story = self._fetch_hn_item(int(sid), deadline=deadline)
                lines.append(
                    f"{rank}. {story.get('title', 'Untitled')} "
                    f"(熱度 {story.get('score', 0)} 分, {story.get('descendants', 0)} 則留言)"
                )
            except Exception:
                continue
        return "\n".join(lines)
