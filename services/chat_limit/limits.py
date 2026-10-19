"""Per-IP fixed-window limits for the public (no sign-in) chat endpoints, which run paid LLM calls."""

import ipaddress
from dataclasses import dataclass


@dataclass(frozen=True)
class Window:
    name: str
    seconds: int
    limit: int
    message: str


# Checked in order; the first exceeded window answers and later windows are not counted.
WINDOWS = (
    Window("minute", 60, 10, "Too many questions. Try again in a minute."),
    Window("day", 86_400, 150, "Daily question limit reached. Try again tomorrow."),
)


class ChatLimitExceeded(Exception):
    def __init__(self, window: Window):
        super().__init__(window.message)
        self.message = window.message
        self.retry_after = window.seconds


def _limit_subject(value: str | None) -> str | None:
    """Normalised IPv4 address, or the /64 of an IPv6 one (a single client controls a whole /64). None if not an IP."""
    try:
        addr = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None
    if addr.version == 6:
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def client_ip(headers: dict[str, str], peer: str | None) -> str:
    """Caller IP behind Railway's edge: the LAST X-Forwarded-For hop (appended by the edge; earlier hops are
    caller-supplied and spoofable), else the socket peer. Must be a valid IP, which also bounds key length."""
    last_hop = (headers.get("x-forwarded-for") or "").split(",")[-1]
    return _limit_subject(last_hop) or _limit_subject(peer) or "unknown"


def window_key(window: Window, ip: str, now: float) -> str:
    return f"rate:chat:{window.name}:{ip}:{int(now) // window.seconds}"


def over_limit(window: Window, count: int | None) -> bool:
    """A None count (counter store down) fails open."""
    return count is not None and count > window.limit
