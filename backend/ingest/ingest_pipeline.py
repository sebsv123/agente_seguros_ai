#!/usr/bin/env python3
"""
ingest_pipeline.py — Pipeline completo de ingesta para Windows.

Lee PDFs de backend/data/raw/<categoria>/, extrae texto, limpia,
divide en chunks, genera embeddings e inserta en PostgreSQL/pgvector.

Uso (Windows):
    python backend/ingest/ingest_pipeline.py --all
    python backend/ingest/ingest_pipeline.py --all --db-dsn postgresql://...
    python backend/ingest/ingest_pipeline.py --incremental
    python backend/ingest/ingest_pipeline.py --export-json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [ingest] %(message)s",
)
logger = logging.getLogger("ingest")

# ── Config ────────────────────────────────────────────
# En Windows, usar INGEST_DB_DSN (apunta al Linux).
# En Linux, usar DB_DSN (local).
DEFAULT_DB_DSN = (
    os.getenv("INGEST_DB_DSN") or
    os.getenv("DB_DSN") or
    "postgresql://agente:agente_pw@localhost:5433/agente_ai"
)
RAW_DIR = Path("backend/data/raw")
PROCESSED_DIR = Path("backend/data/processed")
EXPORTS_DIR = Path("backend/data/exports")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "750"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "100"))

# ── Marcas a sanitizar ───────────────────────────────
_BRAND_NAMES = [
    "Adeslas", "DKV", "Sanitas", "Asisa", "Cigna", "Humana",
    "Mapfre", "Allianz", "AXA", "Generali", "Zurich", "Caser",
    "Mutua Madrileña", "Mutua", "Pelayo", "Reale", "SegurCaixa",
    "FIATC", "Fiatc", "MGS", "Helvetia", "Berkley",
    "Aegon", "CNP", "Nationale Nederlanden", "Previsora General",
    "Preventiva", "Santa Lucía", "Santa Lucia", "Ocaso", "Funespaña",
    "Línea Directa", "Linea Directa", "Grupo ASV",
    "BBVA", "Santander", "ING", "CaixaBank", "Bankinter", "Sabadell",
    "Catalana Occidente", "Plus Ultra", "Liberty", "Liberty Seguros",
]

_BRAND_PATTERNS = []
for name in _BRAND_NAMES:
    escaped = re.escape(name)
    pattern = re.compile(r"\b" + escaped + r"\b", re.I)
    replacement = "la aseguradora"
    nl = name.lower()
    if any(k in nl for k in ["banco", "bbva", "santander", "ing", "caixabank", "bankinter", "sabadell"]):
        replacement = "la entidad bancaria"
    elif any(k in nl for k in ["mutua"]):
        replacement = "la mutua"
    elif any(k in nl for k in ["funeraria", "funespaña"]):
        replacement = "el servicio funerario"
    _BRAND_PATTERNS.append((pattern, replacement))


def sanitize_brands(text: str) -> str:
    for pattern, replacement in _BRAND_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ── Paso 1: Extraer texto de PDF ─────────────────────
def extract_text(filepath: Path) -> Optional[str]:
    try:
        import pypdf
        reader = pypdf.PdfReader(str(filepath))
        pages = [p.extract_text() or "" for p in reader.pages]
        return "\n".join(pages)
    except Exception as e:
        logger.warning("pypdf error on %s: %s", filepath.name, e)
    try:
        import pdfplumber
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


# ── Paso 2: Limpiar texto ────────────────────────────
def clean_text(text: str) -> str:
    if not text:
        return ""
    text = sanitize_brands(text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n\s*\d+\s*\n", "\n", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Paso 3: Detectar categoría ───────────────────────
def detect_category(filepath: Path) -> str:
    name_lower = filepath.stem.lower()
    keywords = {
        "salud": ["salud", "health", "medico", "medica"],
        "vida": ["vida", "life", "fallecimiento"],
        "dental": ["dental", "dentista", "odontologia", "bucal"],
        "mascotas": ["mascota", "pet", "perro", "gato"],
        "decesos": ["deceso", "funeraria", "entierro"],
        "autonomos": ["autonomo", "autónomo", "freelance"],
        "extranjeria": ["extranjero", "foreigner", "expat"],
        "accidentes": ["accidente"],
        "juridica": ["juridico", "jurídico", "defensa", "legal", "abogado"],
    }
    for cat, kws in keywords.items():
        for kw in kws:
            if kw in name_lower:
                return cat
    for part in filepath.parts:
        pl = str(part).lower()
        for cat, kws in keywords.items():
            for kw in kws:
                if kw in pl:
                    return cat
    return "general"


# ── Paso 4: Chunking ─────────────────────────────────
def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    chunks = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_size, length)
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
    h = hashlib.md5(chunk_text_val.encode("utf-8")).hexdigest()[:8]
    return f"{Path(source_file).stem}__{idx:04d}__{h}"


# ── Paso 5: Generar embeddings ───────────────────────
def load_embedder():
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        logger.info("Embedder loaded: all-MiniLM-L6-v2")
        return model
    except Exception as e:
        logger.error("Cannot load embedder: %s", e)
        logger.error("Install: pip install sentence-transformers numpy")
        sys.exit(1)


def embed_chunks(embedder, texts: List[str]) -> List[List[float]]:
    vecs = embedder.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=True)
    return [list(map(float, v.tolist())) for v in vecs]


# ── Paso 6: Insertar en PostgreSQL ────────────────────
def get_db(dsn: str):
    import psycopg
    return psycopg.connect(dsn)


def bootstrap_kb_table(dsn: str) -> None:
    """Crea extensión pgvector y tabla document_chunks."""
    ddl = """
    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE TABLE IF NOT EXISTS document_chunks (
        id          BIGSERIAL PRIMARY KEY,
        document_id VARCHAR(64) NOT NULL,
        source_file TEXT NOT NULL,
        title       TEXT,
        category    VARCHAR(50) NOT NULL DEFAULT 'general',
        chunk_id    VARCHAR(64) NOT NULL UNIQUE,
        chunk_text  TEXT NOT NULL,
        chunk_order INT NOT NULL DEFAULT 0,
        tags        JSONB DEFAULT '[]'::jsonb,
        metadata    JSONB DEFAULT '{}'::jsonb,
        embedding   vector(384),
        checksum    VARCHAR(32),
        version     INT DEFAULT 1,
        created_at  TIMESTAMPTZ DEFAULT now(),
        updated_at  TIMESTAMPTZ DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS idx_doc_chunks_category ON document_chunks(category);
    CREATE INDEX IF NOT EXISTS idx_doc_chunks_source ON document_chunks(source_file);
    CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc_id ON document_chunks(document_id);
    CREATE INDEX IF NOT EXISTS idx_doc_chunks_checksum ON document_chunks(checksum);
    """
    with get_db(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
    logger.info("Table document_chunks + pgvector ready")


def insert_chunk(cur, chunk: Dict[str, Any]) -> None:
    cur.execute(
        """
        INSERT INTO document_chunks
            (document_id, source_file, title, category, chunk_id, chunk_text,
             chunk_order, tags, metadata, embedding, checksum, version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::vector, %s, %s)
        ON CONFLICT (chunk_id) DO UPDATE SET
            chunk_text = EXCLUDED.chunk_text,
            embedding  = EXCLUDED.embedding,
            checksum   = EXCLUDED.checksum,
            version    = document_chunks.version + 1,
            updated_at = now()
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
            "[" + ",".join(f"{x:.6f}" for x in chunk["embedding"]) + "]",
            chunk["checksum"],
            chunk.get("version", 1),
        ),
    )


# ── Pipeline completo ────────────────────────────────
def process_file(filepath: Path, embedder, dsn: str, dry_run: bool = False) -> Dict[str, Any]:
    """Procesa un archivo: extrae, limpia, chunk, embedding, inserta."""
    logger.info("Processing: %s", filepath.name)

    # 1. Extraer texto
    text = extract_text(filepath)
    if not text:
        logger.warning("No text extracted from %s", filepath.name)
        return {"file": filepath.name, "status": "no_text", "chunks": 0}

    # 2. Limpiar
    text = clean_text(text)
    if not text:
        logger.warning("Empty after cleaning: %s", filepath.name)
        return {"file": filepath.name, "status": "empty", "chunks": 0}

    # 3. Detectar categoría
    category = detect_category(filepath)

    # 4. Chunking
    chunks = chunk_text(text)
    logger.info("  %d chunks generated", len(chunks))

    if dry_run:
        return {"file": filepath.name, "status": "dry_run", "chunks": len(chunks), "category": category}

    # 5. Embeddings
    embeddings = embed_chunks(embedder, chunks)

    # 6. Preparar datos
    checksum = hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
    document_id = f"{category}__{filepath.stem}__{checksum[:8]}"
    source_file = str(filepath.relative_to(RAW_DIR.parent.parent)) if RAW_DIR in filepath.parents else filepath.name

    rows = []
    for idx, (chunk_text_val, emb) in enumerate(zip(chunks, embeddings)):
        cid = chunk_id_for(filepath.name, idx, chunk_text_val)
        rows.append({
            "document_id": document_id,
            "source_file": source_file,
            "title": filepath.stem,
            "category": category,
            "chunk_id": cid,
            "chunk_text": chunk_text_val,
            "chunk_order": idx,
            "tags": [category],
            "metadata": {"source_stem": filepath.stem, "file_ext": filepath.suffix.lower()},
            "embedding": emb,
            "checksum": checksum,
            "version": 1,
        })

    # 7. Insertar en PostgreSQL
    inserted = 0
    with get_db(dsn) as conn:
        with conn.cursor() as cur:
            for row in rows:
                insert_chunk(cur, row)
                inserted += 1
        conn.commit()

    logger.info("  ✓ %d chunks inserted for %s", inserted, filepath.name)
    return {"file": filepath.name, "status": "ok", "chunks": inserted, "category": category}


def find_files(data_dir: Path) -> List[Path]:
    files = []
    for p in sorted(data_dir.rglob("*.pdf")):
        files.append(p)
    for p in sorted(data_dir.rglob("*.txt")):
        files.append(p)
    return files


def run_pipeline(
    data_dir: Path,
    dsn: str,
    mode: str = "all",
    dry_run: bool = False,
    export_json: bool = False,
) -> Dict[str, Any]:
    if not data_dir.exists():
        logger.error("Directory not found: %s", data_dir)
        sys.exit(1)

    pdfs = find_files(data_dir)
    if not pdfs:
        logger.warning("No PDFs found in %s", data_dir)
        return {"files": 0, "chunks": 0}

    logger.info("Found %d files in %s", len(pdfs), data_dir)

    if mode in ("all", "db"):
        bootstrap_kb_table(dsn)

    embedder = load_embedder()

    results = []
    total_chunks = 0
    for filepath in pdfs:
        result = process_file(filepath, embedder, dsn, dry_run=dry_run)
        results.append(result)
        total_chunks += result.get("chunks", 0)

    # Exportar a JSON si se solicita
    if export_json and not dry_run:
        export_path = EXPORTS_DIR / "knowledge_base.json"
        export_data = []
        with get_db(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT document_id, source_file, title, category, chunk_id, chunk_text, "
                    "chunk_order, tags, metadata, checksum, version, created_at "
                    "FROM document_chunks ORDER BY source_file, chunk_order"
                )
                for row in cur.fetchall():
                    export_data.append({
                        "document_id": row[0],
                        "source_file": row[1],
                        "title": row[2],
                        "category": row[3],
                        "chunk_id": row[4],
                        "chunk_text": row[5],
                        "chunk_order": row[6],
                        "tags": row[7],
                        "metadata": row[8],
                        "checksum": row[9],
                        "version": row[10],
                        "created_at": row[11].isoformat() if row[11] else None,
                    })
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        logger.info("Exported %d chunks to %s", len(export_data), export_path)

    summary = {"files": len(pdfs), "chunks": total_chunks, "results": results}
    logger.info("═══ DONE: %d files, %d chunks ═══", len(pdfs), total_chunks)
    return summary


# ── CLI ──────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest pipeline: PDFs → pgvector")
    parser.add_argument("--data-dir", default=str(RAW_DIR), help="Directory with PDFs")
    parser.add_argument("--db-dsn", default=DEFAULT_DB_DSN, help="PostgreSQL DSN")
    parser.add_argument("--all", action="store_true", dest="mode_all", help="Full pipeline")
    parser.add_argument("--incremental", action="store_true", help="Only new/modified files")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without inserting")
    parser.add_argument("--export-json", action="store_true", help="Export to JSON after insert")
    args = parser.parse_args()

    mode = "all"
    if args.incremental:
        mode = "incremental"

    run_pipeline(
        data_dir=Path(args.data_dir),
        dsn=args.db_dsn,
        mode=mode,
        dry_run=args.dry_run,
        export_json=args.export_json,
    )


if __name__ == "__main__":
    main()
