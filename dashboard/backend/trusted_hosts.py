"""Host names this unauthenticated, loopback-oriented API answers for.

A page on any website can point its own DNS name at 127.0.0.1 and then read
this API from the visitor's browser as if it were the same site (DNS
rebinding). Such a request still names the foreign site in its Host header, so
refusing unknown names closes that path. This is not authentication; see
SECURITY.md before allowing remote access.
"""
import ipaddress
import os
import re

from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse


# Loopback names, and the Compose service name other containers use.
DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "[::1]", "backend")

_NAME = re.compile(r"(\*\.)?[a-z0-9]([a-z0-9._-]*[a-z0-9])?")
_IPV6 = re.compile(r"\[([0-9a-f:.]+)\]")


def _bracketed(text):
    """The canonical ``[address]`` of a well-formed bracketed IPv6 literal, else None."""
    match = _IPV6.fullmatch(text)
    if match is None:
        return None
    try:
        return "[" + str(ipaddress.IPv6Address(match.group(1))) + "]"
    except ValueError:
        return None


def normalize_host(header):
    """The comparable name in a Host header, or None when it is malformed.

    Case and one trailing dot do not matter, the port is ignored, and an IPv6
    literal must be bracketed and a valid address.
    """
    host = header.strip().lower()
    if host.startswith("["):
        name, closed, port = host.partition("]")
        if not closed or not re.fullmatch(r"(:\d+)?", port):
            return None
        return _bracketed(name + "]")
    name, _, port = host.partition(":")
    if port and not port.isdigit():
        return None
    return name[:-1] if name.endswith(".") else name


def allowed_hosts(environ=None):
    """The defaults plus comma-separated ``TRACKER_ALLOWED_HOSTS``.

    Entries are host names, IP addresses, bracketed IPv6 addresses or
    ``*.example.com`` patterns; a port is ignored. ``*`` disables the check.
    """
    value = (os.environ if environ is None else environ).get("TRACKER_ALLOWED_HOSTS", "")
    hosts = list(DEFAULT_ALLOWED_HOSTS)
    for entry in value.split(","):
        entry = entry.strip().lower()
        if not entry:
            continue
        if entry == "*":
            return ["*"]
        name = re.sub(r":\d+$", "", entry)
        if name.startswith("["):
            name = _bracketed(name)
        elif not _NAME.fullmatch(name):
            name = None
        if name is None:
            raise ValueError("TRACKER_ALLOWED_HOSTS entries must be host names without a scheme or path, "
                             "'*.example.com' patterns, or '*' alone")
        if name not in hosts:
            hosts.append(name)
    return hosts


class HostAllowList(TrustedHostMiddleware):
    """Starlette's host check, with one reading of Host on every version.

    Names compare case-insensitively with one trailing dot ignored, and a
    bracketed IPv6 host must be a complete, valid address.
    """

    def __init__(self, app, allowed_hosts=None):
        super().__init__(app, allowed_hosts=allowed_hosts, www_redirect=False)

    def permitted(self, header):
        host = normalize_host(header)
        if not host:
            return False
        for pattern in self.allowed_hosts:
            if host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:])):
                return True
        return False

    async def __call__(self, scope, receive, send):
        if self.allow_any or scope["type"] not in ("http", "websocket") \
                or self.permitted(Headers(scope=scope).get("host", "")):
            await self.app(scope, receive, send)
        else:
            await PlainTextResponse("Invalid host header", status_code=400)(scope, receive, send)
