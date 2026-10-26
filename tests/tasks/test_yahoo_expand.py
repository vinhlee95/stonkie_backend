import pytest

from tasks.financial_crawler import EXPAND_ALL_BUTTON, _table_expanded


class FakeButton:
    def __init__(self, text: str):
        self.text = text

    def inner_text(self) -> str:
        return self.text


@pytest.mark.parametrize(("label", "expanded"), [("Collapse All", True), ("Expand All", False), ("", False)])
def test_table_expanded_reads_the_button_label(label, expanded):
    assert _table_expanded(FakeButton(label)) is expanded


def test_expand_selector_targets_the_button_not_the_order_label():
    # Yahoo also renders <div class="order"><span class="expand">Order:</span>…</div>.
    assert EXPAND_ALL_BUTTON == "button:has(> span.expand)"
