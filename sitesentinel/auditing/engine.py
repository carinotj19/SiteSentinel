"""Bounded, SSRF-conscious, static HTML audits for monitored public websites.

No JavaScript execution or arbitrary recursive crawling. All HTTP requests use the
existing DNS allowlist, public-IP pinning, and TLS validation.
"""

from __future__ import annotations

import http.client
import re
import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from sitesentinel.monitoring.scanner import (
    MAX_REDIRECTS,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
    UnsafeTarget,
    _PinnedHTTPConnection,
    _PinnedHTTPSConnection,
    normalize_url,
    resolve_public_ip,
    scan_url,
)

MAX_HTML_BYTES = 256 * 1024
DEFAULT_LINK_LIMIT = 5
MAX_LINK_LIMIT = 10
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
GA4_ID = re.compile(r"\bG-[A-Z0-9]{6,15}\b", re.I)
GTM_ID = re.compile(r"\bGTM-[A-Z0-9]{4,16}\b", re.I)
PIXEL_INIT = re.compile(r"""fbq\s*\(\s*['"]init['"]\s*,\s*['"](\d{5,20})['"]""", re.I)


@dataclass(frozen=True)
class PageResponse:
    status_code: int
    final_url: str
    content_type: str
    body: bytes


def _fetch_once(url: str) -> tuple[int, str | None, str, bytes]:
    """Fetch one bounded response from a DNS-vetted pinned public IP."""
    parts = urlsplit(normalize_url(url))
    hostname = parts.hostname
    assert hostname is not None
    is_https = parts.scheme == "https"
    port = 443 if is_https else 80
    ip = resolve_public_ip(hostname, port)
    if is_https:
        conn = _PinnedHTTPSConnection(
            hostname, port, ip, timeout=REQUEST_TIMEOUT_SECONDS, context=ssl.create_default_context()
        )
    else:
        conn = _PinnedHTTPConnection(hostname, port, ip, timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        conn.connect()
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        conn.request(
            "GET", target,
            headers={
                "User-Agent": USER_AGENT,
                "Connection": "close",
                "Accept": "text/html",
                "Accept-Encoding": "identity",
            },
        )
        response = conn.getresponse()
        status = response.status
        location = response.getheader("Location")
        content_type = response.getheader("Content-Type", "").lower()
        if status in REDIRECT_CODES or not 200 <= status < 300:
            return status, location, content_type, b""
        if not (content_type.startswith("text/html") or content_type.startswith("application/xhtml+xml")):
            return status, location, content_type, b""
        length = response.getheader("Content-Length")
        if length and length.isdecimal() and int(length) > MAX_HTML_BYTES:
            raise ValueError("HTML page exceeds the 256 KiB audit limit.")
        body = response.read(MAX_HTML_BYTES + 1)
        if len(body) > MAX_HTML_BYTES:
            raise ValueError("HTML page exceeds the 256 KiB audit limit.")
        return status, location, content_type, body
    finally:
        conn.close()


def fetch_page(url: str) -> PageResponse:
    """Resolve each redirect anew and never fetch private network addresses."""
    current = normalize_url(url)
    for step in range(MAX_REDIRECTS + 1):
        status, location, content_type, body = _fetch_once(current)
        if status not in REDIRECT_CODES:
            return PageResponse(status, current, content_type, body)
        if not location:
            raise ValueError("Redirect response is missing Location.")
        if step == MAX_REDIRECTS:
            raise ValueError("Page redirected too many times.")
        current = normalize_url(urljoin(current, location))
    raise ValueError("Page redirected too many times.")


class _DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titles: list[str] = []
        self.h1s: list[str] = []
        self.description: str | None = None
        self.robots: str | None = None
        self.canonical: str | None = None
        self.hrefs: list[str] = []
        self.script_sources: list[str] = []
        self.script_text: list[str] = []
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("title", "h1", "script"):
            self._capture = tag
            self._buffer = []
        if tag == "a" and attributes.get("href"):
            self.hrefs.append(attributes["href"])
        if tag == "script" and attributes.get("src"):
            self.script_sources.append(attributes["src"])
        if tag == "meta":
            name = (attributes.get("name") or "").lower()
            if name == "description" and self.description is None:
                self.description = (attributes.get("content") or "").strip()
            if name == "robots" and self.robots is None:
                self.robots = (attributes.get("content") or "").strip()
        if tag == "link" and self.canonical is None:
            if "canonical" in (attributes.get("rel") or "").lower().split():
                self.canonical = attributes.get("href")

    def handle_data(self, data):
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag):
        if self._capture != tag:
            return
        value = " ".join(" ".join(self._buffer).split()).strip()
        if tag == "title" and value:
            self.titles.append(value)
        elif tag == "h1" and value:
            self.h1s.append(value)
        elif tag == "script" and value:
            self.script_text.append(value)
        self._capture = None
        self._buffer = []


def analyze_html(html: str, page_url: str, link_limit: int = DEFAULT_LINK_LIMIT) -> dict:
    """Analyze HTML without executing scripts or touching external links."""
    if isinstance(link_limit, bool) or not isinstance(link_limit, int) or not 0 <= link_limit <= MAX_LINK_LIMIT:
        raise ValueError("link_limit must be an integer between 0 and 10.")
    parser = _DocumentParser()
    parser.feed(html)
    parser.close()

    issues = []
    title = parser.titles[0] if parser.titles else None
    if not title:
        issues.append("missing_title")
    if not parser.description:
        issues.append("missing_meta_description")
    if not parser.h1s:
        issues.append("missing_h1")
    if len(parser.h1s) > 1:
        issues.append("multiple_h1")
    if parser.robots and "noindex" in parser.robots.lower():
        issues.append("noindex")
    canonical = urljoin(page_url, parser.canonical) if parser.canonical else None

    script_evidence = "\n".join(parser.script_sources + parser.script_text)
    ga4_ids = sorted({match.upper() for match in GA4_ID.findall(script_evidence)})
    gtm_ids = sorted({match.upper() for match in GTM_ID.findall(script_evidence)})
    pixel_ids = sorted(set(PIXEL_INIT.findall(script_evidence)))
    tracking = {
        "ga4": {
            "detected": bool(ga4_ids or "googletagmanager.com/gtag/js" in script_evidence.lower()),
            "ids": ga4_ids,
        },
        "gtm": {"detected": bool(gtm_ids), "ids": gtm_ids},
        "meta_pixel": {
            "detected": bool(pixel_ids or "connect.facebook.net/" in script_evidence.lower()),
            "ids": pixel_ids,
        },
        "note": "Static HTML only: scripts injected by GTM, consent tools, or JavaScript may not appear.",
    }

    origin = (urlsplit(page_url).scheme, urlsplit(page_url).netloc.lower())
    candidates: list[str] = []
    seen: set[str] = set()
    skipped_external = 0
    skipped_unsupported = 0
    for href in parser.hrefs:
        if not href.strip() or href.lstrip().startswith("#"):
            continue
        try:
            candidate = normalize_url(urljoin(page_url, href))
        except (UnsafeTarget, ValueError):
            skipped_unsupported += 1
            continue
        parts = urlsplit(candidate)
        if (parts.scheme, parts.netloc.lower()) != origin:
            skipped_external += 1
            continue
        if candidate not in seen and candidate != page_url:
            seen.add(candidate)
            candidates.append(candidate)
    results = []
    for url in candidates[:link_limit]:
        result = scan_url(url)
        results.append({
            "url": url, "status": result.status, "status_code": result.status_code,
            "error": result.error,
        })
    return {
        "seo": {
            "title": title, "meta_description": parser.description,
            "h1": parser.h1s[:10], "canonical": canonical,
            "robots": parser.robots, "issues": issues,
        },
        "tracking": tracking,
        "links": {
            "candidates": len(candidates),
            "checked": len(results),
            "broken": [r for r in results if r["status"] != "up"],
            "results": results,
            "skipped_external": skipped_external,
            "skipped_unsupported": skipped_unsupported,
            "truncated": len(candidates) > link_limit,
        },
    }


def audit_site(url: str, link_limit: int = DEFAULT_LINK_LIMIT) -> dict:
    """Return persisted-friendly results; errors are findings, never arbitrary web access."""
    if isinstance(link_limit, bool) or not isinstance(link_limit, int) or not 0 <= link_limit <= MAX_LINK_LIMIT:
        raise ValueError("link_limit must be an integer between 0 and 10.")
    normalized = normalize_url(url)
    try:
        page = fetch_page(normalized)
        if not 200 <= page.status_code < 300:
            return {
                "status": "failed", "status_code": page.status_code,
                "final_url": page.final_url, "findings": {},
                "error": f"HTTP {page.status_code}",
            }
        if not page.body:
            return {
                "status": "failed", "status_code": page.status_code,
                "final_url": page.final_url, "findings": {},
                "error": "Page is not HTML or contains no HTML content.",
            }
        charset = "utf-8"
        match = re.search(r"charset\s*=\s*([a-z0-9_-]+)", page.content_type)
        if match:
            charset = match.group(1)
        try:
            html = page.body.decode(charset, errors="replace")
        except LookupError:
            html = page.body.decode("utf-8", errors="replace")
        return {
            "status": "completed", "status_code": page.status_code,
            "final_url": page.final_url,
            "findings": analyze_html(html, page.final_url, link_limit),
            "error": None,
        }
    except (UnsafeTarget, OSError, ssl.SSLError, http.client.HTTPException, ValueError) as exc:
        return {
            "status": "failed", "status_code": None,
            "final_url": None, "findings": {},
            "error": (str(exc) or type(exc).__name__)[:400],
        }
