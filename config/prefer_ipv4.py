"""
Prefer IPv4 when resolving hostnames (Python equivalent of Node's
--dns-result-order=ipv4first).

Gemini often returns "User location is not supported for the API use"
when the client connects via a mis-geotagged or broken IPv6 path on a VPS.
Preferring IPv4 matches the n8n fix that uses NODE_OPTIONS=--dns-result-order=ipv4first.
"""

from __future__ import annotations

import socket

_applied = False
_original_getaddrinfo = socket.getaddrinfo


def prefer_ipv4_dns(*, ipv4_only: bool = False) -> None:
    """Monkey-patch socket.getaddrinfo so AF_INET results come first (or only)."""
    global _applied
    if _applied:
        return

    def getaddrinfo_ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
        if ipv4_only and family == 0:
            return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
        infos = _original_getaddrinfo(host, port, family, type, proto, flags)
        if family == 0 and len(infos) > 1:
            infos = sorted(infos, key=lambda info: 0 if info[0] == socket.AF_INET else 1)
        return infos

    socket.getaddrinfo = getaddrinfo_ipv4_first
    _applied = True
