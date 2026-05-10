from unittest.mock import MagicMock, patch

from services.market_recap.question_generator import generate_questions


@patch("services.market_recap.question_generator.MultiAgent")
def test_generate_questions_returns_list(mock_agent_cls):
    mock_agent = MagicMock()
    mock_agent_cls.return_value = mock_agent
    mock_agent.generate_content_by_lines.return_value = iter(
        ["Why did tech stocks rally?", "Is inflation slowing?", "What drove oil prices?"]
    )

    result = generate_questions(
        summary="US stocks rose on strong earnings",
        bullets=[{"text": "Tech led gains"}, {"text": "Oil fell 2%"}],
        market="US",
    )

    assert result == ["Why did tech stocks rally?", "Is inflation slowing?", "What drove oil prices?"]
    mock_agent.generate_content_by_lines.assert_called_once()


@patch("services.market_recap.question_generator.MultiAgent")
def test_generate_questions_returns_empty_on_failure(mock_agent_cls):
    mock_agent_cls.side_effect = RuntimeError("api down")

    result = generate_questions(
        summary="summary",
        bullets=[{"text": "b1"}],
        market="US",
    )

    assert result == []


@patch("services.market_recap.question_generator.MultiAgent")
def test_generate_questions_vn_prompt_is_vietnamese(mock_agent_cls):
    mock_agent = MagicMock()
    mock_agent_cls.return_value = mock_agent
    mock_agent.generate_content_by_lines.return_value = iter(["Câu hỏi 1?"])

    generate_questions(
        summary="VN-Index tăng mạnh",
        bullets=[{"text": "Dòng tiền vào nhóm ngân hàng"}],
        market="VN",
    )

    prompt_arg = mock_agent.generate_content_by_lines.call_args[0][0]
    assert "tiếng Việt" in prompt_arg
