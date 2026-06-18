"""Anthropic Claude wrapper — single-turn chat and full agentic tool-use loop."""
from __future__ import annotations
import logging
import os
import time
from typing import Any, Callable, Optional

import anthropic

logger = logging.getLogger(__name__)

SONNET = "claude-sonnet-4-6"
HAIKU  = "claude-haiku-4-5-20251001"

_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def chat(
    system: str,
    user: str,
    model: str = SONNET,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    max_retries: int = 3,
) -> str:
    """Single-turn chat. Returns assistant text."""
    client = get_client()
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return resp.content[0].text
        except anthropic.RateLimitError:
            wait = 60 * attempt
            logger.warning("Rate limited — waiting %ds (attempt %d)", wait, attempt)
            time.sleep(wait)
        except anthropic.APIError as exc:
            if attempt == max_retries:
                raise
            logger.warning("Anthropic API error (attempt %d): %s", attempt, exc)
            time.sleep(5 * attempt)
    raise RuntimeError("LLM call failed after all retries.")


def run_tool_use_agent(
    system: str,
    initial_prompt: str,
    tools: list[dict],
    tool_executor: Callable[[str, dict], str],
    model: str = SONNET,
    max_tokens: int = 4096,
    max_rounds: int = 12,
) -> str:
    """
    Full agentic loop: automatically handles tool calls via tool_executor.

    tool_executor(tool_name, tool_input) -> str result
    Returns final assistant text after all tool rounds complete.
    """
    client = get_client()
    messages: list[dict] = [{"role": "user", "content": initial_prompt}]

    for round_num in range(max_rounds):
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )

        assistant_content = resp.content
        messages.append({"role": "assistant", "content": assistant_content})

        if resp.stop_reason == "end_turn":
            return next((b.text for b in assistant_content if hasattr(b, "text")), "")

        if resp.stop_reason == "tool_use":
            tool_results = []
            for block in assistant_content:
                if block.type != "tool_use":
                    continue
                logger.info("Tool call [round %d]: %s | input: %s", round_num, block.name, block.input)
                try:
                    result = tool_executor(block.name, block.input)
                except Exception as exc:
                    result = f"ERROR: {exc}"
                    logger.exception("Tool %s failed: %s", block.name, exc)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result),
                })

            messages.append({"role": "user", "content": tool_results})
            continue

        logger.warning("Unexpected stop_reason: %s — stopping agent loop.", resp.stop_reason)
        break

    return "Agent reached maximum rounds without conclusion."


def extract_json_block(text: str) -> Optional[str]:
    """Extract JSON from a markdown code block if present, else return the text as-is."""
    import re
    match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    # Try bare JSON object/array
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        return match.group(1)
    return text
