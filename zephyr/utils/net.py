"""Host checks shared by the bot and the website.

Kept free of discord and yt-dlp imports so the website can use the same rule
the bot enforces when a playlist link is saved.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit


def _legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse the forms ``inet_aton`` accepts but ``ipaddress`` rejects.

    ``127.1``, ``2130706433``, ``0x7f000001`` and ``0177.0.0.1`` all reach
    127.0.0.1 through ``getaddrinfo``, which is what FFmpeg and yt-dlp use, so
    a check that only understands dotted quads lets them through.  Parsed by
    hand rather than with ``socket.inet_aton`` because that differs by OS.
    """
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    numbers = []
    for part in parts:
        if not part:
            return None
        try:
            if part.lower().startswith("0x"):
                value = int(part[2:] or "0", 16)
            elif len(part) > 1 and part.startswith("0"):
                value = int(part[1:], 8)
            else:
                value = int(part, 10)
        except ValueError:
            return None
        numbers.append(value)
    # a, a.b (b: 24 bits), a.b.c (c: 16 bits), a.b.c.d -- the BSD rules.
    *head, last = numbers
    if any(n > 255 for n in head) or last >= 256 ** (4 - len(head)):
        return None
    value = 0
    for n in head:
        value = value * 256 + n
    value = value * 256 ** (4 - len(head)) + last
    return ipaddress.IPv4Address(value)


def is_internal_host(host: str) -> bool:
    """Whether a host name or literal address is this machine or a private network."""
    host = (host or "").strip().strip("[]").rstrip(".").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = _legacy_ipv4(host)
        if address is None:
            return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not address.is_global


def is_internal_url(url: str) -> bool:
    """Whether an http(s) URL points at this host or a private network."""
    try:
        host = urlsplit(url.strip()).hostname or ""
    except ValueError:
        return True
    return is_internal_host(host)
