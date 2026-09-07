"""CLI entry point for the end-to-end Ragas RAG evaluation."""

import asyncio

from sre_copilot.rag.evaluation.runner import async_main, parse_args


if __name__ == "__main__":
    asyncio.run(async_main(parse_args()))
