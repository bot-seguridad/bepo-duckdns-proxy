"""Linux-compatible outbound VPN/proxy adapter for Órbita Media.

The uploaded APK contains Android ARM64 code and no Linux binaries or usable
remote VPN credentials. This module supports an independently provisioned
outbound SOCKS5 or HTTP(S) proxy on Linux/Render. It never changes the IP
unless ORBITA_OUTBOUND_PROXY points to an actual functioning remote proxy.
"""
import os
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit, unquote


class ProxySetupError(ValueError):
    """Proxy configuration error with no credentials in its message."""


@dataclass(frozen=True)
class ProxyEndpoint:
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    @property
    def server(self):
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def requests_url(self):
        # socks5h forces remote DNS resolution, avoiding host-local DNS lookups
        scheme = "socks5h" if self.scheme in ("socks5", "socks5h") else self.scheme
        auth = ""
        if self.username:
            from urllib.parse import quote
            auth = quote(self.username, safe="") + ":" + quote(self.password, safe="") + "@"
        return f"{scheme}://{auth}{self.host}:{self.port}"


def outbound_endpoint():
    raw = os.environ.get("ORBITA_OUTBOUND_PROXY", "").strip()
    if not raw:
        return None
    try:
        p = urlsplit(raw)
        if p.scheme not in ("socks5", "socks5h", "http", "https"):
            raise ProxySetupError("El proxy debe usar SOCKS5, HTTP o HTTPS.")
        if not p.hostname or not p.port or p.port <= 0 or p.port > 65535:
            raise ProxySetupError("Falta el host o puerto del proxy.")
        if p.path not in ("", "/") or p.query or p.fragment:
            raise ProxySetupError("La URL del proxy tiene parámetros no admitidos.")
        hostname = p.hostname
        if hostname.lower() == "localhost" or hostname in ("0.0.0.0", "::"):
            raise ProxySetupError("El proxy no puede apuntar a una dirección de escucha genérica.")
        if ":" in hostname:  # IPv6 in URL
            hostname = "[" + hostname + "]"
        return ProxyEndpoint(
            scheme=p.scheme, host=hostname, port=p.port,
            username=unquote(p.username or ""), password=unquote(p.password or "")
        )
    except ValueError as exc:
        if isinstance(exc, ProxySetupError):
            raise
        raise ProxySetupError("La variable ORBITA_OUTBOUND_PROXY no contiene una URL válida.") from None


def proxy_is_configured():
    return bool(os.environ.get("ORBITA_OUTBOUND_PROXY", "").strip())


def make_session():
    import requests
    session = requests.Session()
    endpoint = outbound_endpoint()
    if endpoint:
        url = endpoint.requests_url
        session.trust_env = False
        session.proxies.update({"http": url, "https": url})
    return session


def browser_proxy():
    endpoint = outbound_endpoint()
    if not endpoint:
        return None
    config = {"server": endpoint.server}
    if endpoint.username:
        config["username"] = endpoint.username
        config["password"] = endpoint.password
    return config


def ffmpeg_network_proxy():
    endpoint = outbound_endpoint()
    if not endpoint:
        return None
    # FFmpeg's HTTP proxy feature is not a reliable SOCKS5 client.
    # Fail closed for playlist manifests if using SOCKS5; don't connect directly.
    if endpoint.scheme not in ("http", "https"):
        raise ProxySetupError(
            "La reproducción HLS/DASH por SOCKS5 no está implementada; no se hará una conexión directa."
        )
    return endpoint.requests_url


def public_proxy_status():
    # Never expose username, password, hostname or IP in status.
    try:
        endpoint = outbound_endpoint()
    except ProxySetupError:
        return "invalid"
    return "configured" if endpoint else "unconfigured"
