#!/usr/bin/env python3
"""
search.py — Búsqueda semántica en pgvector para Linux.

NO requiere sentence-transformers ni numpy.
Usa pgvector cosine similarity directamente en PostgreSQL.

Uso:
    from backend.rag.search import semantic_search

    results = semantic_search("¿qué cubre el seguro de salud?", category="salud")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("rag")

DB_DSN = os.getenv("DB_DSN", "postgresql://agente:agente_pw@localhost:5433/agente_ai")
KB_TOP_K = int(os.getenv("KB_TOP_K", "5"))
KB_SCORE_THRESHOLD = float(os.getenv("KB_SCORE_THRESHOLD", "0.55"))


def _get_db():
    import psycopg
    return psycopg.connect(DB_DSN)


def get_embedding_from_api(text: str) -> Optional[List[float]]:
    """
    Genera un embedding usando API externa (DeepSeek o Groq).
    Esto evita tener que instalar sentence-transformers en Linux.

    Requiere DEEPSEEK_API_KEY o GROQ_API_KEY en .env
    """
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("GROQ_API_KEY")
    if not api_key:
        logger.warning("No API key found for embeddings")
        return None

    # Intentar con DeepSeek primero
    try:
        import urllib.request
        import json as _json

        url = "https://api.deepseek.com/v1/embeddings"
        data = _json.dumps({
            "model": "deepseek-embedding",
            "input": text
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = _json.loads(resp.read())
            return result["data"][0]["embedding"]
    except Exception as e:
        logger.warning("DeepSeek embedding failed: %s", e)

    return None


def semantic_search(
    query: str,
    category: Optional[str] = None,
    top_k: int = KB_TOP_K,
    threshold: float = KB_SCORE_THRESHOLD,
) -> List[Dict[str, Any]]:
    """
    Búsqueda semántica usando pgvector cosine similarity.

    Estrategia:
    1. Si hay API key, genera embedding vía API externa y busca con pgvector
    2. Si no, usa búsqueda full-text con tsvector como fallback

    Args:
        query: Texto de búsqueda
        category: Filtrar por categoría (opcional)
        top_k: Número de resultados
        threshold: Umbral de similitud mínima

    Returns:
        Lista de chunks ordenados por relevancia
    """
    # Estrategia 1: Embedding vía API externa + pgvector
    embedding = get_embedding_from_api(query)

    if embedding:
        vec_str = "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"
        sql = """
            SELECT chunk_id, chunk_text, source_file, title, category,
                   chunk_order, tags, metadata,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM document_chunks
            WHERE 1 - (embedding <=> %s::vector) >= %s
        """
        params = [vec_str, vec_str, threshold]

        if category:
            sql += " AND category = %s"
            params.append(category)

        sql += " ORDER BY similarity DESC LIMIT %s"
        params.append(top_k)

        with _get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        results = []
        for row in rows:
            results.append({
                "chunk_id": row[0],
                "chunk_text": row[1],
                "source_file": row[2],
                "title": row[3],
                "category": row[4],
                "chunk_order": row[5],
                "tags": row[6],
                "metadata": row[7],
                "similarity": float(row[8]) if row[8] else 0,
            })
        return results

    # Estrategia 2: Fallback a búsqueda full-text
    logger.info("Using full-text search fallback for: %s", query[:50])
    words = query.lower().split()
    tsquery = " & ".join(f"{w}:*" for w in words if len(w) > 2)

    if not tsquery:
        return []

    sql = """
        SELECT chunk_id, chunk_text, source_file, title, category,
               chunk_order, tags, metadata,
               ts_rank(to_tsvector('spanish', chunk_text), to_tsquery('spanish', %s)) AS rank
        FROM document_chunks
        WHERE to_tsvector('spanish', chunk_text) @@ to_tsquery('spanish', %s)
    """
    params = [tsquery, tsquery]

    if category:
        sql += " AND category = %s"
        params.append(category)

    sql += " ORDER BY rank DESC LIMIT %s"
    params.append(top_k)

    with _get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    results = []
    for row in rows:
        results.append({
            "chunk_id": row[0],
            "chunk_text": row[1],
            "source_file": row[2],
            "title": row[3],
            "category": row[4],
            "chunk_order": row[5],
            "tags": row[6],
            "metadata": row[7],
            "similarity": float(row[8]) if row[8] else 0,
        })
    return results


def get_kb_stats() -> Dict[str, Any]:
    """Estadísticas de la base de conocimiento."""
    try:
        with _get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM document_chunks")
                total = cur.fetchone()[0]

                cur.execute("""
                    SELECT category, COUNT(*) as cnt
                    FROM document_chunks GROUP BY category ORDER BY cnt DESC
                """)
                by_category = {row[0]: row[1] for row in cur.fetchall()}

                cur.execute("SELECT COUNT(DISTINCT source_file) FROM document_chunks")
                sources = cur.fetchone()[0]

                return {
                    "total_chunks": total,
                    "total_sources": sources,
                    "by_category": by_category,
                    "vector_size": 384,
                    "status": "ok",
                }
    except Exception as e:
        return {"status": "error", "error": str(e), "total_chunks": 0}


def import_from_json(json_path: str) -> int:
    """
    Importa chunks desde un JSON exportado a PostgreSQL.
    Útil para Linux cuando se reciben datos desde Windows.
    """
    import psycopg

    with open(json_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    inserted = 0
    with _get_db() as conn:
        with conn.cursor() as cur:
            for chunk in chunks:
                cur.execute(
                    """
                    INSERT INTO document_chunks
                        (document_id, source_file, title, category, chunk_id, chunk_text,
                         chunk_order, tags, metadata, checksum, version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                    ON CONFLICT (chunk_id) DO NOTHING
                    """,
                    (
                        chunk["document_id"],
                        chunk["source_file"],
                        chunk.get("title"),
                        chunk["category"],
                        chunk["chunk_id"],
                        chunk["chunk_text"],
                        chunk["chunk_order"],
                        json.dumps(chunk.get("tags", [])),
                        json.dumps(chunk.get("metadata", {})),
                        chunk.get("checksum"),
                        chunk.get("version", 1),
                    ),
                )
                inserted += 1
        conn.commit()

    logger.info("Imported %d chunks from %s", inserted, json_path)
    return inserted


# ── CLI ──────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RAG search tools")
    parser.add_argument("--query", help="Search query")
    parser.add_argument("--category", help="Filter by category")
    parser.add_argument("--stats", action="store_true", help="Show KB stats")
    parser.add_argument("--import-json", help="Import from JSON file")
    args = parser.parse_args()

    if args.import_json:
        count = import_from_json(args.import_json)
        print(f"Imported {count} chunks")

    elif args.stats:
        stats = get_kb_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))

    elif args.query:
        results = semantic_search(args.query, category=args.category)
        print(json.dumps(results, ensure_ascii=False, indent=2)[:2000])

    else:
        parser.print_help()
