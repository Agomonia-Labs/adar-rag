# services/vectordb.py — pgvector only, user-scoped
from __future__ import annotations
import os
from database.connection import get_pool

EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))
TOP_K         = int(os.getenv("TOP_K", "6"))


def _vec(v: list[float]) -> str:
    return "[" + ",".join(map(str, v)) + "]"


async def store_chunk(
    *,
    document_id:  str,
    user_id:      str,
    chunk_index:  int,
    chunk_total:  int,
    content:      str,
    embedding:    list[float],
    chunk_metadata: dict,
) -> None:
    import json
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO document_chunks
              (document_id, user_id, chunk_index, chunk_total, content, embedding, chunk_metadata)
            VALUES ($1,$2,$3,$4,$5,$6::vector,$7::jsonb)
            """,
            document_id, user_id, chunk_index, chunk_total,
            content, _vec(embedding), json.dumps(chunk_metadata),
        )


async def delete_document_vectors(document_id: str) -> int:
    async with get_pool().acquire() as conn:
        r = await conn.execute(
            "DELETE FROM document_chunks WHERE document_id = $1", document_id
        )
        return int(r.split()[-1])


async def find_similar(
    query_embedding: list[float],
    user_id:         str,
    document_ids:    list[str],   # only search within these docs
    limit:           int = TOP_K,
) -> list[dict]:
    """
    Cosine similarity search scoped to a specific user and document set.
    Users can ONLY search their own embedded documents.
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                dc.document_id,
                d.original_name          AS doc_name,
                dc.chunk_index,
                dc.chunk_total,
                dc.content,
                dc.chunk_metadata,
                1 - (dc.embedding <=> $1::vector) AS similarity
            FROM  document_chunks dc
            JOIN  documents d ON d.id = dc.document_id
            WHERE dc.user_id     = $2
              AND dc.document_id = ANY($3::uuid[])
            ORDER BY dc.embedding <=> $1::vector
            LIMIT $4
            """,
            _vec(query_embedding),
            user_id,
            document_ids,
            limit,
        )
        return [dict(r) for r in rows]
