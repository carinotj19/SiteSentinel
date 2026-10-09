"""On-demand HTTP/HTTPS scans with public-IP pinning and redirect validation.

Every request resolves the hostname, rejects *all* non-global DNS answers,
and connects to one already validated IP while retaining the hostname for
HTTP Host headers, TLS SNI, and certificate validation. Redirects are checked
anew. No ambient HTTP_PROXY environment variables are honored.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 3
REQUEST_TIMEOUT_SECONDS = 5
USER_AGENT = "SiteSentinel/0.1 (website health monitor)"


class UnsafeTarget(ValueError):
    """A target is malformed, or cannot safely be accessed from a scanner."""


@dataclass(frozen=True)
class ScanResult:
    status: str  # up, down, blocked
    checked_at: datetime
    status_code: int | None = None
    response_time_ms: int | None = None
    certificate_expires_at: datetime | None = None
    error: str | None = None
    final_url: str | None = None


def normalize_url(value: object) -> str:
    """Normalize public HTTP/HTTPS targets without making a network request."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise UnsafeTarget("URL must be a nonempty string no longer than 2048 characters.")
    if any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise UnsafeTarget("URL must not contain whitespace or control characters.")
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            raise UnsafeTarget("Only absolute http:// and https:// URLs are supported.")
        if parts.username is not None or parts.password is not None:
            raise UnsafeTarget("URLs containing credentials are not allowed.")
        host = parts.hostname
        port = parts.port  # Forces invalid-port checks.
        if not host or "%" in host:
            raise UnsafeTarget("Invalid hostname.")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            # DNS labels must be unambiguous; no single-label internal names.
            host = host.encode("idna").decode("ascii").lower()
            if "." not in host or host.endswith((".localhost", ".local", ".internal", ".lan", ".home")):
                raise UnsafeTarget("Local and internal hostnames are not allowed.")
        else:
            if not ip.is_global:
                raise UnsafeTarget("Only public IP addresses are allowed.")
            host = ip.compressed

        scheme = parts.scheme.lower()
        allowed_port = 443 if scheme == "https" else 80
        if port is not None and port != allowed_port:
            raise UnsafeTarget("Only standard HTTP (80) and HTTPS (443) ports are supported.")
        netloc = f"[{host}]" if ":" in host else host
        # Drop an explicitly specified default port for consistent uniqueness.
        normalized = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
        if len(normalized) > MAX_URL_LENGTH:
            raise UnsafeTarget("Normalized URL is too long.")
        return normalized
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, UnsafeTarget):
            raise
        raise UnsafeTarget("Malformed URL or port.") from exc


def resolve_public_ip(hostname: str, port: int) -> str:
    """Reject the complete DNS result when *any* answer is not globally routable."""
    try:
        records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OSError("DNS lookup failed.") from exc
    if not records:
        raise OSError("DNS returned no addresses.")
    addresses = [ipaddress.ip_address(entry[4][0]) for entry in records]
    if any(not address.is_global for address in addresses):
        raise UnsafeTarget("DNS returned a private, reserved, or non-public IP address.")
    return str(addresses[0])


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, port: int, ip: str, **kwargs):
        self._pinned_ip = ip
        super().__init__(hostname, port=port, **kwargs)

    def _create_connection(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        return socket.create_connection((self._pinned_ip, address[1]), timeout, source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, port: int, ip: str, **kwargs):
        self._pinned_ip = ip
        super().__init__(hostname, port=port, **kwargs)

    def _create_connection(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        return socket.create_connection((self._pinned_ip, address[1]), timeout, source_address)


def _request_once(url: str) -> tuple[int, str | None, datetime | None]:
    """Perform exactly one pinned GET and return status, Location, cert expiry."""
    parts = urlsplit(url)
    hostname = parts.hostname
    assert hostname is not None  # normalize_url() established this.
    is_https = parts.scheme == "https"
    port = 443 if is_https else 80
    ip = resolve_public_ip(hostname, port)
    if is_https:
        connection = _PinnedHTTPSConnection(
            hostname, port, ip, timeout=REQUEST_TIMEOUT_SECONDS, context=ssl.create_default_context()
        )
    else:
        connection = _PinnedHTTPConnection(hostname, port, ip, timeout=REQUEST_TIMEOUT_SECONDS)

    try:
        # Explicit connect lets us inspect the certificate before any HTTP request.
        connection.connect()
        expiry = None
        if is_https:
            cert = connection.sock.getpeercert()
            if cert and cert.get("notAfter"):
                expiry = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=timezone.utc)
        request_target = parts.path or "/"
        if parts.query:
            request_target += "?" + parts.query
        connection.request(
            "GET", request_target, headers={"User-Agent": USER_AGENT, "Connection": "close"}
        )
        response = connection.getresponse()
        return response.status, response.getheader("Location"), expiry
    finally:
        connection.close()


def scan_url(value: object) -> ScanResult:
    """Scan a public URL; never raise network errors into an API handler."""
    started = time.monotonic()
    certificate_expiry = None
    current_url = None
    status_code = None
    try:
        current_url = normalize_url(value)
        for redirect_number in range(MAX_REDIRECTS + 1):
            status_code, location, expiry = _request_once(current_url)
            if expiry is not None:
                certificate_expiry = expiry
            if status_code in (301, 302, 303, 307, 308):
                if not location:
                    return _outcome("down", started, status_code, certificate_expiry, "Redirect lacks Location.", current_url)
                if redirect_number >= MAX_REDIRECTS:
                    return _outcome("down", started, status_code, certificate_expiry, "Too many redirects.", current_url)
                # Re-normalize; next hop performs its own DNS safety check.
                current_url = normalize_url(urljoin(current_url, location))
                continue
            status = "up" if 200 <= status_code < 400 else "down"
            error = None if status == "up" else f"HTTP {status_code}"
            return _outcome(status, started, status_code, certificate_expiry, error, current_url)
    except UnsafeTarget as exc:
        return _outcome("blocked", started, status_code, certificate_expiry, str(exc), current_url)
    except (OSError, ssl.SSLError, http.client.HTTPException, ValueError) as exc:
        # Errors may contain data from a remote server; cap persisted detail.
        reason = str(exc)[:400] or exc.__class__.__name__
        return _outcome("down", started, status_code, certificate_expiry, reason, current_url)
    # Unreachable, but keep an explicit fail-closed fallback.
    return _outcome("down", started, status_code, certificate_expiry, "Unexpected scan termination.", current_url)


def _outcome(
    status: str,
    started: float,
    code: int | None,
    expiry: datetime | None,
    error: str | None,
    final_url: str | None,
) -> ScanResult:
    return ScanResult(
        status=status,
        checked_at=datetime.now(timezone.utc),
        status_code=code,
        response_time_ms=max(0, round((time.monotonic() - started) * 1000)),
        certificate_expires_at=expiry,
        error=error,
        final_url=final_url,
    )
