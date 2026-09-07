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
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
except ImportError:  # pragma: no cover
    AssistantMessage = None
    ClaudeAgentOptions = None
    TextBlock = None
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
    """Serialize the existing Gemini-shaped message history in order."""
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
            function_call = (
                part.get("function_call") if isinstance(part, dict) else getattr(part, "function_call", None)
            )
            if text:
                text_parts.append(str(text))
            if function_call:
                name = function_call.get("name") if isinstance(function_call, dict) else getattr(function_call, "name", None)
                args = function_call.get("args", {}) if isinstance(function_call, dict) else getattr(function_call, "args", {})
                if not name:
                    raise ClaudeCodeRequestError("Malformed Gemini function call")
                text_parts.append(
                    f"[Assistant tool call: {name}({json.dumps(args, sort_keys=True)})]"
                )
        if text_parts:
            lines.append(f"{role_name}:\n" + "\n".join(text_parts))

    if not lines:
        raise ClaudeCodeRequestError("Claude Code request contains no message content")
    return "\n\n".join(lines)


def _options() -> Any:
    if ClaudeAgentOptions is None:
        raise ClaudeCodeUnavailableError(
            "claude-agent-sdk is not installed; install the project dependency before using LLM_PROVIDER=claude_code"
        )

    return ClaudeAgentOptions(
        model=os.getenv("CLAUDE_CODE_MODEL") or None,
        max_turns=_int_env("CLAUDE_CODE_MAX_TURNS", 1),
        max_output_tokens=_int_env("CLAUDE_CODE_MAX_OUTPUT_TOKENS", 8192),
        permission_mode=os.getenv("CLAUDE_CODE_PERMISSION_MODE", "default"),
        allowed_tools=[],
        disallowed_tools=[
            "Bash", "Read", "Write", "Edit", "NotebookEdit",
            "Glob", "Grep", "WebFetch", "WebSearch", "Task",
        ],
        cwd=os.getenv("CLAUDE_CODE_WORKING_DIRECTORY") or None,
    )


def _text_from_message(message: Any) -> str:
    if AssistantMessage is None or TextBlock is None or not isinstance(message, AssistantMessage):
        return ""
    return "".join(block.text for block in message.content if isinstance(block, TextBlock))


async def _consume_query(
    prompt: str,
    options: Any,
    client: Callable[..., AsyncIterator[Any]],
) -> str:
    chunks: list[str] = []
    try:
        async for message in client(prompt=prompt, options=options):
            text = _text_from_message(message)
            if text:
                chunks.append(text)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        message = str(exc).lower()
        if "auth" in message or "credential" in message or "login" in message:
            raise ClaudeCodeRequestError("Claude Code authentication failed") from exc
        raise ClaudeCodeRequestError("Claude Code request failed") from exc

    response = "".join(chunks).strip()
    if not response:
        raise ClaudeCodeRequestError("Claude Code returned an empty response")
    return response


def _response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)])
            )
        ]
    )


async def invoke(
    contents: list[Any],
    *,
    system_prompt: str | None = None,
    timeout_seconds: int | None = None,
    client: Callable[..., AsyncIterator[Any]] | None = None,
) -> types.GenerateContentResponse:
    """Invoke Claude Code asynchronously using the existing response contract."""
    if query is None and client is None:
        raise ClaudeCodeUnavailableError(
            "claude-agent-sdk is not installed; install the project dependency before using LLM_PROVIDER=claude_code"
        )

    logical_prompt = _messages_to_prompt(contents)
    prompt = logical_prompt if not system_prompt else f"System instructions:\n{system_prompt}\n\n{logical_prompt}"
    options = _options()
    timeout = timeout_seconds or _int_env("CLAUDE_CODE_TIMEOUT_SECONDS", 180)
    query_client = client or query
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        text = await asyncio.wait_for(
            _consume_query(prompt, options, query_client),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        raise ClaudeCodeTimeoutError(f"Claude Code timed out after {timeout} seconds") from exc
    finally:
        logger.info(
            "LLM request provider=claude_code elapsed_seconds=%.3f",
            loop.time() - started,
        )
    return _response(text)
