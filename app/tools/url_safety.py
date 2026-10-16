"""SSRF protection: scheme/port/host checks + DNS resolution against private ranges."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.errors import ProviderError
from app.models.common import ErrorCategory

_BLOCKED_NETS = [ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("169.254.0.0/16")]
_TRACKING = ("utm_", "gclid", "fbclid", "mc_cid", "mc_eid")


@dataclass
class ParsedURL:
    url: str
    scheme: str
    host: str
    port: int
    ips: list[str]


def canonicalize(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    netloc = host
    if parts.port and not ((scheme == "http" and parts.port == 80) or (scheme == "https" and parts.port == 443)):
        netloc = f"{host}:{parts.port}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not k.lower().startswith(_TRACKING)])
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, query, ""))


def _ip_blocked(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _ip_blocked(ip.ipv4_mapped)
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified):
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local:
        return True
    return any(ip in net for net in _BLOCKED_NETS if ip.version == net.version)


def check_static(url: str) -> tuple[str, str, int]:
    if len(url) > 2048:
        raise ProviderError(ErrorCategory.blocked_url, "url too long")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ProviderError(ErrorCategory.blocked_url, "scheme")
    if parts.username or parts.password:
        raise ProviderError(ErrorCategory.blocked_url, "userinfo")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise ProviderError(ErrorCategory.blocked_url, "no host")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as e:
        raise ProviderError(ErrorCategory.blocked_url, "bad port") from e
    if port not in (80, 443):
        raise ProviderError(ErrorCategory.blocked_url, "port")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ProviderError(ErrorCategory.blocked_url, "local host")
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
        if _ip_blocked(ip):
            raise ProviderError(ErrorCategory.blocked_url, "private ip")
    except ValueError:
        pass
    return scheme, host, port


async def validate_public_url(url: str) -> ParsedURL:
    scheme, host, port = check_static(url)
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=5)
    except (OSError, TimeoutError) as e:
        raise ProviderError(ErrorCategory.fetch_http_error, "dns failure") from e
    ips: list[str] = []
    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr.split("%")[0])
        if _ip_blocked(ip):
            raise ProviderError(ErrorCategory.blocked_url, "resolves to private ip")
        ips.append(str(ip))
    if not ips:
        raise ProviderError(ErrorCategory.fetch_http_error, "no addresses")
    return ParsedURL(url=url, scheme=scheme, host=host, port=port, ips=ips)
