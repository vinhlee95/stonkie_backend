import json
from pathlib import Path

import pytest

import connectors.yahoo_financials_page as yahoo_page
from connectors.yahoo_financials_page import EXPAND_ALL_BUTTON, expand_all_rows, table_expanded


class FakeButton:
    """Yahoo's Expand All toggle; `works` lists which click kinds actually toggle it."""

    def __init__(self, works=("js", "force", "dispatch"), label="Expand All"):
        self.works = works
        self.label = label
        self.clicks = []

    def toggle(self, kind):
        self.clicks.append(kind)
        if kind in self.works:
            self.label = "Collapse All" if self.label == "Expand All" else "Expand All"

    def inner_text(self, timeout=None):
        return self.label

    def click(self, force=False):
        self.toggle("force")

    def dispatch_event(self, event):
        self.toggle("dispatch")


class FakePage:
    def __init__(self, button):
        self.button = button

    def evaluate(self, script, selector):
        assert selector == EXPAND_ALL_BUTTON
        self.button.toggle("js")

    def wait_for_timeout(self, ms):
        pass


def test_table_expanded_reads_the_button_label():
    assert table_expanded(FakeButton(label="Collapse All")) is True
    assert table_expanded(FakeButton(label="Expand All")) is False


def test_js_click_is_tried_first():
    button = FakeButton()

    assert expand_all_rows(FakePage(button), button) is True
    assert button.clicks == ["js"]


def test_falls_through_to_the_next_click_kind_when_the_table_stays_collapsed():
    # The EU consent popup swallows pointer clicks; only dispatch_event gets through here.
    button = FakeButton(works=("dispatch",))

    assert expand_all_rows(FakePage(button), button) is True
    assert button.clicks == ["js", "force", "dispatch"]


def test_never_clicks_an_already_expanded_table():
    button = FakeButton(label="Collapse All")

    assert expand_all_rows(FakePage(button), button) is True
    assert button.clicks == []


def test_slow_label_update_is_not_toggled_back():
    # The JS click worked, but the label only flips after the first check.
    class SlowButton(FakeButton):
        checks = 0

        def inner_text(self, timeout=None):
            SlowButton.checks += 1
            return "Expand All" if SlowButton.checks <= 2 else self.label

    button = SlowButton()

    assert expand_all_rows(FakePage(button), button) is True
    assert button.clicks == ["js"]


def test_returns_false_when_no_click_expands_the_table():
    button = FakeButton(works=())

    assert expand_all_rows(FakePage(button), button) is False
    assert button.clicks == ["js", "force", "dispatch"]


class FakeContext:
    def __init__(self, state):
        self.state = state

    def storage_state(self, path=None):
        Path(path).write_text(json.dumps(self.state))


class FakeResponse:
    def __init__(self, html):
        self.html = html

    def text(self):
        return self.html


@pytest.fixture()
def layout_cache(tmp_path, monkeypatch):
    path = tmp_path / "layout.json"
    monkeypatch.setattr(yahoo_page, "_LAYOUT_STATE_FILE", path)
    return path


def test_classic_layout_is_detected_from_the_server_html():
    assert yahoo_page.served_classic_table(FakeResponse('<div class="tableHeader yf-1">')) is True
    assert yahoo_page.served_classic_table(FakeResponse('<div class="table-container yf-2"><table>')) is False
    assert yahoo_page.served_classic_table(None) is False


def test_cookies_of_a_classic_layout_context_are_reused(layout_cache):
    assert yahoo_page.classic_layout_state() is None

    yahoo_page.remember_classic_layout(FakeContext({"cookies": [{"name": "A1"}], "origins": []}))

    assert yahoo_page.classic_layout_state() == str(layout_cache)
    assert json.loads(layout_cache.read_text())["cookies"] == [{"name": "A1"}]


def test_new_layout_drops_the_cached_cookies(layout_cache):
    yahoo_page.remember_classic_layout(FakeContext({"cookies": [], "origins": []}))

    with pytest.raises(yahoo_page.NewTableLayoutError):
        yahoo_page.require_classic_table(FakeResponse("<table>"))

    assert yahoo_page.classic_layout_state() is None


def test_classic_layout_passes_the_check(layout_cache):
    yahoo_page.require_classic_table(FakeResponse('<div class="tableHeader yf-1">'))
