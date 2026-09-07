"""Compatibility helpers for the Ragas 0.4.3 Vertex AI import path.

Ragas 0.4.3 imports ChatVertexAI and VertexAI from paths that were removed
from langchain-community. The implementations now live in
langchain-google-vertexai. Install the latter and register compatibility
modules before importing Ragas.
"""

import sys
import types


def patch_ragas_vertexai_imports() -> None:
    """Provide the legacy Ragas Vertex AI modules from the new package."""
    if "langchain_community.chat_models.vertexai" not in sys.modules:
        from langchain_google_vertexai import ChatVertexAI

        chat_vertexai = types.ModuleType("langchain_community.chat_models.vertexai")
        chat_vertexai.ChatVertexAI = ChatVertexAI
        sys.modules["langchain_community.chat_models.vertexai"] = chat_vertexai

    if "langchain_community.llms.vertexai" not in sys.modules:
        from langchain_google_vertexai import VertexAI

        vertexai = types.ModuleType("langchain_community.llms.vertexai")
        vertexai.VertexAI = VertexAI
        sys.modules["langchain_community.llms.vertexai"] = vertexai
