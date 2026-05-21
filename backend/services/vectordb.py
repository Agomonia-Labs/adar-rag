# services/vectordb.py — Hybrid search: pgvector cosine + PostgreSQL FTS + RRF
from __future__ import annotations
import os, logging
from database.connection import get_pool

log = logging.getLogger("docintel.vectordb")

EMBEDDING_DIM  = int(os.getenv("EMBEDDING_DIM",       "1536"))
TOP_K          = int(os.getenv("TOP_K",                "6"))
HYBRID_K       = int(os.getenv("HYBRID_CANDIDATE_K",   "50"))  # candidates per source
RRF_K          = int(os.getenv("RRF_K",                "60"))   # RRF constant (standard=60)
HYBRID_SEARCH  = os.getenv("HYBRID_SEARCH",  "true").lower() != "false"
RERANK_FETCH_K = int(os.getenv("RERANK_FETCH_K", "20"))         # candidates to fetch before re-ranking


def _vec(v: list[float]) -> str:
    return "[" + ",".join(map(str, v)) + "]"


# ── Store chunk + FTS vector ───────────────────────────────────────────────────

async def store_chunk(
    *,
    document_id:    str,
    user_id:        str,
    chunk_index:    int,
    chunk_total:    int,
    content:        str,
    embedding:      list[float],
    chunk_metadata: dict,
) -> None:
    import json
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO document_chunks
              (document_id, user_id, chunk_index, chunk_total,
               content, embedding, chunk_metadata, search_vector)
            VALUES ($1,$2,$3,$4,$5,$6::vector,$7::jsonb,
                    to_tsvector('english', $5))
            """,
            document_id, user_id, chunk_index, chunk_total,
            content, _vec(embedding), json.dumps(chunk_metadata),
        )


# ── Delete ─────────────────────────────────────────────────────────────────────

async def delete_document_vectors(document_id: str) -> int:
    async with get_pool().acquire() as conn:
        r = await conn.execute(
            "DELETE FROM document_chunks WHERE document_id = $1", document_id
        )
        return int(r.split()[-1])


# ── Main search entry point ────────────────────────────────────────────────────

async def find_similar(
    query_embedding: list[float],
    user_id:         str,
    document_ids:    list[str],
    limit:           int = TOP_K,
    query_text:      str = "",
) -> list[dict]:
    """
    Hybrid search combining pgvector cosine similarity and PostgreSQL BM25-like
    full-text search, fused with Reciprocal Rank Fusion (RRF).

    Falls back to pure vector search when:
    - HYBRID_SEARCH=false
    - query_text is empty
    - FTS returns 0 results (stopwords-only query)
    """
    if not HYBRID_SEARCH or not query_text.strip():
        return await _vector_only(query_embedding, user_id, document_ids, limit)

    return await _hybrid_rrf(query_embedding, query_text, user_id, document_ids, limit)


# ── Vector-only search ────────────────────────────────────────────────────────

async def _vector_only(
    query_embedding: list[float],
    user_id:         str,
    document_ids:    list[str],
    limit:           int,
) -> list[dict]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                dc.document_id,
                d.original_name                          AS doc_name,
                dc.chunk_index,
                dc.chunk_total,
                dc.content,
                dc.chunk_metadata,
                1 - (dc.embedding <=> $1::vector)        AS similarity,
                'vector'                                 AS match_type
            FROM  document_chunks dc
            JOIN  documents d ON d.id = dc.document_id
            WHERE dc.user_id     = $2
              AND dc.document_id = ANY($3::uuid[])
              AND dc.embedding   IS NOT NULL
            ORDER BY dc.embedding <=> $1::vector
            LIMIT $4
            """,
            _vec(query_embedding), user_id, document_ids, limit,
        )
        return [dict(r) for r in rows]


# ── Hybrid RRF search ─────────────────────────────────────────────────────────

async def _hybrid_rrf(
    query_embedding: list[float],
    query_text:      str,
    user_id:         str,
    document_ids:    list[str],
    limit:           int,
) -> list[dict]:
    """
    Reciprocal Rank Fusion over vector + full-text search.

    RRF score = 1/(k + vector_rank) + 1/(k + fts_rank)

    Chunks found by both methods score highest.
    Chunks found by only one method still contribute.
    """
    # Sanitize query for PostgreSQL FTS
    fts_query = " & ".join(
        w for w in query_text[:200].split() if len(w) > 1
    ) or query_text[:200]

    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            WITH
            vector_ranked AS (
                SELECT
                    dc.id                                    AS chunk_id,
                    dc.document_id,
                    dc.chunk_index,
                    dc.chunk_total,
                    dc.content,
                    dc.chunk_metadata,
                    1 - (dc.embedding <=> $1::vector)        AS vec_similarity,
                    ROW_NUMBER() OVER (
                        ORDER BY dc.embedding <=> $1::vector
                    )                                        AS vrank
                FROM document_chunks dc
                WHERE dc.user_id     = $2
                  AND dc.document_id = ANY($3::uuid[])
                  AND dc.embedding   IS NOT NULL
                LIMIT $5
            ),

            fts_ranked AS (
                SELECT
                    dc.id                                    AS chunk_id,
                    dc.document_id,
                    dc.chunk_index,
                    dc.chunk_total,
                    dc.content,
                    dc.chunk_metadata,
                    ts_rank_cd(dc.search_vector,
                               to_tsquery('english', $4))    AS fts_score,
                    ROW_NUMBER() OVER (
                        ORDER BY ts_rank_cd(dc.search_vector,
                                            to_tsquery('english', $4)) DESC
                    )                                        AS frank
                FROM document_chunks dc
                WHERE dc.user_id       = $2
                  AND dc.document_id   = ANY($3::uuid[])
                  AND dc.search_vector IS NOT NULL
                  AND dc.search_vector @@ to_tsquery('english', $4)
                LIMIT $5
            ),

            rrf AS (
                SELECT
                    COALESCE(v.chunk_id,    f.chunk_id)        AS chunk_id,
                    COALESCE(v.document_id, f.document_id)     AS document_id,
                    COALESCE(v.chunk_index, f.chunk_index)     AS chunk_index,
                    COALESCE(v.chunk_total, f.chunk_total)     AS chunk_total,
                    COALESCE(v.content,     f.content)         AS content,
                    COALESCE(v.chunk_metadata, f.chunk_metadata) AS chunk_metadata,
                    COALESCE(1.0 / ($6 + v.vrank), 0.0) +
                    COALESCE(1.0 / ($6 + f.frank), 0.0)        AS rrf_score,
                    CASE
                        WHEN v.chunk_id IS NOT NULL AND f.chunk_id IS NOT NULL THEN 'hybrid'
                        WHEN v.chunk_id IS NOT NULL THEN 'vector'
                        ELSE 'keyword'
                    END                                        AS match_type
                FROM      vector_ranked v
                FULL OUTER JOIN fts_ranked f ON v.chunk_id = f.chunk_id
            )

            SELECT
                r.document_id,
                d.original_name                              AS doc_name,
                r.chunk_index,
                r.chunk_total,
                r.content,
                r.chunk_metadata,
                LEAST(r.rrf_score / (2.0 / ($6 + 1)), 1.0)  AS similarity,
                r.match_type
            FROM  rrf r
            JOIN  documents d ON d.id = r.document_id
            ORDER BY r.rrf_score DESC
            LIMIT $7
            """,
            _vec(query_embedding),  # $1
            user_id,                # $2
            document_ids,           # $3
            fts_query,              # $4
            HYBRID_K,               # $5 candidates per source
            float(RRF_K),           # $6
            limit,                  # $7 final limit
        )

    results = [dict(r) for r in rows]

    if not results:
        log.info("FTS returned 0 results — falling back to vector-only")
        return await _vector_only(query_embedding, user_id, document_ids, limit)

    types = {}
    for r in results:
        t = r.get("match_type", "vector")
        types[t] = types.get(t, 0) + 1
    log.info(f"Hybrid search: {len(results)} results — {types}")
    return results