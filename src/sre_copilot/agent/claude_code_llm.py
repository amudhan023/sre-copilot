"""Claude Code backend for the existing Gemini-compatible LLM boundary."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator, Callable

from google.genai import types

logger = logging.getLogger(__name__)

try:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolUseBlock, query
except ImportError:  # pragma: no cover
    AssistantMessage = None
    ClaudeAgentOptions = None
    ResultMessage = None
    TextBlock = None
    ToolUseBlock = None
    query = None


class ClaudeCodeUnavailableError(RuntimeError):
    """Claude Agent SDK is unavailable in the current environment."""


class ClaudeCodeRequestError(RuntimeError):
    """Claude Code could not satisfy a request."""


class ClaudeCodeTimeoutError(TimeoutError):
    """Claude Code exceeded the configured request timeout."""


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ClaudeCodeRequestError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ClaudeCodeRequestError(f"{name} must be greater than zero")
    return value


def _messages_to_prompt(contents: list[Any]) -> str:
    """Serialize Gemini-shaped history while preserving tool requests/results."""
    lines: list[str] = []
    for content in contents:
        role = content.get("role") if isinstance(content, dict) else getattr(content, "role", None)
        parts = content.get("parts") if isinstance(content, dict) else getattr(content, "parts", None)
        if role not in {"user", "model"} or not isinstance(parts, list):
            raise ClaudeCodeRequestError("Malformed Gemini conversation contents")
        role_name = "Assistant" if role == "model" else "User"
        text_parts: list[str] = []
        for part in parts:
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            function_call = part.get("function_call") if isinstance(part, dict) else getattr(part, "function_call", None)
            if text:
                text_parts.append(str(text))
            if function_call:
                name = function_call.get("name") if isinstance(function_call, dict) else getattr(function_call, "name", None)
                args = function_call.get("args", {}) if isinstance(function_call, dict) else getattr(function_call, "args", {})
                if not name:
                    raise ClaudeCodeRequestError("Malformed Gemini function call")
                text_parts.append(f"[Previous assistant tool call: {name}({json.dumps(args, sort_keys=True)})]")
        if text_parts:
            lines.append(f"{role_name}:\n{'\n'.join(text_parts)}")
    if not lines:
        raise ClaudeCodeRequestError("Claude Code request contains no message content")
    return "\n\n".join(lines)


def _options() -> Any:
    if ClaudeAgentOptions is None:
        raise ClaudeCodeUnavailableError("claude-agent-sdk is not installed; install the project dependency before using LLM_PROVIDER=claude_code")
    return ClaudeAgentOptions(
        model=os.getenv("CLAUDE_CODE_MODEL") or None,
        max_turns=_int_env("CLAUDE_CODE_MAX_TURNS", 1),
        max_output_tokens=_int_env("CLAUDE_CODE_MAX_OUTPUT_TOKENS", 8192),
        permission_mode=os.getenv("CLAUDE_CODE_PERMISSION_MODE", "default"),
        allowed_tools=[],
        disallowed_tools=["Bash", "Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "WebFetch", "WebSearch", "Task"],
        cwd=os.getenv("CLAUDE_CODE_WORKING_DIRECTORY") or None,
    )


def _block_field(block: Any, field: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(field, default)
    return getattr(block, field, default)


def _result_from_messages(messages: list[Any]) -> types.GenerateContentResponse:
    """Convert Claude's final assistant message into the existing response type.

    ToolUseBlock is deliberately converted to Gemini FunctionCall so the existing
    LangGraph tool-routing code can execute the MCP tool and append its result.
    """
    parts: list[types.Part] = []
    for message in messages:
        if AssistantMessage is None or not isinstance(message, AssistantMessage):
            continue
        for block in message.content:
            if TextBlock is not None and isinstance(block, TextBlock):
                if block.text:
                    parts.append(types.Part(text=block.text))
            elif ToolUseBlock is not None and isinstance(block, ToolUseBlock):
                name = _block_field(block, "name")
                args = _block_field(block, "input", {}) or {}
                call_id = _block_field(block, "id")
                if not name:
                    raise ClaudeCodeRequestError("Claude Code returned a malformed tool request")
                parts.append(types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=args)))
    if not parts:
        raise ClaudeCodeRequestError("Claude Code returned no usable response")
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))])


async def _consume_query(prompt: str, options: Any, client: Callable[..., AsyncIterator[Any]]) -> types.GenerateContentResponse:
    messages: list[Any] = []
    try:
        async for message in client(prompt=prompt, options=options):
            messages.append(message)
            if ResultMessage is not None and isinstance(message, ResultMessage):
                if getattr(message, "is_error", False):
                    raise ClaudeCodeRequestError("Claude Code request failed")
    except asyncio.CancelledError:
        raise
    except ClaudeCodeRequestError:
        raise
    except Exception as exc:
        lowered = str(exc).lower()
        if "auth" in lowered or "credential" in lowered or "login" in lowered:
            raise ClaudeCodeRequestError("Claude Code authentication failed") from exc
        raise ClaudeCodeRequestError("Claude Code request failed") from exc
    return _result_from_messages(messages)


async def invoke(
    contents: list[Any],
    *,
    timeout_seconds: int | None = None,
    client: Callable[..., AsyncIterator[Any]] | None = None,
) -> types.GenerateContentResponse:
    """Invoke Claude Code and return a Gemini-compatible text/tool-call response."""
    if query is None and client is None:
        raise ClaudeCodeUnavailableError("claude-agent-sdk is not installed; install the project dependency before using LLM_PROVIDER=claude_code")
    prompt = _messages_to_prompt(contents)
    options = _options()
    timeout = timeout_seconds or _int_env("CLAUDE_CODE_TIMEOUT_SECONDS", 180)
    query_client = client or query
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        response = await asyncio.wait_for(_consume_query(prompt, options, query_client), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise ClaudeCodeTimeoutError(f"Claude Code timed out after {timeout} seconds") from exc
    finally:
        logger.info("LLM request provider=claude_code elapsed_seconds=%.3f", loop.time() - started)
    return response
