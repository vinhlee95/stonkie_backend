"""Playwright helpers for Yahoo Finance statement pages, shared by the crawl task and the export scripts."""

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Yahoo A/B-tests two statement layouts per browser session (cookies). Only the classic one (tableHeader/tableBody
# divs, figures in thousands) can be scraped; the other renders an abbreviated <table>. A session's cookies keep
# its layout, so the cookies of a classic-layout session are cached on disk and reused by every scraper process.
_LAYOUT_STATE_FILE = Path(tempfile.gettempdir()) / "stonkie_yahoo_classic_layout_state.json"
_CLASSIC_LAYOUT_MARKER = "tableHeader"


class NewTableLayoutError(RuntimeError):
    """Yahoo served the unscrapeable table layout; retry with a fresh browser session."""


# Yahoo's "Expand All" button. A bare `span.expand` also matches the "Order:" label next to it.
EXPAND_ALL_BUTTON = "button:has(> span.expand)"


def classic_layout_state() -> str | None:
    """Storage-state file of a session that got the classic layout, for `browser.new_context(storage_state=...)`."""
    return str(_LAYOUT_STATE_FILE) if _LAYOUT_STATE_FILE.exists() else None


def remember_classic_layout(context) -> None:
    """Cache this session's cookies; written via a temp file so concurrent scrapers never read a partial file."""
    fd, tmp = tempfile.mkstemp(dir=_LAYOUT_STATE_FILE.parent, suffix=".json")
    os.close(fd)
    context.storage_state(path=tmp)
    os.replace(tmp, _LAYOUT_STATE_FILE)


def served_classic_table(response) -> bool:
    return response is not None and _CLASSIC_LAYOUT_MARKER in response.text()


def require_classic_table(response) -> None:
    """Fail fast on the new layout (seconds, not the 15-40s of waiting for divs that never come)."""
    if not served_classic_table(response):
        _LAYOUT_STATE_FILE.unlink(missing_ok=True)
        raise NewTableLayoutError("Yahoo served the new table layout")


def table_expanded(expand_button) -> bool:
    """The button reads "Collapse All" once every row is expanded."""
    return "collapse" in expand_button.inner_text(timeout=2000).lower()


def expand_all_rows(page, expand_button, settle_ms: int = 3000) -> bool:
    """Click "Expand All" until the table is expanded. The button toggles, so an expanded table is never clicked."""
    clicks = (
        # JavaScript first: the EU consent popup intercepts pointer clicks.
        ("javascript", lambda: page.evaluate("sel => document.querySelector(sel).click()", EXPAND_ALL_BUTTON)),
        ("force", lambda: expand_button.click(force=True)),
        ("dispatch", lambda: expand_button.dispatch_event("click")),
    )
    for name, click in clicks:
        try:
            if table_expanded(expand_button):
                return True
            click()
            page.wait_for_timeout(settle_ms)
            if table_expanded(expand_button):
                logger.info(f"Expanded all statement rows with a {name} click")
                return True
            logger.warning(f"{name} click did not expand the statement table")
        except Exception as e:
            logger.warning(f"{name} click on Expand All failed: {e}")
    return False
