import json
import logging
import os
import random
from enum import Enum
from typing import Any

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types

# This module is the only place that actually calls an LLM. It tries Gemini
# first, and falls back to Groq if Gemini is unavailable or fails with a
# transient error. Since Gemini and Groq use different message/tool-call
# shapes (Gemini's native format vs Groq's OpenAI-compatible one), a good
# chunk of this file is just translating between the two so the rest of the
# agent doesn't have to care which provider actually answered. There's also
# a safety mechanism here that caps how much tool-result text gets sent back
# to Groq, since oversized payloads can blow past its context/request limits.

load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_DEFAULT_MODEL = "gemini-3.5-flash-lite"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-20b"
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MAX_TOOL_RESULT_CHARS = 12_000
GROQ_MAX_HISTORY_TOOL_CHARS = 48_000

SYSTEM_INSTRUCTION = """
You are an SRE investigation agent.

Investigate incidents systematically using the available observability tools.

Investigation process:
1. Identify the affected service and incident time window.
2. Discover available metrics for the service.
3. Query relevant metrics.
4. Search service logs for errors, warnings, and symptoms.
5. Check traces for failed or slow operations.
6. Check recent deployments around the incident.
7. Search historical incidents for similar failures using the current
   incident's observed symptoms and concrete technical evidence.
8. Treat historical incidents as supporting context and hypothesis
   generation, not as proof of the current root cause.
9. Correlate current-incident evidence across metrics, logs, traces,
   deployments, and historical incidents before forming an RCA.
10. Do not invent operation names when querying traces.
11. If the operation is unknown, search traces with an empty operation
    to discover available operations.
12. Never describe CPU usage as observed evidence unless a CPU metric
    was actually returned by a tool.
13. If the alert claims high CPU but no CPU telemetry exists, state:
    "The alert reports high CPU, but CPU telemetry was not available."
14. Confidence must reflect missing evidence.
15. If a key signal required to prove the alert condition is missing,
    do not assign High confidence solely from indirect correlations.
16. Temporal correlation with a deployment is evidence of correlation,
    not proof that the deployment caused the incident.

Evidence rules:
- Only use telemetry that was actually returned by a tool.
- Never invent metrics, logs, traces, deployments, or incidents.
- Distinguish observed evidence from inference.
- If a requested telemetry source is unavailable, explicitly say so.
- A single signal should not automatically be treated as the root cause.
- Look for temporal correlation between signals.
- Prefer explanations supported by multiple independent signals.
- The incident description or alert is a claim, not evidence. Never restate it
  as an observed fact.

Historical incident / RAG rules:
- When calling search_similar_incidents, construct the query from
  concrete symptoms observed in the current incident.
- Include relevant technical terms such as database timeouts,
  connection pool exhaustion, request latency, failed operations,
  or specific error messages when they were actually observed.
- Do not search using only the alert name.
- Do not invent symptoms just to improve the historical search query.
- Historical incidents describe past events and must never be treated
  as direct evidence of what happened in the current incident.
- A historical incident with a similar root cause can support a hypothesis,
  but current telemetry must support the conclusion.
- If multiple historical incidents are returned, look for recurring
  patterns, but do not assume the current incident has the same cause.
- If no relevant historical incidents are returned, continue the
  investigation using current telemetry.

Final response format:
Alert claim:
- Restate what the incident/alert asserts, verbatim, without endorsing it.

Observed telemetry:
- Metrics: list what was returned. For any metric implied by the alert claim
  that was not returned (e.g. no CPU metric exists), say so explicitly.
- Logs:
- Traces:
- Deployments:
- Similar incidents:
  - List relevant historical incidents returned by the RAG tool.
  - Explain briefly why each incident is similar.
  - Clearly label this as historical evidence rather than current telemetry.

Inference:
- State what the observed telemetry does and does not support.
- Explicitly call out any part of the alert claim that remains unconfirmed.
- Note any temporal correlations and label them as suggestive, not proof.
- If historical incidents support the suspected root cause, explicitly
  state that they strengthen the hypothesis but do not prove causation.
  
Likely root cause:
- Explain the most likely cause and which specific observed telemetry
  supports it. Do not fold the unconfirmed alert claim into this section.

Confidence:
- High / Medium / Low.
- Reserve "High" for when multiple independent signals directly confirm the
  exact stated cause. Temporal correlation alone, or the alert's own wording,
  is never sufficient for "High" — use "Medium" or "Low" instead.
- If the alert's named symptom (e.g. CPU) is unconfirmed by telemetry, say so
  here regardless of the confidence assigned to the root cause.

Recommended next steps:
- Concrete actions for the SRE, including verifying any telemetry the alert
  claimed but that tools did not return.
"""

class ProviderConfigurationError(RuntimeError):
    """A provider is configured incorrectly and must not be skipped."""


class ProvidersUnavailableError(RuntimeError):
    """Every selected provider failed with a temporary availability error."""


class ProviderRequestError(RuntimeError):
    """A provider rejected a validly formed request and should not be retried."""


def _provider_names() -> list[str]:
    providers = [
        provider.strip().lower()
        for provider in os.getenv("LLM_PROVIDERS", "gemini,groq").split(",")
        if provider.strip()
    ]
    if not providers:
        raise ProviderConfigurationError("LLM_PROVIDERS must name at least one provider")
    unknown = set(providers) - {"gemini", "groq"}
    if unknown:
        raise ProviderConfigurationError(
            f"Unsupported LLM provider(s): {', '.join(sorted(unknown))}"
        )
    return providers


def _api_key(provider: str) -> str:
    variable = f"{provider.upper()}_API_KEY"
    key = os.getenv(variable)
    if not key:
        raise ProviderConfigurationError(f"{variable} is required for provider '{provider}'")
    return key


def _status_code(error: Exception) -> int | None:
    for value in (
        getattr(error, "status_code", None),
        getattr(error, "code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        if isinstance(value, int):
            return value
    return None


def _is_transient(error: Exception) -> bool:
    return (
        _status_code(error) in {429, 503}
        or isinstance(error, (TimeoutError, httpx.TimeoutException, httpx.TransportError))
    )


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        schema = value
    elif hasattr(value, "model_dump"):
        schema = value.model_dump(exclude_none=True)
    else:
        raise ProviderConfigurationError("Malformed Gemini-compatible tool definition")
    return _json_schema(schema)


def _json_schema(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value.lower()
    if isinstance(value, dict):
        return {key: _json_schema(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_schema(item) for item in value]
    return value


def _groq_tools(tool: Any) -> list[dict[str, Any]]:
    declarations = getattr(tool, "function_declarations", None)
    if declarations is None:
        raise ProviderConfigurationError("Malformed Gemini-compatible tool definition")
    tools = []
    for declaration in declarations:
        name = getattr(declaration, "name", None)
        parameters = getattr(declaration, "parameters", None)
        if not name or parameters is None:
            raise ProviderConfigurationError("Malformed Gemini-compatible tool definition")
        tools.append({"type": "function", "function": {
            "name": name,
            "description": getattr(declaration, "description", "") or "",
            "parameters": _as_dict(parameters),
        }})
    return tools


def _truncate_tool_result(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    marker = "\n[Tool output truncated for Groq request size]\n"
    if limit <= len(marker):
        return marker[:max(limit, 0)]
    remaining = limit - len(marker)
    head = remaining // 2
    tail = remaining - head
    return content[:head] + marker + content[-tail:]


def _cap_groq_tool_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Limit old tool output without reducing the original incident description."""
    remaining = GROQ_MAX_HISTORY_TOOL_CHARS
    for message in reversed(messages):
        content = message["content"]
        if message["role"] != "tool":
            continue
        capped = _truncate_tool_result(content, GROQ_MAX_TOOL_RESULT_CHARS)
        message["content"] = _truncate_tool_result(capped, max(remaining, 1))
        remaining = max(remaining - len(message["content"]), 0)
    return messages


def _call_field(call: Any, field: str, default: Any = None) -> Any:
    if isinstance(call, dict):
        return call.get(field, default)
    return getattr(call, field, default)


def _tool_result(text: str) -> tuple[str, str] | None:
    if not text.startswith("Result from ") or ":\n" not in text:
        return None
    name, content = text.removeprefix("Result from ").split(":\n", 1)
    return name, content


def _groq_messages(contents: list[Any]) -> list[dict[str, Any]]:
    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    pending_calls: list[tuple[str, str]] = []
    for content_index, content in enumerate(contents):
        role = getattr(content, "role", None)
        parts = getattr(content, "parts", None)
        if isinstance(content, dict):
            role = content.get("role")
            parts = content.get("parts")
        if role not in {"user", "model"} or not isinstance(parts, list):
            raise ProviderConfigurationError("Malformed Gemini conversation contents")

        text_parts = []
        tool_calls = []
        for part in parts:
            text = getattr(part, "text", None)
            function_call = getattr(part, "function_call", None)
            if isinstance(part, dict):
                text = part.get("text")
                function_call = part.get("function_call")
            if function_call:
                name = _call_field(function_call, "name")
                args = _call_field(function_call, "args", {})
                call_id = _call_field(function_call, "id") or f"call_{content_index}_{len(tool_calls)}"
                if not name:
                    raise ProviderConfigurationError("Malformed Gemini function call")
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                })
                pending_calls.append((call_id, name))
            elif text:
                text_parts.append(text)

        if role == "model":
            message: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                message["tool_calls"] = tool_calls
            messages.append(message)
            continue

        for text in text_parts:
            result = _tool_result(text)
            if result:
                name, result_content = result
                match = next(((call_id, call_name) for call_id, call_name in pending_calls if call_name == name), None)
                if match:
                    pending_calls.remove(match)
                    messages.append({"role": "tool", "tool_call_id": match[0], "content": result_content})
                    continue
            messages.append({"role": "user", "content": text})
    return _cap_groq_tool_history(messages)


def _groq_response(payload: dict[str, Any]):
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("Groq returned a malformed response") from error

    parts = []
    if message.get("content"):
        parts.append(types.Part(text=message["content"]))
    for tool_call in message.get("tool_calls", []):
        try:
            function = tool_call["function"]
            args = json.loads(function["arguments"] or "{}")
            parts.append(types.Part(function_call=types.FunctionCall(
                id=tool_call.get("id"), name=function["name"], args=args
            )))
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("Groq returned a malformed tool call") from error

    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


def _call_gemini(contents: list[Any], tool: Any):
    client = genai.Client(api_key=_api_key("gemini"))
    config = types.GenerateContentConfig(
        tools=[tool],
        system_instruction=SYSTEM_INSTRUCTION,
    )
    return client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", GEMINI_DEFAULT_MODEL),
        contents=contents,
        config=config,
    )


def _call_groq(contents: list[Any], tool: Any):
    payload = {
        "model": os.getenv("GROQ_MODEL", GROQ_DEFAULT_MODEL),
        "messages": _groq_messages(contents),
        "tools": _groq_tools(tool),
        "tool_choice": "auto",
    }
    payload_bytes = len(json.dumps(payload).encode("utf-8"))
    logger.info(
        "Groq request payload: %d bytes, %d messages, %d tools",
        payload_bytes, len(payload["messages"]), len(payload["tools"]),
    )
    response = httpx.post(
        GROQ_CHAT_COMPLETIONS_URL,
        headers={"Authorization": f"Bearer {_api_key('groq')}"},
        json=payload,
        timeout=30,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 413:
            raise ProviderRequestError(
                f"Groq rejected the {payload_bytes}-byte request with HTTP 413"
            ) from error
        raise
    return _groq_response(response.json())


def _call_provider(provider: str, contents: list[Any], tool: Any):
    logger.info("LLM provider: %s", provider)
    if provider == "gemini":
        return _call_gemini(contents, tool)
    return _call_groq(contents, tool)


def continue_gemini(contents, tool):
    """Call a configured LLM and return the Gemini-compatible response shape."""
    providers = _provider_names()
    strategy = os.getenv("LLM_STRATEGY", "fallback").lower()
    if strategy not in {"fallback", "random"}:
        raise ProviderConfigurationError("LLM_STRATEGY must be 'fallback' or 'random'")

    if strategy == "random":
        provider = random.choice(providers)
        try:
            return _call_provider(provider, contents, tool)
        except Exception as error:
            if _is_transient(error):
                raise ProvidersUnavailableError(f"{provider} is temporarily unavailable") from error
            raise

    last_error = None
    for index, provider in enumerate(providers):
        try:
            return _call_provider(provider, contents, tool)
        except Exception as error:
            if not _is_transient(error):
                raise
            last_error = error
            if index < len(providers) - 1:
                status = _status_code(error)
                detail = str(status) if status else type(error).__name__
                logger.warning(
                    "%s failed with %s; falling back to %s",
                    provider.title(), detail, providers[index + 1],
                )

    raise ProvidersUnavailableError("All configured LLM providers are temporarily unavailable") from last_error
