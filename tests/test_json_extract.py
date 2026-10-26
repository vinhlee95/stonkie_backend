import json

import pytest

from utils.json_extract import extract_json_array, extract_json_object


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


@pytest.mark.parametrize(
    "text",
    ['[{"period_end_year": 2025}]', '```json\n[{"period_end_year": 2025}]\n```', 'Here: [{"period_end_year": 2025}]'],
)
def test_finds_the_array(text):
    assert extract_json_array(text) == [{"period_end_year": 2025}]


@pytest.mark.parametrize("text", ["no brackets", "[1, 2"])
def test_no_array_raises(text):
    with pytest.raises(ValueError):
        extract_json_array(text)
