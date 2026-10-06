import json

import pytest

from utils.json_extract import extract_json_object


@pytest.mark.parametrize(
    "text",
    ['{"route": "x"}', '```json\n{"route": "x"}\n```', 'Sure! {"route": "x"} hope that helps'],
)
def test_finds_the_object(text):
    assert extract_json_object(text) == {"route": "x"}


@pytest.mark.parametrize("text", ["no braces here", '["route"]'])
def test_no_object_raises(text):
    with pytest.raises(ValueError):
        extract_json_object(text)


def test_invalid_json_raises_value_error():
    with pytest.raises(json.JSONDecodeError):
        extract_json_object("{route: x}")
