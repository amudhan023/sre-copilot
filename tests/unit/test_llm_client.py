"""Unit tests for the LLM client wrapper."""
import pytest
import time
from unittest.mock import MagicMock, patch, call
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


class TestChatFunction:
    def test_returns_text_on_success(self, mock_anthropic):
        content_block = MagicMock()
        content_block.text = "Classified as LATENCY_SPIKE"
        mock_anthropic.messages.create.return_value.content = [content_block]

        with patch("shared.llm_client._client", mock_anthropic):
            from shared.llm_client import chat
            result = chat(system="sys", user="user input")

        assert result == "Classified as LATENCY_SPIKE"

    def test_retries_on_rate_limit(self, mock_anthropic):
        content_block = MagicMock()
        content_block.text = "success after retry"

        # Use the mock RateLimitError from our stub (takes no keyword args)
        import shared.llm_client as llm_mod
        RateLimitError = type("RateLimitError", (Exception,), {})
        real_anthropic = llm_mod.anthropic
        real_anthropic.RateLimitError = RateLimitError

        mock_anthropic.messages.create.side_effect = [
            RateLimitError("rate limited"),
            MagicMock(content=[content_block]),
        ]

        with patch("shared.llm_client._client", mock_anthropic):
            with patch("time.sleep"):
                from shared.llm_client import chat
                result = chat(system="sys", user="user", max_retries=3)

        assert result == "success after retry"

    def test_uses_correct_model(self, mock_anthropic):
        content_block = MagicMock()
        content_block.text = "ok"
        mock_anthropic.messages.create.return_value = MagicMock(content=[content_block])

        with patch("shared.llm_client._client", mock_anthropic):
            from shared.llm_client import chat, HAIKU
            chat(system="sys", user="user", model=HAIKU)

        call_kwargs = mock_anthropic.messages.create.call_args[1]
        assert call_kwargs["model"] == HAIKU

    def test_raises_after_max_retries(self, mock_anthropic):
        import shared.llm_client as llm_mod
        APIError = type("APIError", (Exception,), {})
        llm_mod.anthropic.APIError = APIError

        mock_anthropic.messages.create.side_effect = APIError("API error")

        with patch("shared.llm_client._client", mock_anthropic):
            with patch("time.sleep"):
                from shared.llm_client import chat
                with pytest.raises(APIError):
                    chat(system="sys", user="user", max_retries=2)


class TestRunToolUseAgent:
    def test_end_turn_returns_text(self, mock_anthropic):
        text_block = MagicMock()
        text_block.text = "Final analysis complete"
        mock_anthropic.messages.create.return_value = MagicMock(
            content=[text_block],
            stop_reason="end_turn",
        )

        with patch("shared.llm_client._client", mock_anthropic):
            from shared.llm_client import run_tool_use_agent
            result = run_tool_use_agent(
                system="sys",
                initial_prompt="investigate",
                tools=[],
                tool_executor=lambda n, i: "result",
            )
        assert result == "Final analysis complete"

    def test_tool_use_calls_executor(self, mock_anthropic):
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "get_context"
        tool_block.input = {"query": "test"}
        tool_block.id = "tool_001"

        text_block = MagicMock()
        text_block.text = "Done"
        text_block.type = "text"

        mock_anthropic.messages.create.side_effect = [
            MagicMock(content=[tool_block], stop_reason="tool_use"),
            MagicMock(content=[text_block], stop_reason="end_turn"),
        ]

        executed_tools = []

        def executor(name, inputs):
            executed_tools.append(name)
            return f"result for {name}"

        with patch("shared.llm_client._client", mock_anthropic):
            from shared.llm_client import run_tool_use_agent
            result = run_tool_use_agent(
                system="sys",
                initial_prompt="investigate",
                tools=[{"name": "get_context", "description": "test", "input_schema": {}}],
                tool_executor=executor,
            )

        assert "get_context" in executed_tools

    def test_max_rounds_respected(self, mock_anthropic):
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "loop_tool"
        tool_block.input = {}
        tool_block.id = "loop_001"

        # Always returns tool_use → should stop at max_rounds
        mock_anthropic.messages.create.return_value = MagicMock(
            content=[tool_block], stop_reason="tool_use"
        )

        with patch("shared.llm_client._client", mock_anthropic):
            from shared.llm_client import run_tool_use_agent
            result = run_tool_use_agent(
                system="sys",
                initial_prompt="test",
                tools=[],
                tool_executor=lambda n, i: "result",
                max_rounds=3,
            )
        # Should not raise, should return fallback message
        assert isinstance(result, str)


class TestExtractJsonBlock:
    def test_extracts_json_from_code_block(self):
        text = 'Some text\n```json\n{"key": "value"}\n```\nMore text'
        from shared.llm_client import extract_json_block
        result = extract_json_block(text)
        assert '"key"' in result

    def test_extracts_bare_json_object(self):
        text = 'Here is the result: {"anomaly_type": "CPU_SATURATION", "severity": "HIGH"}'
        from shared.llm_client import extract_json_block
        result = extract_json_block(text)
        assert '"CPU_SATURATION"' in result

    def test_returns_text_if_no_json(self):
        text = "No JSON here at all."
        from shared.llm_client import extract_json_block
        result = extract_json_block(text)
        assert result == text
