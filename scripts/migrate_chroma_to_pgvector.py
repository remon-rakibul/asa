"""One-off backfill: move legacy Chroma RAG chunks into pgvector.

The RAG backend switched from Chroma (per-process files under ./chroma_store)
to pgvector (shared `rag_chunks` table). Documents uploaded BEFORE the switch
still have their chunks only in Chroma, so every pgvector search returns
nothing for them. This script re-ingests each registered document's text from
its Chroma chunks into pgvector (re-chunk + re-embed via the normal
ingest_document path), then updates the registry chunk_count.

Usage:  .venv/bin/python -m scripts.migrate_chroma_to_pgvector
Idempotent: re-ingesting a document_id replaces its pgvector chunks.
"""

from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("migrate_chroma_to_pgvector")


async def main() -> None:
    from tools.database import get_pool, list_hospitals
    from tools.rag import _chroma_store, ingest_document

    pool = await get_pool()
    migrated = 0
    for hospital in await list_hospitals():
        hid = hospital["id"]
        async with pool.acquire() as conn:
            docs = await conn.fetch(
                "SELECT id, filename FROM hospital_documents WHERE hospital_id = $1", hid
            )
        if not docs:
            continue
        try:
            store = _chroma_store(hid)
            data = store.get()  # {"ids": [...], "documents": [...], "metadatas": [...]}
        except Exception as exc:
            log.warning("hospital %s: no readable Chroma store (%s) — skipping", hid, exc)
            continue

        # Group chunk texts by the owning document (ids are doc-{id}-chunk-{i}).
        by_doc: dict[int, list[tuple[int, str]]] = {}
        for chunk_id, text in zip(data.get("ids") or [], data.get("documents") or []):
            try:
                _, doc_id, _, idx = chunk_id.split("-")
                by_doc.setdefault(int(doc_id), []).append((int(idx), text))
            except ValueError:
                log.warning("skipping unrecognized chunk id %r", chunk_id)

        for doc in docs:
            async with pool.acquire() as conn:
                existing = await conn.fetchval(
                    "SELECT count(*) FROM rag_chunks WHERE hospital_id=$1 AND document_id=$2",
                    hid, doc["id"],
                )
            if existing:
                log.info("doc %s (%s): already in pgvector (%s chunks) — skipping",
                         doc["id"], doc["filename"], existing)
                continue
            chunks = sorted(by_doc.get(doc["id"], []))
            if not chunks:
                log.warning("doc %s (%s): registered but has NO Chroma chunks — "
                            "re-upload it via the admin UI", doc["id"], doc["filename"])
                continue
            text = "\n\n".join(t for _, t in chunks)
            n = await ingest_document(
                hospital_id=hid, document_id=doc["id"],
                filename=doc["filename"], text=text,
            )
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE hospital_documents SET chunk_count=$1 WHERE id=$2", n, doc["id"]
                )
            log.info("doc %s (%s): migrated %s chunks to pgvector", doc["id"], doc["filename"], n)
            migrated += 1
    log.info("done — %s document(s) migrated", migrated)


if __name__ == "__main__":
    asyncio.run(main())
