"""Tests for the RAG layer that don't require live Gemini calls.

We focus on the pure-Python pieces: chunker, doc-signature, and the numpy
backend's search semantics with a stubbed embedder.
"""

import numpy as np
import pytest

from app.agent import _doc_signature
from app.rag import (
    Doc,
    _chunk_text,
    _NumpyBackend,
    build_docs_from_emails,
    build_docs_from_firestore,
)


class StubEmbedder:
    """Maps each unique token to a one-hot dimension. Cosine similarity then
    reflects shared-token overlap, which is enough to verify search wiring."""

    def __init__(self):
        self.vocab: dict[str, int] = {}
        self.dim = 64

    def _vec(self, text: str) -> list[float]:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in text.lower().split():
            idx = self.vocab.setdefault(tok, len(self.vocab) % self.dim)
            v[idx] += 1.0
        return v.tolist()

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


# ---------- Chunker ----------

class TestChunkText:
    def test_short_text_returns_single_chunk(self):
        assert _chunk_text("hello world") == ["hello world"]

    def test_empty_text_returns_empty(self):
        assert _chunk_text("") == []
        assert _chunk_text(None) == []  # type: ignore[arg-type]

    def test_long_text_splits_with_overlap(self):
        text = "a" * 1000
        chunks = _chunk_text(text, size=300, overlap=50)
        assert len(chunks) >= 3
        assert all(len(c) <= 300 for c in chunks)

    def test_prefers_paragraph_boundaries(self):
        # Two paragraphs that together fit in `size`. Should NOT split.
        text = "First paragraph here.\n\nSecond paragraph here."
        chunks = _chunk_text(text, size=200, overlap=20)
        assert chunks == [text]

    def test_splits_on_paragraphs_when_long(self):
        para1 = "x" * 250
        para2 = "y" * 250
        text = f"{para1}\n\n{para2}"
        chunks = _chunk_text(text, size=300, overlap=20)
        # Each paragraph should land in its own chunk, not get sliced mid-paragraph.
        assert any("x" * 200 in c for c in chunks)
        assert any("y" * 200 in c for c in chunks)

    def test_splits_on_sentence_boundary(self):
        # Long text without paragraph breaks but with sentence punctuation.
        text = ". ".join([f"Sentence number {i:02d}" for i in range(40)])
        chunks = _chunk_text(text, size=120, overlap=20)
        # No chunk should start mid-word of "Sentence" — the splitter should
        # have used ". " as the boundary.
        for c in chunks:
            stripped = c.lstrip()
            assert not stripped.startswith("entence"), f"Mid-word split: {c[:30]!r}"


# ---------- Doc signature ----------

class TestDocSignature:
    def test_same_docs_same_sig(self):
        a = [Doc(id="x:1", source="s", title="t", text="hello")]
        b = [Doc(id="x:1", source="s", title="t", text="hello")]
        assert _doc_signature(a) == _doc_signature(b)

    def test_changed_text_changes_sig(self):
        a = [Doc(id="x:1", source="s", title="t", text="hello")]
        b = [Doc(id="x:1", source="s", title="t", text="hello world")]
        assert _doc_signature(a) != _doc_signature(b)

    def test_added_doc_changes_sig(self):
        a = [Doc(id="x:1", source="s", title="t", text="hello")]
        b = a + [Doc(id="x:2", source="s", title="t", text="bye")]
        assert _doc_signature(a) != _doc_signature(b)


# ---------- Numpy backend ----------

class TestNumpyBackend:
    def setup_method(self):
        self.embedder = StubEmbedder()
        self.backend = _NumpyBackend(self.embedder)
        self.docs = [
            Doc(id="p:1", source="project", title="Acme", text="acme website redesign in progress"),
            Doc(id="p:2", source="project", title="Northwind", text="northwind invoice automation"),
            Doc(id="i:1", source="invoice", title="Inv 99", text="acme invoice 5000 USD overdue"),
        ]
        self.backend.upsert(self.docs)

    def test_search_returns_relevant_doc(self):
        results = self.backend.search("acme website", top_k=2)
        assert len(results) >= 1
        # The Acme project should rank above Northwind.
        ids = [d.id for d, _ in results]
        assert "p:1" in ids

    def test_search_with_where_filter(self):
        results = self.backend.search("acme", top_k=5, where={"source": "invoice"})
        assert all(d.source == "invoice" for d, _ in results)

    def test_search_empty_index(self):
        empty = _NumpyBackend(self.embedder)
        assert empty.search("anything") == []

    def test_search_respects_top_k(self):
        results = self.backend.search("acme", top_k=1)
        assert len(results) == 1


# ---------- Doc builders ----------

class TestBuildDocs:
    def test_firestore_builder_emits_one_doc_per_short_record(self):
        projects = [{"id": "p1", "name": "Acme", "client": "Acme Co", "status": "active"}]
        invoices = [{"id": "i1", "client": "Acme", "amount": 100, "status": "paid"}]
        alerts = [{"id": "a1", "severity": "warn", "message": "late", "action": "ping"}]
        docs = build_docs_from_firestore(projects, invoices, alerts)
        assert len(docs) == 3
        sources = {d.source for d in docs}
        assert sources == {"project", "invoice", "alert"}

    def test_emails_builder_handles_empty(self):
        assert build_docs_from_emails([]) == []
        assert build_docs_from_emails(None) == []  # type: ignore[arg-type]

    def test_emails_builder_chunks_long_body(self):
        emails = [{
            "id": "e1",
            "subject": "Hello",
            "from": "Bob <b@example.com>",
            "fromAddress": "b@example.com",
            "date": "2026-01-01",
            "body": "x" * 3000,
        }]
        docs = build_docs_from_emails(emails)
        assert len(docs) > 1  # Long body should chunk
        assert all(d.source == "email" for d in docs)
