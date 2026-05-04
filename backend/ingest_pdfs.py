#!/usr/bin/env python3
"""
Ingesta de PDFs/TXTs → kb_documents en Postgres (pgvector).

Uso:
    python ingest_pdfs.py --data_dir ..\\data
    python ingest_pdfs.py --data_dir ..\\data --db_dsn postgresql://user:pw@host/db
    python ingest_pdfs.py --data_dir ..\\data --dry_run

Requisitos:
    pip install pypdf psycopg[binary] sentence-transformers python-dotenv pdfplumber
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [ingest] %(message)s",
)
logger = logging.getLogger("ingest")

# ── Config por defecto ───────────────────────────────
DEFAULT_DB_DSN = os.getenv("DB_DSN", "postgresql://agente:agente_pw@localhost:5433/agente_ai")
DEFAULT_DATA_DIR = "./data"
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "750"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "100"))
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# ── Mapeo nombre-de-archivo → category ───────────────
CATEGORY_KEYWORDS: List[Tuple[str, str]] = [
    ("salud", "salud"),
    ("health", "salud"),
    ("vida", "vida"),
    ("life", "vida"),
    ("hogar", "hogar"),
    ("home", "hogar"),
    ("auto", "auto"),
    ("coche", "auto"),
    ("vehiculo", "auto"),
    ("deceso", "decesos"),
    ("viaje", "viajes"),
    ("travel", "viajes"),
    ("mascota", "mascotas"),
    ("pet", "mascotas"),
]

# ── Marcas a sanitizar en el texto ingestado ─────────
BRAND_PATTERNS = [
    (re.compile(r"\bASISA\b", re.I), "la aseguradora"),
    (re.compile(r"\bMapfre\b", re.I), "la compañía"),
    (re.compile(r"\bSanitas\b", re.I), "la aseguradora"),
    (re.compile(r"\bAdeslas\b", re.I), "la compañía"),
    (re.compile(r"\bDKV\b", re.I), "la aseguradora"),
    (re.compile(r"\bAXA\b", re.I), "la compañía"),
    (re.compile(r"\bCigna\b", re.I), "la aseguradora"),
    (re.compile(r"\bCaser\b", re.I), "la compañía"),
    (re.compile(r"\bMutua Madrileña\b", re.I), "la aseguradora"),
    (re.compile(r"\bMutua\b", re.I), "la aseguradora"),
    (re.compile(r"\bAllianz\b", re.I), "la compañía"),
    (re.compile(r"\bZurich\b", re.I), "la aseguradora"),
    (re.compile(r"\bGenerali\b", re.I), "la compañía"),
    # Frases "según MARCA" → "según la póliza"
    (re.compile(r"según\s+(?:la\s+)?(?:aseguradora|compañía)", re.I), "según la póliza"),
    (re.compile(r"(?:en|para|de)\s+(?:la\s+)?(?:aseguradora|compañía)", re.I), "en la póliza"),
]


def sanitize_brands(text: str) -> str:
    for pattern, replacement in BRAND_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ── Detección de categoría ───────────────────────────
def detect_category(filepath: Path) -> str:
    name_lower = filepath.stem.lower()
    for keyword, category in CATEGORY_KEYWORDS:
        if keyword in name_lower:
            return category
    # También buscar en la ruta completa (subdirectorio)
    for part in filepath.parts:
        part_lower = str(part).lower()
        for keyword, category in CATEGORY_KEYWORDS:
            if keyword in part_lower:
                return category
    return "general"


# ── Extracción de texto del PDF ──────────────────────
def extract_text_pypdf(filepath: Path) -> Optional[str]:
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(filepath))
        pages = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                pages.append(t)
        return "\n".join(pages) if pages else None
    except Exception as e:
        logger.warning("pypdf error on %s: %s", filepath.name, e)
        return None


def extract_text_pdfplumber(filepath: Path) -> Optional[str]:
    try:
        import pdfplumber  # type: ignore

        pages = []
        with pdfplumber.open(str(filepath)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages.append(t)
        return "\n".join(pages) if pages else None
    except Exception as e:
        logger.warning("pdfplumber error on %s: %s", filepath.name, e)
        return None


def extract_text(filepath: Path) -> Optional[str]:
    text = extract_text_pypdf(filepath)
    if not text:
        text = extract_text_pdfplumber(filepath)
    if not text:
        logger.error("No se pudo extraer texto de %s", filepath.name)
        return None
    # Limpiar texto: múltiples espacios/saltos
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return sanitize_brands(text.strip())


# ── Chunking ─────────────────────────────────────────
def chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> List[str]:
    """
    Divide el texto en chunks de chunk_size chars con solape de overlap chars.
    Intenta romper en salto de párrafo o punto cuando sea posible.
    """
    chunks: List[str] = []
    start = 0
    length = len(text)

    while start < length:
        end = min(start + chunk_size, length)

        # Intentar cortar en fin de párrafo o punto
        if end < length:
            for sep in ("\n\n", ". ", "\n", " "):
                idx = text.rfind(sep, start + chunk_size // 2, end)
                if idx != -1:
                    end = idx + len(sep)
                    break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        next_start = end - overlap
        if next_start <= start:
            next_start = start + 1
        start = next_start

    return chunks


def chunk_id_for(source_file: str, idx: int, chunk_text_val: str) -> str:
    """Genera un chunk_id determinista basado en source_file + índice + hash del texto."""
    h = hashlib.md5(chunk_text_val.encode("utf-8")).hexdigest()[:8]
    return f"{Path(source_file).stem}__{idx:04d}__{h}"


# ── Embeddings ───────────────────────────────────────
def load_embedder():
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        model = SentenceTransformer(EMBED_MODEL)
        logger.info("Embedder cargado: %s", EMBED_MODEL)
        return model
    except Exception as e:
        logger.warning("SentenceTransformer no disponible: %s — se ingestará sin embeddings", e)
        return None


def embed_chunks(embedder, texts: List[str]) -> List[Optional[List[float]]]:
    if embedder is None:
        return [None] * len(texts)
    try:
        vecs = embedder.encode(
            texts,
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=False,
        )
        # vecs puede ser np.ndarray; convertimos a list[float]
        return [list(map(float, v.tolist() if hasattr(v, "tolist") else v)) for v in vecs]
    except Exception as e:
        logger.warning("Error generando embeddings: %s", e)
        return [None] * len(texts)


# ── DB ───────────────────────────────────────────────
def get_db(dsn: str):
    import psycopg  # type: ignore

    return psycopg.connect(dsn)


def bootstrap_kb_table(dsn: str) -> None:
    """Crea extensión pgvector y tabla kb_documents si no existen."""
    with get_db(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS kb_documents (
                    id          BIGSERIAL PRIMARY KEY,
                    category    TEXT NOT NULL,
                    route       TEXT,
                    source_file TEXT NOT NULL,
                    chunk_id    TEXT NOT NULL,
                    chunk_text  TEXT NOT NULL,
                    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
                    embedding   vector(384),
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE(chunk_id)
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_kb_category
                    ON kb_documents(category);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_kb_source_file
                    ON kb_documents(source_file);
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS place_cache (
                    place_text TEXT PRIMARY KEY,
                    province   TEXT NOT NULL,
                    source     VARCHAR(20) DEFAULT 'llm',
                    created_at TIMESTAMPTZ DEFAULT now()
                );
                """
            )
        conn.commit()

    logger.info("Tablas kb_documents y place_cache verificadas/creadas.")


def upsert_chunk(
    cur,
    category: str,
    source_file: str,
    chunk_id: str,
    chunk_text: str,
    embedding: Optional[List[float]],
    route: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    """
    Inserta o actualiza un chunk. Devuelve 'ok'.
    - Usa UNIQUE(chunk_id) y ON CONFLICT(chunk_id)
    - embedding puede ser NULL
    - metadata siempre es JSONB válido
    """
    metadata = metadata or {}
    metadata_json = json.dumps(metadata, ensure_ascii=False)

    vec_str: Optional[str]
    if embedding is not None:
        vec_str = "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"
    else:
        vec_str = None

    cur.execute(
        """
        INSERT INTO kb_documents (category, route, source_file, chunk_id, chunk_text, metadata, embedding)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::vector)
        ON CONFLICT (chunk_id) DO UPDATE SET
            chunk_text  = EXCLUDED.chunk_text,
            category    = EXCLUDED.category,
            route       = EXCLUDED.route,
            source_file = EXCLUDED.source_file,
            metadata    = EXCLUDED.metadata,
            embedding   = EXCLUDED.embedding
        """,
        (category, route, source_file, chunk_id, chunk_text, metadata_json, vec_str),
    )

    return "ok"


# ── Proceso principal ────────────────────────────────
def find_pdfs(data_dir: Path) -> Iterator[Path]:
    for p in sorted(data_dir.rglob("*.pdf")):
        yield p
    for p in sorted(data_dir.rglob("*.txt")):
        yield p


def ingest_file(
    filepath: Path,
    embedder,
    dsn: str,
    dry_run: bool = False,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> Tuple[int, int]:
    """
    Procesa un archivo y lo ingesta en la DB.
    Devuelve (chunks_totales, chunks_upserted).
    """
    category = detect_category(filepath)
    source_file = filepath.name

    # route “lógica”: carpeta dentro de data (si existe)
    # Ej: data/salud/xxx.pdf -> route="salud"
    try:
        route = filepath.relative_to(Path(filepath.parents[1])).parts[0]  # heurística
    except Exception:
        route = None

    logger.info("Procesando: %s → category=%s", source_file, category)

    # Extracción de texto
    if filepath.suffix.lower() == ".txt":
        try:
            raw_text = filepath.read_text(encoding="utf-8", errors="ignore")
            text = sanitize_brands(raw_text.strip())
        except Exception as e:
            logger.error("Error leyendo %s: %s", source_file, e)
            return 0, 0
    else:
        text = extract_text(filepath)

    if not text:
        logger.warning("Sin texto extraído de %s — saltando", source_file)
        return 0, 0

    # Chunking
    chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    logger.info("  %d chunks generados (chunk_size=%d, overlap=%d)", len(chunks), chunk_size, overlap)

    if dry_run:
        for i, c in enumerate(chunks[:3]):
            logger.info("  [DRY] chunk %d: %r…", i, c[:80])
        if len(chunks) > 3:
            logger.info("  [DRY] ... y %d más", len(chunks) - 3)
        return len(chunks), 0

    # Embeddings en batch
    embeddings = embed_chunks(embedder, chunks)

    # Upsert en DB
    upserted = 0
    with get_db(dsn) as conn:
        with conn.cursor() as cur:
            for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                cid = chunk_id_for(source_file, idx, chunk)
                md = {
                    "chunk_index": idx,
                    "source_stem": Path(source_file).stem,
                    "file_ext": filepath.suffix.lower(),
                }
                upsert_chunk(cur, category, source_file, cid, chunk, emb, route=route, metadata=md)
                upserted += 1
        conn.commit()

    logger.info("  ✓ %d chunks upserted para %s", upserted, source_file)
    return len(chunks), upserted


def run_ingest(
    data_dir: Path,
    dsn: str,
    dry_run: bool = False,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> dict:
    if not data_dir.exists():
        logger.error("Directorio no encontrado: %s", data_dir)
        sys.exit(1)

    if not dry_run:
        bootstrap_kb_table(dsn)

    embedder = load_embedder()

    pdfs = list(find_pdfs(data_dir))
    if not pdfs:
        logger.warning("No se encontraron PDFs/TXTs en %s", data_dir)
        return {"files": 0, "chunks_total": 0, "chunks_upserted": 0}

    logger.info("Encontrados %d archivos en %s", len(pdfs), data_dir)

    total_chunks = 0
    total_upserted = 0
    files_processed = 0

    for filepath in pdfs:
        chunks, upserted = ingest_file(
            filepath,
            embedder,
            dsn,
            dry_run=dry_run,
            chunk_size=chunk_size,
            overlap=overlap,
        )
        total_chunks += chunks
        total_upserted += upserted
        files_processed += 1

    summary = {
        "files": files_processed,
        "chunks_total": total_chunks,
        "chunks_upserted": total_upserted,
    }
    logger.info(
        "═══ RESUMEN: %d archivos | %d chunks totales | %d upserted ═══",
        files_processed,
        total_chunks,
        total_upserted,
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta PDFs/TXTs → kb_documents en Postgres (pgvector)")
    parser.add_argument("--data_dir", default=DEFAULT_DATA_DIR, help="Carpeta con PDFs/TXTs")
    parser.add_argument("--db_dsn", default=DEFAULT_DB_DSN, help="DSN de Postgres")
    parser.add_argument("--chunk_size", type=int, default=CHUNK_SIZE, help="Tamaño de chunk (chars)")
    parser.add_argument("--overlap", type=int, default=CHUNK_OVERLAP, help="Solape entre chunks (chars)")
    parser.add_argument("--dry_run", action="store_true", help="Simular sin escribir en DB")
    args = parser.parse_args()

    run_ingest(
        data_dir=Path(args.data_dir),
        dsn=args.db_dsn,
        dry_run=args.dry_run,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
    )


if __name__ == "__main__":
    main()