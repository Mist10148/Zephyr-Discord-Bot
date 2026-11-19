"""Host checks shared by the bot and the website.

Kept free of discord and yt-dlp imports so the website can use the same rule
the bot enforces when a playlist link is saved.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit

# One part of a numeric host as inet_aton reads it: decimal, octal or hex.  No
# sign -- int() accepts "-1", and IPv4Address(-1) then raised out of the check.
_NUMERIC_PART = re.compile(r"(?:0x[0-9a-f]*|[0-9]+)", re.IGNORECASE)

# Indirected so tests can answer DNS without touching the shared socket module.
_getaddrinfo = socket.getaddrinfo


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
        if not part or not _NUMERIC_PART.fullmatch(part):
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
            # A name with no dot is never on the public internet: it is a
            # Compose service (web, postgres, redis), a hosts-file entry
            # (ip6-localhost) or a search-domain shortcut -- all of them inside.
            return "." not in host
    return _is_private(address)


def _is_private(address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not address.is_global


def resolves_internal(host: str) -> bool:
    """Whether a public-looking name resolves to a private address.

    ``127.0.0.1.nip.io`` passes ``is_internal_host`` but connects to localhost.
    Blocking (a DNS lookup), so call it off the event loop.  A name that does
    not resolve is not called internal: the fetch will fail on its own.
    """
    host = (host or "").strip().strip("[]").rstrip(".").lower()
    if not host:
        return False
    if is_internal_host(host):
        return True
    try:
        infos = _getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            continue
        if _is_private(address):
            return True
    return False


def url_resolves_internal(url: str) -> bool:
    """``resolves_internal`` for a URL's host; an unparsable URL counts as internal."""
    try:
        host = urlsplit((url or "").strip()).hostname or ""
    except ValueError:
        return True
    return resolves_internal(host)


def is_internal_url(url: str) -> bool:
    """Whether an http(s) URL points at this host or a private network."""
    try:
        host = urlsplit(url.strip()).hostname or ""
    except ValueError:
        return True
    return is_internal_host(host)
