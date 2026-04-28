"""Per-tenant RAG over the user's Firestore data.

Production path: **Chroma Cloud** with one collection per tenant
(`tenant_{userId}`). Vectors persist between requests and are isolated by
collection — no cross-tenant leakage at the vector layer.

Embedding: Gemini `gemini-embedding-001` via `langchain_google_genai`.
Chunker: 600-char overlapping chunks (overlap 80) so long content (project
notes, invoice lines, eventual email bodies) can match on partial matches.
Metadata: every chunk carries `{source, source_id, title, client, chunk_idx}`
so the agent (and we) can filter by source type or by project/client later.

Fallback: if `CHROMA_API_KEY` isn't set, RagIndex transparently falls back
to in-memory numpy cosine — same input, same output shape, just slower and
non-persistent. Lets local dev work without a Chroma signup.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

from .settings import settings

log = logging.getLogger("sushmi.rag")


class _RestGeminiEmbedder:
    """Tiny Gemini embeddings client using the REST API (via httpx).
    Bypasses langchain-google-genai's gRPC stack which breaks inside
    FastAPI's AnyIO worker threads."""

    def __init__(self, model: str, api_key: str):
        # Strip 'models/' prefix; the REST URL adds it back.
        self.model = model[len("models/"):] if model.startswith("models/") else model
        self.api_key = api_key
        self._url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:embedContent"
        self._batch_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:batchEmbedContents"
        self._client = httpx.Client(timeout=30.0)

    def _one(self, text: str, task_type: str) -> list[float]:
        r = self._client.post(
            self._url,
            params={"key": self.api_key},
            json={"content": {"parts": [{"text": text}]}, "taskType": task_type},
        )
        r.raise_for_status()
        return r.json()["embedding"]["values"]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # Try batch first; fall back to per-text if the batch endpoint is fussy.
        if not texts:
            return []
        try:
            r = self._client.post(
                self._batch_url,
                params={"key": self.api_key},
                json={
                    "requests": [
                        {"model": f"models/{self.model}", "content": {"parts": [{"text": t}]}, "taskType": "RETRIEVAL_DOCUMENT"}
                        for t in texts
                    ]
                },
            )
            r.raise_for_status()
            return [e["values"] for e in r.json().get("embeddings", [])]
        except Exception:
            return [self._one(t, "RETRIEVAL_DOCUMENT") for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._one(text, "RETRIEVAL_QUERY")


def _ensure_thread_loop():
    """Chroma's gRPC client expects an event loop on the current thread.
    FastAPI's AnyIO worker threads don't have one. Install a loop on the
    current thread (idempotent) so all subsequent gRPC calls work."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


def _run_with_loop(fn):
    """Try fn(); on a 'no event loop' error from gRPC, install a loop and retry."""
    _ensure_thread_loop()
    try:
        return fn()
    except RuntimeError as e:
        msg = str(e)
        if "current event loop" not in msg and "no running event loop" not in msg:
            raise
        # The gRPC call internally requested a loop on a thread that has none.
        # Force-install one on THIS thread and retry.
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return fn()


# --- Document / chunk model ------------------------------------------------

@dataclass
class Doc:
    id: str
    source: str               # "project" | "invoice" | "alert" | "email"
    title: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


_RECURSIVE_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


def _chunk_text(text: str, size: int = 600, overlap: int = 80) -> list[str]:
    """Recursive semantic chunker.

    Tries to split on paragraph boundaries first (`\\n\\n`), then line breaks,
    then sentence-ish boundaries (`. `), then words, falling back to raw
    character splits only when nothing else fits. This keeps semantic units
    intact — a sentence isn't chopped in half mid-word, and a paragraph isn't
    split unless it's longer than `size` on its own.

    Mirrors LangChain's `RecursiveCharacterTextSplitter` behaviour without
    pulling in the dependency. Same `size` / `overlap` parameter shape so all
    existing callers and tests continue to work."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks = _recursive_split(text, size, _RECURSIVE_SEPARATORS)
    return _merge_with_overlap(chunks, size, overlap)


def _recursive_split(text: str, size: int, separators: list[str]) -> list[str]:
    """Split on the first separator that actually appears in `text`.
    Pieces longer than `size` get re-split using the next separator down."""
    if len(text) <= size:
        return [text]
    sep = next((s for s in separators if s and s in text), "")
    if not sep:
        # Last resort: hard character split.
        return [text[i : i + size] for i in range(0, len(text), size)]
    pieces = text.split(sep)
    out: list[str] = []
    remaining = separators[separators.index(sep) + 1 :] if sep in separators else [""]
    for i, piece in enumerate(pieces):
        # Re-attach the separator on rejoining so we don't lose punctuation.
        chunk = piece + (sep if i < len(pieces) - 1 else "")
        if len(chunk) <= size:
            out.append(chunk)
        else:
            out.extend(_recursive_split(chunk, size, remaining))
    return [c for c in out if c.strip()]


def _merge_with_overlap(pieces: list[str], size: int, overlap: int) -> list[str]:
    """Greedy: pack pieces together so each chunk stays at or below `size`,
    carrying `overlap` chars from the end of one chunk into the start of the
    next so context isn't lost across boundaries.

    A piece that is itself longer than `size` (already char-split upstream)
    is emitted as its own chunk untouched — adding overlap would push it
    over the budget."""
    if not pieces:
        return []
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        # Oversize piece — flush whatever's buffered and emit alone.
        if len(piece) >= size:
            if buf.strip():
                chunks.append(buf.strip())
                buf = ""
            chunks.append(piece.strip())
            continue
        if not buf:
            buf = piece
            continue
        # Reserve room for the overlap tail when chaining.
        if len(buf) + len(piece) <= size:
            buf += piece
        else:
            chunks.append(buf.strip())
            tail = buf[-overlap:] if overlap > 0 and len(buf) > overlap else ""
            # If even the overlap+piece would overflow, drop the overlap.
            if len(tail) + len(piece) > size:
                tail = ""
            buf = (tail + piece) if tail else piece
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


# --- Backends --------------------------------------------------------------

class _ChromaBackend:
    """Chroma Cloud collection per tenant. Persistent. The collection name
    `tenant_{userId}` is the primary multi-tenant boundary at the vector layer."""

    def __init__(self, user_id: str, embedder: _RestGeminiEmbedder):
        import os
        os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "noop")
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        self.user_id = user_id
        self.embedder = embedder
        # Use HttpClient (REST) instead of CloudClient (gRPC). The gRPC path
        # breaks inside FastAPI's AnyIO worker threads which don't have an
        # event loop — REST has no such problem.
        self.client = chromadb.HttpClient(
            host="api.trychroma.com",
            ssl=True,
            tenant=settings.CHROMA_TENANT,
            database=settings.CHROMA_DATABASE,
            headers={"x-chroma-token": settings.CHROMA_API_KEY},
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        # Sanitise — Chroma collection names must be 3-63 chars, alnum/underscore/hyphen.
        safe = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in user_id)[:50]
        self.collection_name = f"tenant_{safe}"
        self.collection = self.client.get_or_create_collection(name=self.collection_name)

    def upsert(self, docs: list[Doc]) -> None:
        if not docs:
            return
        texts = [d.text for d in docs]
        # Embed in one batch (Gemini quota friendlier).
        embeddings = self.embedder.embed_documents(texts)
        ids = [d.id for d in docs]
        metadatas = [
            {
                "source": d.source,
                "title": (d.title or "")[:200],
                "source_id": d.metadata.get("source_id", d.id.split(":", 1)[-1]),
                "client": d.metadata.get("client", ""),
                "chunk_idx": d.metadata.get("chunk_idx", 0),
            }
            for d in docs
        ]
        # `upsert` keeps the same id stable across re-syncs — no duplicates.
        self.collection.upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)

    def search(self, query: str, top_k: int = 4, where: dict | None = None) -> list[tuple[Doc, float]]:
        embed = self.embedder.embed_query(query)
        res = self.collection.query(
            query_embeddings=[embed],
            n_results=top_k,
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        out: list[tuple[Doc, float]] = []
        ids = (res.get("ids") or [[]])[0]
        documents = (res.get("documents") or [[]])[0]
        metadatas = (res.get("metadatas") or [[]])[0]
        distances = (res.get("distances") or [[]])[0]
        for i, doc_id in enumerate(ids):
            md = metadatas[i] if i < len(metadatas) else {}
            text = documents[i] if i < len(documents) else ""
            # Chroma returns L2 distance; convert to a similarity-ish score in [0,1].
            dist = float(distances[i]) if i < len(distances) else 0.0
            score = 1.0 / (1.0 + dist)
            out.append((
                Doc(id=doc_id, source=md.get("source", ""), title=md.get("title", ""), text=text, metadata=dict(md)),
                score,
            ))
        return out


class _NumpyBackend:
    """In-memory cosine, used when Chroma keys aren't configured. Same shape as Chroma."""

    def __init__(self, embedder: _RestGeminiEmbedder):
        self.embedder = embedder
        self._docs: list[Doc] = []
        self._vectors: np.ndarray | None = None

    def upsert(self, docs: list[Doc]) -> None:
        self._docs = list(docs)
        if not self._docs:
            self._vectors = None
            return
        arr = np.array(self.embedder.embed_documents([d.text for d in self._docs]), dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1
        self._vectors = arr / norms

    def search(self, query: str, top_k: int = 4, where: dict | None = None) -> list[tuple[Doc, float]]:
        if not self._docs or self._vectors is None:
            return []
        # Optional metadata filter to mirror Chroma's `where` semantics for `$eq`.
        candidates: list[tuple[int, Doc]] = list(enumerate(self._docs))
        if where:
            def _match(d: Doc) -> bool:
                for k, v in where.items():
                    expected = v.get("$eq") if isinstance(v, dict) else v
                    if d.metadata.get(k) != expected:
                        return False
                return True
            candidates = [(i, d) for i, d in candidates if _match(d)]
        if not candidates:
            return []
        idxs = np.array([i for i, _ in candidates])
        sub = self._vectors[idxs]
        q = np.array(self.embedder.embed_query(query), dtype=np.float32)
        n = np.linalg.norm(q)
        if n:
            q = q / n
        scores = sub @ q
        top = np.argsort(-scores)[:top_k]
        return [(candidates[int(i)][1], float(scores[int(i)])) for i in top]


# --- Public RagIndex --------------------------------------------------------

class RagIndex:
    """Per-request RAG facade. Picks Chroma Cloud if configured, otherwise
    in-memory numpy. Always upserts the user's current Firestore docs on
    construction so the agent's first `search_knowledge` call sees fresh data."""

    def __init__(self, user_id: str, docs: list[Doc]):
        self.user_id = user_id
        self.docs = docs
        self._embedder: _RestGeminiEmbedder | None = None
        self.backend = self._pick_backend()
        self._sync()

    def _ensure_embedder(self) -> _RestGeminiEmbedder:
        if self._embedder is None:
            if not settings.GEMINI_API_KEY:
                raise RuntimeError("GEMINI_API_KEY not configured")
            self._embedder = _RestGeminiEmbedder(
                model=settings.GEMINI_EMBED_MODEL,
                api_key=settings.GEMINI_API_KEY,
            )
        return self._embedder

    def _pick_backend(self):
        # Backend selection rules:
        #   - Default: in-memory numpy. Fast, dependency-light, plays nicely
        #     with FastAPI's worker-thread model.
        #   - Opt-in: Chroma Cloud, when both Chroma keys AND
        #     RAG_USE_CHROMA=1 are set. The Chroma client's gRPC stack
        #     conflicts with FastAPI worker threads on Render's free tier;
        #     we keep the integration wired (verified locally) but don't
        #     enable it in production hosting until that's resolved upstream.
        import os
        use_chroma = (
            os.getenv("RAG_USE_CHROMA") == "1"
            and settings.CHROMA_API_KEY
            and settings.CHROMA_TENANT
        )
        if use_chroma:
            try:
                return _ChromaBackend(self.user_id, self._ensure_embedder())
            except Exception as e:  # noqa: BLE001
                log.warning("Chroma init failed (%s) — falling back to numpy", e)
        return _NumpyBackend(self._ensure_embedder())

    def _sync(self) -> None:
        if not self.docs:
            return
        try:
            self.backend.upsert(self.docs)
        except Exception as e:  # noqa: BLE001
            log.warning("RAG upsert failed: %s", e)

    def search(self, query: str, top_k: int = 4, where: dict | None = None) -> list[tuple[Doc, float]]:
        if not query:
            return []
        try:
            return self.backend.search(query, top_k=top_k, where=where)
        except Exception as e:  # noqa: BLE001
            log.warning("RAG search failed: %s", e)
            return []


# --- Convert Firestore data into chunked docs ------------------------------

def build_docs_from_firestore(projects: list[dict], invoices: list[dict], alerts: list[dict]) -> list[Doc]:
    """Materialise Firestore data into chunked Doc objects with metadata."""
    docs: list[Doc] = []

    for p in projects:
        title = f"Project: {p.get('name', '')} ({p.get('client', '')})"
        body = (
            f"{title}\n"
            f"Status: {p.get('status')}. Health: {p.get('health')}%. "
            f"Commits: {p.get('commits', 0)}. Days left: {p.get('daysLeft')}. "
            f"Budget: {p.get('budget')}. Spent: {p.get('spent')}. "
            f"Repo: {p.get('repo') or 'n/a'}.\n"
            f"{p.get('description') or ''}"
        )
        for i, chunk in enumerate(_chunk_text(body)):
            docs.append(Doc(
                id=f"project:{p.get('id')}:{i}",
                source="project",
                title=title,
                text=chunk,
                metadata={"source_id": str(p.get("id") or ""), "client": p.get("client") or "", "chunk_idx": i},
            ))

    for inv in invoices:
        title = f"Invoice {inv.get('id', '')} — {inv.get('client', '')}"
        body = (
            f"{title}\n"
            f"Amount: {inv.get('amount')}. Status: {inv.get('status')}. "
            f"Issued: {inv.get('issuedDate')}. Due: {inv.get('dueDate')}.\n"
            f"{inv.get('notes') or ''}"
        )
        for i, chunk in enumerate(_chunk_text(body)):
            docs.append(Doc(
                id=f"invoice:{inv.get('id')}:{i}",
                source="invoice",
                title=title,
                text=chunk,
                metadata={"source_id": str(inv.get("id") or ""), "client": inv.get("client") or "", "chunk_idx": i},
            ))

    for a in alerts:
        body = f"Alert ({a.get('severity')}): {a.get('message')}. Suggested action: {a.get('action')}."
        for i, chunk in enumerate(_chunk_text(body)):
            docs.append(Doc(
                id=f"alert:{a.get('id')}:{i}",
                source="alert",
                title="Alert",
                text=chunk,
                metadata={"source_id": str(a.get("id") or ""), "client": "", "chunk_idx": i},
            ))

    return docs


def build_docs_from_emails(emails: list[dict]) -> list[Doc]:
    """Materialise indexed email bodies into chunked Doc objects.
    Each chunk carries the original Gmail UID + sender so the agent can cite back."""
    docs: list[Doc] = []
    # Emails get a slightly larger chunk size since they have more prose per topic.
    for e in emails or []:
        title = f"Email: {e.get('subject', '(no subject)')} — {e.get('from', '')}"
        body = (
            f"From: {e.get('from','')} <{e.get('fromAddress','')}>\n"
            f"Subject: {e.get('subject','')}\n"
            f"Date: {e.get('date','')}\n\n"
            f"{e.get('body','')}"
        )
        for i, chunk in enumerate(_chunk_text(body, size=800, overlap=100)):
            docs.append(Doc(
                id=f"email:{e.get('id') or e.get('uid') or i}:{i}",
                source="email",
                title=title,
                text=chunk,
                metadata={
                    "source_id": str(e.get("id") or e.get("uid") or ""),
                    "client": "",
                    "chunk_idx": i,
                    "subject": (e.get("subject") or "")[:200],
                    "from": (e.get("from") or "")[:120],
                    "date": e.get("date") or "",
                },
            ))
    return docs


def docs_to_json_snippets(docs: list[Doc]) -> str:
    """For including in an LLM prompt if needed (debug helper)."""
    return json.dumps([{"id": d.id, "title": d.title, "text": d.text} for d in docs], indent=2)
