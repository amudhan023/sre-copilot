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
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, query
except ImportError:  # pragma: no cover
    AssistantMessage = None
    ClaudeAgentOptions = None
    ResultMessage = None
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


def _tool_definitions(tool: Any) -> list[dict[str, Any]]:
    declarations = getattr(tool, "function_declarations", None)
    if declarations is None:
        raise ClaudeCodeRequestError("Malformed Gemini-compatible tool definition")
    definitions: list[dict[str, Any]] = []
    for declaration in declarations:
        name = getattr(declaration, "name", None)
        if not name:
            raise ClaudeCodeRequestError("Malformed Gemini-compatible tool definition")
        definitions.append({
            "name": name,
            "description": getattr(declaration, "description", "") or "",
        })
    if not definitions:
        raise ClaudeCodeRequestError("No MCP tools were supplied to Claude Code")
    return definitions


def _options(tool: Any) -> Any:
    if ClaudeAgentOptions is None:
        raise ClaudeCodeUnavailableError(
            "claude-agent-sdk is not installed; install the project dependency before using LLM_PROVIDER=claude_code"
        )
    from sre_copilot.agent.llm import SYSTEM_INSTRUCTION

    tools = _tool_definitions(tool)
    tool_catalog = "\n".join(
        f"- {item['name']}: {item['description']}" for item in tools
    )
    system_prompt = (
        f"{SYSTEM_INSTRUCTION}\n\n"
        "You are running behind an application-controlled LangGraph tool loop. "
        "Do not execute tools yourself. Decide either to call exactly one of the "
        "listed MCP tools or to provide the final answer. Return only the requested "
        "structured decision. Tool arguments must match the selected tool schema.\n\n"
        "Available MCP tools:\n"
        f"{tool_catalog}"
    )
    return ClaudeAgentOptions(
        model=os.getenv("CLAUDE_CODE_MODEL") or None,
        max_turns=_int_env("CLAUDE_CODE_MAX_TURNS", 1),
        permission_mode=os.getenv("CLAUDE_CODE_PERMISSION_MODE", "default"),
        allowed_tools=[],
        disallowed_tools=[
            "Bash", "Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep",
            "WebFetch", "WebSearch", "Task",
        ],
        cwd=os.getenv("CLAUDE_CODE_WORKING_DIRECTORY") or None,
        system_prompt=system_prompt,
        output_format={
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["tool", "final"]},
                    "tool_name": {"type": ["string", "null"]},
                    "arguments": {"type": "object", "additionalProperties": True},
                    "answer": {"type": ["string", "null"]},
                },
                "required": ["action", "tool_name", "arguments", "answer"],
                "additionalProperties": False,
            },
        },
    )


def _result_from_messages(messages: list[Any], tool: Any) -> types.GenerateContentResponse:
    """Convert Claude's structured decision into the Gemini-compatible response."""
    structured: dict[str, Any] | None = None
    for message in messages:
        if ResultMessage is not None and isinstance(message, ResultMessage):
            candidate = getattr(message, "structured_output", None)
            if isinstance(candidate, dict):
                structured = candidate
            if getattr(message, "is_error", False):
                raise ClaudeCodeRequestError("Claude Code request failed")

    if structured is not None:
        action = structured.get("action")
        if action == "final":
            answer = structured.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise ClaudeCodeRequestError("Claude Code returned an empty final answer")
            return types.GenerateContentResponse(
                candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=answer)]))]
            )
        if action == "tool":
            name = structured.get("tool_name")
            args = structured.get("arguments") or {}
            if not isinstance(name, str) or not name:
                raise ClaudeCodeRequestError("Claude Code returned a malformed tool request")
            allowed_names = {item["name"] for item in _tool_definitions(tool)}
            if name not in allowed_names:
                raise ClaudeCodeRequestError(f"Claude Code requested unknown tool '{name}'")
            if not isinstance(args, dict):
                raise ClaudeCodeRequestError("Claude Code returned malformed tool arguments")
            return types.GenerateContentResponse(
                candidates=[types.Candidate(content=types.Content(
                    role="model",
                    parts=[types.Part(function_call=types.FunctionCall(
                        id=f"claude_{name}", name=name, args=args
                    ))],
                ))]
            )
        raise ClaudeCodeRequestError("Claude Code returned an invalid structured action")

    # Keep a defensive compatibility path for SDK/client fakes that expose
    # AssistantMessage text, but never treat Claude Code's own tools as MCP calls.
    parts: list[types.Part] = []
    for message in messages:
        if AssistantMessage is None or not isinstance(message, AssistantMessage):
            continue
        for block in getattr(message, "content", []):
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                parts.append(types.Part(text=text))
    if not parts:
        raise ClaudeCodeRequestError("Claude Code returned no usable response")
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


async def _consume_query(
    prompt: str,
    options: Any,
    client: Callable[..., AsyncIterator[Any]],
    tool: Any,
) -> types.GenerateContentResponse:
    messages: list[Any] = []
    try:
        async for message in client(prompt=prompt, options=options):
            messages.append(message)
    except asyncio.CancelledError:
        raise
    except ClaudeCodeRequestError:
        raise
    except Exception as exc:
        lowered = str(exc).lower()
        if "auth" in lowered or "credential" in lowered or "login" in lowered:
            raise ClaudeCodeRequestError("Claude Code authentication failed") from exc
        raise ClaudeCodeRequestError("Claude Code request failed") from exc
    return _result_from_messages(messages, tool)


async def invoke(
    contents: list[Any],
    *,
    tool: Any,
    timeout_seconds: int | None = None,
    client: Callable[..., AsyncIterator[Any]] | None = None,
) -> types.GenerateContentResponse:
    """Invoke Claude Code and return a Gemini-compatible text/tool-call response."""
    if query is None and client is None:
        raise ClaudeCodeUnavailableError(
            "claude-agent-sdk is not installed; install the project dependency before using LLM_PROVIDER=claude_code"
        )
    prompt = _messages_to_prompt(contents)
    options = _options(tool)
    timeout = timeout_seconds or _int_env("CLAUDE_CODE_TIMEOUT_SECONDS", 180)
    query_client = client or query
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        response = await asyncio.wait_for(
            _consume_query(prompt, options, query_client, tool), timeout=timeout
        )
    except asyncio.TimeoutError as exc:
        raise ClaudeCodeTimeoutError(f"Claude Code timed out after {timeout} seconds") from exc
    finally:
        logger.info(
            "LLM request provider=claude_code elapsed_seconds=%.3f",
            loop.time() - started,
        )
    return response


def invoke_sync(contents: list[Any], *, tool: Any) -> types.GenerateContentResponse:
    """Synchronous adapter for the existing provider boundary."""
    return asyncio.run(invoke(contents, tool=tool))
