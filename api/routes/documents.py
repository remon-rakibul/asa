"""Hospital document management — RAG ingestion endpoints.

Hospital admins and platform admins can upload documents (txt, md, pdf) that
the booking agent will use to answer patient questions about the hospital.
Documents are chunked, embedded with a local Ollama model, and stored in
PGVector. A `hospital_documents` registry row tracks chunk counts so deletions
are clean.

Endpoints:
  POST   /hospitals/{hospital_id}/documents         — upload & ingest
  GET    /hospitals/{hospital_id}/documents         — list documents
  DELETE /hospitals/{hospital_id}/documents/{doc_id} — delete doc + chunks
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from api.deps import current_user, require_role
from api.schemas import DocumentOut
from config import settings
from tools.database import (
    create_hospital_document,
    delete_hospital_document,
    get_hospital_document,
    list_hospital_documents,
)
from tools.rag import delete_document_chunks_for_hospital, extract_text, ingest_document

log = logging.getLogger(__name__)

router = APIRouter(tags=["documents"])

_ALLOWED_TYPES = {
    "text/plain",
    "text/markdown",
    "application/pdf",
    "application/octet-stream",  # browsers sometimes send this for .md/.txt
}
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def _check_hospital_admin(user: dict, hospital_id: int) -> None:
    """Allow platform_admin (any hospital) or hospital_admin scoped to this hospital."""
    role = user.get("role", "")
    if role == "platform_admin":
        return
    if role == "hospital_admin" and user.get("hospital_id") == hospital_id:
        return
    raise HTTPException(
        status_code=403,
        detail="Only hospital admins or platform admins can manage documents.",
    )


@router.post(
    "/hospitals/{hospital_id}/documents",
    response_model=DocumentOut,
    status_code=201,
)
async def upload_document(
    hospital_id: int,
    file: UploadFile,
    user: dict = Depends(current_user),
) -> dict:
    """Upload and ingest a document into this hospital's RAG knowledge base.

    Supported formats: .txt, .md, .pdf (max 10 MB).
    """
    _check_hospital_admin(user, hospital_id)

    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB).")
    if not raw.strip():
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    filename = file.filename or "document.txt"
    content_type = file.content_type or "text/plain"

    if content_type not in _ALLOWED_TYPES:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ("txt", "md", "pdf"):
            raise HTTPException(
                status_code=415,
                detail="Unsupported file type. Upload .txt, .md, or .pdf.",
            )

    text = extract_text(filename, raw, content_type)
    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail="Could not extract any text from the uploaded file.",
        )

    # Create registry row first (we need the doc id for chunk IDs).
    doc_row = await create_hospital_document(
        hospital_id=hospital_id,
        filename=filename,
        content_type=content_type,
        chunk_count=0,
    )
    doc_id = doc_row["id"]

    try:
        chunk_count = await ingest_document(
            hospital_id=hospital_id,
            document_id=doc_id,
            filename=filename,
            text=text,
        )
    except Exception as exc:
        # Roll back the registry row if embedding failed.
        await delete_hospital_document(doc_id)
        log.exception("RAG ingestion failed for hospital %s doc %s", hospital_id, doc_id)
        raise HTTPException(
            status_code=502,
            detail=f"Embedding failed — is Ollama running with the '{settings.embedding_model}' model pulled? "
                   f"Run: ollama pull {settings.embedding_model}. Error: {exc}",
        ) from exc

    # Update chunk_count in the registry.
    from tools.database import get_pool
    async with (await get_pool()).acquire() as conn:
        await conn.execute(
            "UPDATE hospital_documents SET chunk_count = $1 WHERE id = $2",
            chunk_count, doc_id,
        )

    doc_row["chunk_count"] = chunk_count
    return doc_row


@router.get(
    "/hospitals/{hospital_id}/documents",
    response_model=list[DocumentOut],
)
async def list_documents(
    hospital_id: int,
    user: dict = Depends(current_user),
) -> list[dict]:
    _check_hospital_admin(user, hospital_id)
    return await list_hospital_documents(hospital_id)


@router.delete(
    "/hospitals/{hospital_id}/documents/{doc_id}",
    status_code=204,
)
async def delete_document(
    hospital_id: int,
    doc_id: int,
    user: dict = Depends(current_user),
) -> JSONResponse:
    _check_hospital_admin(user, hospital_id)

    doc = await get_hospital_document(doc_id)
    if not doc or doc["hospital_id"] != hospital_id:
        raise HTTPException(status_code=404, detail="Document not found.")

    # Remove vector chunks first (uses deterministic IDs).
    try:
        await delete_document_chunks_for_hospital(
            hospital_id=hospital_id,
            document_id=doc_id,
            chunk_count=doc["chunk_count"],
        )
    except Exception:
        log.exception("Failed to delete vector chunks for doc %s", doc_id)
        # Continue — delete the registry row so the admin can retry.

    await delete_hospital_document(doc_id)
    return JSONResponse(status_code=204, content=None)
