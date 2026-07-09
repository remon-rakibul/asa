"""RAG layer for per-hospital document knowledge bases.

Two interchangeable backends behind the same public API (RAG_BACKEND):

  * "pgvector" (default) — chunks + embeddings live in the `rag_chunks`
    Postgres table (migration 0020), shared by every API worker/pod.
    Tenant isolation via the hospital_id column.
  * "chroma" (legacy) — local file store `{CHROMA_DIR}/hospital_{id}/`,
    one collection per hospital. Per-process filesystem state; kept for one
    release as a rollback path.

Embeddings are local in both cases (Ollama nomic-embed-text).
"""
from __future__ import annotations

import io
import logging
import os
from pathlib import Path

from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import settings

log = logging.getLogger(__name__)

_CHUNK_SIZE = 800
_CHUNK_OVERLAP = 120
# Persisted next to the project root; overridable via CHROMA_DIR env var.
_CHROMA_DIR = Path(os.environ.get("CHROMA_DIR", "./chroma_store")).resolve()

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=_CHUNK_SIZE,
    chunk_overlap=_CHUNK_OVERLAP,
)


def _embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(
        model=settings.embedding_model,
        base_url=settings.ollama_base_url,
    )


# nomic-embed-text is an asymmetric retrieval model: it expects task prefixes
# ("search_document: " on stored chunks, "search_query: " on queries) and
# ranks noticeably worse without them (live-observed: the chunk containing a
# person's name ranked below generic body chunks for a "who is <name>?" query).
_DOC_PREFIX = "search_document: "
_QUERY_PREFIX = "search_query: "


def _use_pgvector() -> bool:
    return (settings.rag_backend or "pgvector").lower() != "chroma"


def _vec_literal(vec: list[float]) -> str:
    """Serialize an embedding as a pgvector text literal ('[1,2,...]')."""
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


def _chroma_store(hospital_id: int):
    """Return a persistent Chroma store for this hospital (legacy backend)."""
    from langchain_chroma import Chroma  # lazy: not needed for pgvector

    persist_dir = str(_CHROMA_DIR / f"hospital_{hospital_id}")
    return Chroma(
        collection_name=f"hospital_{hospital_id}",
        embedding_function=_embeddings(),
        persist_directory=persist_dir,
    )


def extract_text(filename: str, raw_bytes: bytes, content_type: str) -> str:
    """Extract plain text from uploaded bytes. Supports .txt, .md, .pdf."""
    if filename.lower().endswith(".pdf") or "pdf" in content_type:
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
            return "\n\n".join(
                page.extract_text() or "" for page in reader.pages
            ).strip()
        except Exception:
            pass
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return raw_bytes.decode("latin-1", errors="replace")


async def ingest_document(
    *,
    hospital_id: int,
    document_id: int,
    filename: str,
    text: str,
) -> int:
    """Chunk, embed and persist text. Returns the number of chunks created.

    IDs: ``doc-{document_id}-chunk-{i}`` — re-ingesting the same document_id
    replaces old chunks cleanly (upsert).
    """
    raw = [Document(page_content=text, metadata={"source": filename, "document_id": document_id})]
    chunks = _splitter.split_documents(raw)

    if not _use_pgvector():
        ids = [f"doc-{document_id}-chunk-{i}" for i in range(len(chunks))]
        store = _chroma_store(hospital_id)
        await store.aadd_documents(chunks, ids=ids)
        return len(chunks)

    from tools.database import get_pool

    vectors = await _embeddings().aembed_documents(
        [_DOC_PREFIX + c.page_content for c in chunks]
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Re-ingest replaces the document's chunks entirely (old chunk
            # count may differ from the new one).
            await conn.execute(
                "DELETE FROM rag_chunks WHERE hospital_id = $1 AND document_id = $2",
                hospital_id, document_id,
            )
            await conn.executemany(
                """
                INSERT INTO rag_chunks (id, hospital_id, document_id, content, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
                """,
                [
                    (
                        f"doc-{document_id}-chunk-{i}",
                        hospital_id,
                        document_id,
                        chunk.page_content,
                        _vec_literal(vec),
                    )
                    for i, (chunk, vec) in enumerate(zip(chunks, vectors))
                ],
            )
    return len(chunks)


async def delete_document_chunks(*, document_id: int, chunk_count: int) -> None:
    """Remove all vector chunks for a document."""
    if _use_pgvector():
        from tools.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM rag_chunks WHERE document_id = $1", document_id
            )
        return

    # Chroma legacy: we don't know the owning collection here, so scan all
    # hospital dirs (fast in practice: few hospitals, small collections).
    if chunk_count <= 0:
        return
    ids = [f"doc-{document_id}-chunk-{i}" for i in range(chunk_count)]
    if not _CHROMA_DIR.exists():
        return
    for hospital_dir in _CHROMA_DIR.iterdir():
        if not hospital_dir.is_dir():
            continue
        try:
            hospital_id = int(hospital_dir.name.replace("hospital_", ""))
        except ValueError:
            continue
        store = _chroma_store(hospital_id)
        try:
            await store.adelete(ids=ids)
        except Exception:
            pass


async def delete_document_chunks_for_hospital(
    *, hospital_id: int, document_id: int, chunk_count: int
) -> None:
    """Faster delete when hospital_id is known."""
    if _use_pgvector():
        from tools.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM rag_chunks WHERE hospital_id = $1 AND document_id = $2",
                hospital_id, document_id,
            )
        return

    if chunk_count <= 0:
        return
    ids = [f"doc-{document_id}-chunk-{i}" for i in range(chunk_count)]
    store = _chroma_store(hospital_id)
    await store.adelete(ids=ids)


async def search_docs(hospital_id: int, query: str, k: int = 4) -> list[str]:
    """Semantic search over this hospital's documents.

    Returns matching text chunks (empty list if no documents or no matches;
    also empty — with a warning — if the pgvector table is missing so the
    agent degrades to "no info" instead of crashing the turn).
    """
    if not _use_pgvector():
        store = _chroma_store(hospital_id)
        results = await store.asimilarity_search(query, k=k)
        return [doc.page_content for doc in results]

    from tools.database import get_pool

    vec = _vec_literal(await _embeddings().aembed_query(_QUERY_PREFIX + query))
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.content, d.filename
                FROM rag_chunks c
                LEFT JOIN hospital_documents d ON d.id = c.document_id
                WHERE c.hospital_id = $1
                ORDER BY c.embedding <=> $2::vector
                LIMIT $3
                """,
                hospital_id, vec, k,
            )
    except Exception as exc:
        log.warning("pgvector RAG search failed (%s) — returning no results", exc)
        return []
    # Source attribution: a chunk from the middle of a document often lacks
    # the subject's name/title, so the model can't connect e.g. a resume's
    # project list to the person asked about. Prefixing the owning document's
    # name gives it that association generically.
    return [
        (f"[উৎস: {r['filename']}] " if r["filename"] else "") + r["content"]
        for r in rows
    ]
