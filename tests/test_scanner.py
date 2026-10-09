"""Scanner safety and HTTP/TLS result logic; no external network calls."""

import socket
from datetime import datetime, timezone

import pytest

from sitesentinel.monitoring import scanner


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com", "http://localhost", "http://localhost:80",
    "http://127.0.0.1", "http://10.2.3.4", "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/", "http://example.com:8080", "http://user:pass@example.com",
    "http://local.internal", "http://example.com/hello\r\nInjected: yes",
    "http://example.com:wat", "http://a", "", None,
])
def test_normalize_rejects_unsafe_targets(url):
    with pytest.raises(scanner.UnsafeTarget):
        scanner.normalize_url(url)


def test_url_normalization_preserves_paths_and_drops_fragment():
    assert scanner.normalize_url("HTTPS://Example.COM:443/path?x=1#ignore") == "https://example.com/path?x=1"
    assert scanner.normalize_url("http://example.com") == "http://example.com/"


def test_dns_rejects_mixed_public_and_private_answers(monkeypatch):
    monkeypatch.setattr(scanner.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    with pytest.raises(scanner.UnsafeTarget, match="DNS"):
        scanner.resolve_public_ip("example.com", 443)


def test_dns_chooses_public_ip(monkeypatch):
    monkeypatch.setattr(scanner.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
    ])
    assert scanner.resolve_public_ip("example.com", 443) == "1.1.1.1"


def test_dns_lookup_error_is_result_not_exception(monkeypatch):
    monkeypatch.setattr(scanner, "_request_once", lambda _url: (_ for _ in ()).throw(OSError("DNS failed")))
    result = scanner.scan_url("https://example.com/")
    assert result.status == "down"
    assert "DNS failed" in result.error
    assert result.response_time_ms >= 0


def test_http_status_results_and_ssl_certificate(monkeypatch):
    expiry = datetime(2027, 5, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(scanner, "_request_once", lambda _url: (200, None, expiry))
    result = scanner.scan_url("https://example.com")
    assert result.status == "up"
    assert result.status_code == 200
    assert result.certificate_expires_at == expiry
    assert result.error is None
    monkeypatch.setattr(scanner, "_request_once", lambda _url: (503, None, None))
    result = scanner.scan_url("https://example.com")
    assert result.status == "down" and result.error == "HTTP 503"


def test_redirect_to_private_literal_is_blocked(monkeypatch):
    monkeypatch.setattr(scanner, "_request_once", lambda _url: (302, "http://127.0.0.1/admin", None))
    result = scanner.scan_url("https://example.com")
    assert result.status == "blocked"
    assert result.status_code == 302


def test_redirects_revalidate_dns_at_every_hop(monkeypatch):
    seen = []
    def fake_once(url):
        seen.append(url)
        if url == "https://example.com/":
            return 301, "https://other.example.org/", None
        raise scanner.UnsafeTarget("DNS returned a private IP address.")
    monkeypatch.setattr(scanner, "_request_once", fake_once)
    result = scanner.scan_url("https://example.com")
    assert seen == ["https://example.com/", "https://other.example.org/"]
    assert result.status == "blocked"


def test_redirect_loop_is_bounded(monkeypatch):
    monkeypatch.setattr(scanner, "_request_once", lambda _url: (302, "/again", None))
    result = scanner.scan_url("https://example.com")
    assert result.status == "down" and result.error == "Too many redirects."


def test_tls_certificate_failure_returns_down(monkeypatch):
    import ssl
    monkeypatch.setattr(scanner, "_request_once", lambda _url: (_ for _ in ()).throw(ssl.SSLError("bad cert")))
    assert scanner.scan_url("https://example.com").status == "down"
