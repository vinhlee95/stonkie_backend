"""Per-IP fixed-window limits for the public (no sign-in) chat endpoints, which run paid LLM calls."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Window:
    name: str
    seconds: int
    limit: int
    message: str


# Checked in order; the first exceeded window answers.
WINDOWS = (
    Window("minute", 60, 10, "Too many questions. Try again in a minute."),
    Window("day", 86_400, 150, "Daily question limit reached. Try again tomorrow."),
)


class ChatLimitExceeded(Exception):
    def __init__(self, window: Window):
        super().__init__(window.message)
        self.window = window


def client_ip(headers: dict[str, str], peer: str | None) -> str:
    """Caller IP behind Railway's edge: X-Real-IP, else the first X-Forwarded-For hop, else the socket peer."""
    real_ip = (headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    forwarded = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or peer or "unknown"


def window_key(window: Window, ip: str, now: float) -> str:
    return f"rate:chat:{window.name}:{ip}:{int(now) // window.seconds}"


def exceeded(counts: list[int | None]) -> Window | None:
    """First window over its limit. A None count (counter store down) fails open."""
    for window, count in zip(WINDOWS, counts):
        if count is not None and count > window.limit:
            return window
    return None
