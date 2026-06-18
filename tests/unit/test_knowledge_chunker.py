"""Unit tests for the knowledge seeder chunking and frontmatter parsing."""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from knowledge.seeder.src.main import (
    chunk_by_sections,
    parse_frontmatter,
    doc_hash,
)


class TestParseFrontmatter:
    def test_extracts_simple_fields(self):
        text = """---
runbook_id: RB-001
title: "High API Latency"
version: "1.2"
---

# Body content here"""
        meta, body = parse_frontmatter(text)
        assert meta["runbook_id"] == "RB-001"
        assert meta["title"] == "High API Latency"
        assert "Body content here" in body

    def test_no_frontmatter_returns_empty_dict(self):
        text = "# Just a plain document\n\nNo frontmatter here."
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert "Just a plain document" in body

    def test_parses_list_fields(self):
        text = """---
anomaly_types: ["LATENCY_SPIKE", "ERROR_RATE_SPIKE"]
services: ["api-gateway", "payment-service"]
---
Body"""
        meta, body = parse_frontmatter(text)
        assert isinstance(meta["anomaly_types"], list)
        assert "LATENCY_SPIKE" in meta["anomaly_types"]

    def test_empty_frontmatter(self):
        text = "---\n---\nBody"
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == "Body"

    def test_unclosed_frontmatter_returns_empty(self):
        text = "---\ntitle: test\nBody without closing"
        meta, body = parse_frontmatter(text)
        assert meta == {}


class TestChunkBySections:
    def test_splits_on_h2_headers(self):
        text = """## Section One
Content for section one.

## Section Two
Content for section two.

## Section Three
Content for section three."""
        chunks = chunk_by_sections(text)
        assert len(chunks) == 3
        assert "Section One" in chunks[0]
        assert "Section Two" in chunks[1]

    def test_splits_on_h3_headers(self):
        text = """## Overview
Overview content.

### Step 1: Investigation
Investigation steps here.

### Step 2: Remediation
Remediation steps here."""
        chunks = chunk_by_sections(text)
        assert len(chunks) >= 2

    def test_long_section_further_chunked(self):
        # 500 paragraphs × ~30 chars → well above max_chunk_tokens=100
        para = "Word " * 30 + "\n\n"
        text = "## Section\n" + para * 500
        chunks = chunk_by_sections(text, max_chunk_tokens=100)
        # Should be split into multiple chunks due to paragraph splitting
        assert len(chunks) >= 2

    def test_short_doc_returns_one_chunk(self):
        text = "## Section\nShort content."
        chunks = chunk_by_sections(text)
        assert len(chunks) == 1

    def test_empty_doc_fallback(self):
        text = ""
        chunks = chunk_by_sections(text)
        assert len(chunks) >= 1  # fallback returns at least one empty chunk

    def test_chunks_are_strings(self):
        text = "## A\nContent A.\n\n## B\nContent B."
        chunks = chunk_by_sections(text)
        for c in chunks:
            assert isinstance(c, str)

    def test_no_empty_chunks(self):
        text = "## Section One\nContent.\n\n## Section Two\nMore content."
        chunks = chunk_by_sections(text)
        for c in chunks:
            assert c.strip() != ""


class TestDocHash:
    def test_same_content_same_hash(self):
        assert doc_hash("hello world") == doc_hash("hello world")

    def test_different_content_different_hash(self):
        assert doc_hash("hello") != doc_hash("world")

    def test_hash_length(self):
        h = doc_hash("test content")
        assert len(h) == 16

    def test_hash_is_hex(self):
        h = doc_hash("test")
        int(h, 16)  # should not raise

    def test_empty_string_hash(self):
        h = doc_hash("")
        assert len(h) == 16
