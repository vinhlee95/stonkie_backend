"""Playwright helpers for Yahoo Finance statement pages, shared by the crawl task and the export scripts."""

import logging

logger = logging.getLogger(__name__)

# Yahoo's "Expand All" button. A bare `span.expand` also matches the "Order:" label next to it.
EXPAND_ALL_BUTTON = "button:has(> span.expand)"


def table_expanded(expand_button) -> bool:
    """The button reads "Collapse All" once every row is expanded."""
    return "collapse" in expand_button.inner_text().lower()


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
