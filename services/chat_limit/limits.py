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


def _valid_ip(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def client_ip(headers: dict[str, str], peer: str | None) -> str:
    """Caller IP behind Railway's edge: the LAST X-Forwarded-For hop (appended by the edge; earlier hops are
    caller-supplied and spoofable), else the socket peer. Must be a valid IP, which also bounds key length."""
    last_hop = (headers.get("x-forwarded-for") or "").split(",")[-1]
    return _valid_ip(last_hop) or _valid_ip(peer) or "unknown"


def window_key(window: Window, ip: str, now: float) -> str:
    return f"rate:chat:{window.name}:{ip}:{int(now) // window.seconds}"


def over_limit(window: Window, count: int | None) -> bool:
    """A None count (counter store down) fails open."""
    return count is not None and count > window.limit
