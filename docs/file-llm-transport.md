# File-backed LLM transport

Every LLM call in this project can be answered from a pair of directories
instead of a provider API. The caller writes the exact prompt it would have
sent, waits, and reads the reply back off disk.

```
llm_calls/request/<issue>.request.<timestamp>.txt     written by the app
llm_calls/response/<issue>.response.<timestamp>.txt   written by whoever answers
```

`<issue>` labels the caller: an incident id for the agent, a case id plus a
metric name for the evaluation. `<timestamp>` is UTC to the millisecond, so
two calls never collide. Each request file names the exact response file to
create, so nobody has to reconstruct the naming convention by hand.

## Why it exists

- The hosted models go through long `503 UNAVAILABLE` windows. A run that
  dies two thirds of the way through wastes the retrieval work already done.
  With this transport, a run can be finished by hand or by any process that
  watches the request directory.
- Every prompt and every answer lands on disk, so a run can be audited,
  diffed, or replayed later.

## Turning it on

```bash
LLM_TRANSPORT=file uv run python scripts/rag_eval.py
```

`LLM_TRANSPORT` defaults to `api`, so behaviour is unchanged unless you opt
in. The switch is global: it covers agent turns, evaluation answer
generation, and the Ragas judge calls.

You can also list `file` in `LLM_PROVIDERS` to keep it as a last-resort
backup that only runs once the API providers give up. The valid names are
`gemini`, `groq`, `claude_code` and `file`, and with `LLM_STRATEGY=fallback`
they are tried left to right:

```bash
LLM_PROVIDERS=gemini,groq,claude_code,file
```

A provider is only skipped for a *transient* failure (HTTP 429 or 503, or a
timeout); anything else stops the chain, because retrying a rejected request
on another provider just wastes the next one. The Claude Code provider wraps
every SDK error in `ClaudeCodeRequestError`, so `_status_code` in
`agent/llm.py` follows the `__cause__` chain to find the original status —
without that, a Claude overload would look permanent and never reach `file`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `LLM_TRANSPORT` | `api` | Set to `file` to route every call through disk. |
| `LLM_CALLS_DIR` | `<repo>/llm_calls` | Where the two directories live. |
| `LLM_FILE_TIMEOUT` | `900` | Seconds to wait for one response file. |
| `LLM_FILE_POLL_INTERVAL` | `2` | Seconds between checks. |
| `LLM_FILE_VALIDATION_ATTEMPTS` | `3` | How many times a Ragas judge reply may fail its schema before the run gives up. |

## Answering a request

Read the request file. Everything above the `===== REQUEST BEGINS =====`
marker is instructions to you; everything below is the prompt. Then create
the response file the header names.

There are two reply formats.

1. **Plain text.** Used for agent turns and for evaluation answer generation.
   The whole file is the answer.
2. **JSON.** Used for Ragas judge calls, which ask for structured output. The
   request ends with the required JSON schema. Reply with one JSON object
   matching it. A ` ```json ` fence and surrounding prose are both tolerated —
   the parser slices the object out. If the object does not validate, the
   next request repeats the prompt with the rejection and the schema error
   attached.

### Calling a tool from an agent turn

An agent turn has a third option: ask for a tool call instead of answering.
The request file spells this out, and the shape it asks for is the same one
the Claude Code provider uses, so both providers answer the same contract.

```json
{"action": "tool",
 "tool_name": "search_similar_incidents",
 "arguments": {"query": "connection pool exhaustion"}}
```

`{"action": "final", "answer": "..."}` is the explicit way to finish, though
plain prose does the same thing. The OpenAI assistant-message shape still
works too, and so does a bare `{"name": ..., "args": {...}}`:

```json
{"content": null,
 "tool_calls": [{"id": "call_1", "type": "function",
                 "function": {"name": "search_similar_incidents",
                              "arguments": {"query": "connection pool"}}}]}
```

`arguments` may be an object or a JSON-encoded string; both work.

Three rules keep this safe:

- **A reply is only read as a tool call when it has one of those shapes.**
  A final report often quotes a JSON log line, and that has to stay prose.
  Anything else JSON-shaped is kept whole as the answer.
- **Tool names are checked against the tools listed in the request.** An
  invented name is rejected in `agent/llm.py` rather than failing later as an
  opaque MCP error.
- **A reply that is neither an answer nor a tool call is an error.** It would
  otherwise produce a message with no parts, which `route_after_llm` reads as
  "the model is finished" — ending the investigation with an empty report.

Tenant scoping still happens in `tool_node`, so a tenant written into the
arguments is overwritten. The file transport does not widen the security
boundary.

## Two things to know

**Write the file atomically.** The reader waits until a file's size and
modification time are unchanged across two consecutive polls before it reads,
and it ignores empty files. That covers an editor that saves once. If you
generate a response with a script, write to a temporary name in the same
directory and `mv` it into place, so a slow write can never be read half
finished.

**Embeddings still need the API.** `AnswerRelevancy` compares embedding
vectors, and nobody can write an embedding by hand, so `GEMINI_API_KEY` is
still required for the evaluation even under `LLM_TRANSPORT=file`. The
embedding endpoint is separate from the chat models and has been the reliable
half.

## Where it lives

- `src/sre_copilot/llm_files.py` — the transport: file naming, the request
  header, the poll loop, JSON extraction.
- `src/sre_copilot/rag/evaluation/file_llm.py` — `FileInstructorLLM`, the
  Ragas evaluator that reads its structured answers from files.
- `src/sre_copilot/agent/llm.py` — the `file` provider alongside `gemini` and
  `groq`.
- `src/sre_copilot/rag/evaluation/runner.py` — picks the file transport for
  answer generation and for the judge.
