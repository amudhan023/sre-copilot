"""File-backed LLM transport: something outside this process answers the prompt.

Every LLM call in this project can be routed through a pair of directories
instead of a provider API. The caller writes the exact prompt it would have
sent to ``llm_calls/request/``, blocks until a matching file shows up in
``llm_calls/response/``, and treats that file's contents as the model reply.

Two reasons this exists:

* The hosted models go through long ``503 UNAVAILABLE`` windows that strand a
  run halfway through. With this transport a run can be finished by hand, or
  by any script that watches the request directory.
* Every prompt and every answer lands on disk, so a run can be audited or
  replayed afterwards.

Turn it on with ``LLM_TRANSPORT=file``. The default stays ``api``, so normal
behaviour is unchanged unless somebody opts in.

File names pair a request with its answer::

    llm_calls/request/<issue>.request.<timestamp>.txt
    llm_calls/response/<issue>.response.<timestamp>.txt

``<issue>`` labels the caller (an incident id, an evaluation case id) and
``<timestamp>`` is UTC to the millisecond, so concurrent calls never collide.
Each request file names the response file to create, which means the person
answering never has to work the convention out for themselves.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CALLS_DIR = REPO_ROOT / "llm_calls"

# A person has to read the prompt and write an answer, so the default wait is
# generous. Polling every couple of seconds keeps the turnaround snappy
# without spinning the CPU.
DEFAULT_TIMEOUT_SECONDS = 900.0
DEFAULT_POLL_SECONDS = 2.0

_UNSAFE_ISSUE_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_ISSUE_CHARS = 60

_REQUEST_MARKER = "===== REQUEST BEGINS ====="


class FileTransportTimeout(TimeoutError):
    """No usable response file appeared before the deadline."""


def transport_is_enabled() -> bool:
    """Report whether callers should use files instead of a provider API."""
    return os.getenv("LLM_TRANSPORT", "api").strip().lower() == "file"


def calls_dir() -> Path:
    return Path(os.getenv("LLM_CALLS_DIR", str(DEFAULT_CALLS_DIR)))


def request_dir() -> Path:
    return calls_dir() / "request"


def response_dir() -> Path:
    return calls_dir() / "response"


def _seconds_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number of seconds, got {raw!r}") from None
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero, got {value}")
    return value


def issue_slug(issue: str) -> str:
    """Reduce a caller label to something safe to put in a file name.

    Dots are stripped along with everything else unusual, because the file
    name uses dots to separate the issue, the kind, and the timestamp.
    """
    slug = _UNSAFE_ISSUE_CHARS.sub("-", (issue or "").strip()).strip("-")
    return slug[:_MAX_ISSUE_CHARS].rstrip("-") or "llm"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3]


def _render_request(prompt: str, response_path: Path, schema: dict[str, Any] | None) -> str:
    if schema is None:
        reply_format = (
            "Write the model's answer as plain text. The whole file is taken\n"
            "# as the answer."
        )
        schema_block = ""
    else:
        reply_format = (
            "Reply with a single JSON object matching the schema at the bottom\n"
            "# of this file. A ```json fence around it is fine; anything outside\n"
            "# the object is ignored."
        )
        schema_block = (
            "\n\n===== REQUIRED JSON RESPONSE SCHEMA =====\n"
            + json.dumps(schema, indent=2)
        )

    header = (
        "# --------------------------------------------------------------------\n"
        "# SRE Copilot is blocked waiting for an answer to the prompt below.\n"
        "#\n"
        "# Reply by creating exactly this file:\n"
        f"#   {response_path}\n"
        "#\n"
        f"# {reply_format}\n"
        "#\n"
        "# Everything above the REQUEST marker is instructions to you and is not\n"
        "# part of the prompt. Put only the answer in the response file: do not\n"
        "# echo these instructions or the prompt back.\n"
        "# --------------------------------------------------------------------\n\n"
    )
    return f"{header}{_REQUEST_MARKER}\n{prompt}{schema_block}\n"


def _find_response(directory: Path, issue: str, stamp: str) -> Path | None:
    """Locate the answer file, accepting any extension for convenience."""
    exact = directory / f"{issue}.response.{stamp}.txt"
    if exact.exists():
        return exact
    matches = sorted(directory.glob(f"{issue}.response.{stamp}.*"))
    return matches[0] if matches else None


def _signature(path: Path) -> tuple[int, int] | None:
    """Return (size, mtime) so a half-written file can be spotted."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def exchange(
    issue: str,
    prompt: str,
    *,
    schema: dict[str, Any] | None = None,
    timeout: float | None = None,
    poll_interval: float | None = None,
) -> str:
    """Write a prompt to disk and block until somebody answers it.

    Returns the response file's contents with surrounding whitespace removed.
    Raises FileTransportTimeout if nothing usable arrives in time.
    """
    if timeout is None:
        timeout = _seconds_env("LLM_FILE_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)
    if poll_interval is None:
        poll_interval = _seconds_env("LLM_FILE_POLL_INTERVAL", DEFAULT_POLL_SECONDS)

    name = issue_slug(issue)
    stamp = _timestamp()
    requests, responses = request_dir(), response_dir()
    requests.mkdir(parents=True, exist_ok=True)
    responses.mkdir(parents=True, exist_ok=True)

    response_path = responses / f"{name}.response.{stamp}.txt"
    request_path = requests / f"{name}.request.{stamp}.txt"
    request_path.write_text(_render_request(prompt, response_path, schema), encoding="utf-8")

    print(f"[llm-file] request written: {request_path}", file=sys.stderr, flush=True)
    print(f"[llm-file] waiting for:     {response_path}", file=sys.stderr, flush=True)

    # Require the file to look identical on two consecutive polls before
    # reading it. A response being written a chunk at a time would otherwise
    # be picked up truncated, and a truncated answer is worse than a slow one.
    previous: tuple[int, int] | None = None
    deadline = time.monotonic() + timeout
    while True:
        found = _find_response(responses, name, stamp)
        if found is not None:
            current = _signature(found)
            if current is not None and current[0] > 0 and current == previous:
                text = found.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    print(f"[llm-file] response read:   {found}", file=sys.stderr, flush=True)
                    return text
            previous = current
        else:
            previous = None

        if time.monotonic() >= deadline:
            raise FileTransportTimeout(
                f"No response for {request_path.name} after {timeout:.0f}s. "
                f"Create {response_path} to answer it."
            )
        time.sleep(poll_interval)


async def aexchange(issue: str, prompt: str, **kwargs: Any) -> str:
    """Async wrapper so event-loop callers do not block on the poll."""
    return await asyncio.to_thread(lambda: exchange(issue, prompt, **kwargs))


def extract_json(text: str) -> Any:
    """Pull a JSON value out of a response file.

    People answering by hand tend to wrap JSON in a markdown fence or add a
    sentence of commentary, so fall back to slicing out the outermost braces
    rather than rejecting an answer that is really valid underneath.
    """
    candidates = [text]

    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))

    for opening, closing in (("{", "}"), ("[", "]")):
        start, end = text.find(opening), text.rfind(closing)
        if 0 <= start < end:
            candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"Response file did not contain valid JSON: {text[:200]!r}")
