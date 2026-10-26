from utils.yahoo_financials_page import EXPAND_ALL_BUTTON, expand_all_rows, table_expanded


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

    def inner_text(self):
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

        def inner_text(self):
            SlowButton.checks += 1
            return "Expand All" if SlowButton.checks <= 2 else self.label

    button = SlowButton()

    assert expand_all_rows(FakePage(button), button) is True
    assert button.clicks == ["js"]


def test_returns_false_when_no_click_expands_the_table():
    button = FakeButton(works=())

    assert expand_all_rows(FakePage(button), button) is False
    assert button.clicks == ["js", "force", "dispatch"]
