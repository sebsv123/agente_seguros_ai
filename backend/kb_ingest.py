"""
kb_ingest.py — Ingesta de PDFs a la KB (pgvector) con chunking + embeddings.

Flujo:
1. Extraer texto del PDF (PyMuPDF + fallback OCR con Tesseract)
2. Dividir en chunks semánticos
3. Generar embeddings con sentence-transformers
4. Insertar en kb_documents con ON CONFLICT DO UPDATE
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kb_ingest")

# ── Optional dependencies ────────────────────────────
try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except Exception:
    fitz = None  # type: ignore
    _HAS_FITZ = False

try:
    import pytesseract
    _HAS_TESSERACT = True
except Exception:
    pytesseract = None  # type: ignore
    _HAS_TESSERACT = False

try:
    from PIL import Image
    from io import BytesIO
    _HAS_PIL = True
except Exception:
    Image = None  # type: ignore
    BytesIO = None  # type: ignore
    _HAS_PIL = False


# ── 1. Extracción de texto ──────────────────────────

def extract_text_from_pdf(filepath: str) -> str:
    """
    Extrae texto de un PDF usando PyMuPDF como método principal.
    Si el texto extraído es < 100 caracteres, intenta OCR con Tesseract.
    """
    if not _HAS_FITZ:
        logger.error("PyMuPDF (fitz) no disponible")
        return ""

    try:
        doc = fitz.open(filepath)
        pages = []
        for page in doc:
            t = page.get_text()
            if t and len(t.strip()) > 100:
                pages.append(t.strip())
            elif _HAS_TESSERACT and _HAS_PIL:
                # Fallback OCR
                try:
                    pix = page.get_pixmap(dpi=200)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    ocr = pytesseract.image_to_string(img, lang="spa+eng")
                    if ocr.strip():
                        pages.append(ocr.strip())
                except Exception as ocr_err:
                    logger.warning("OCR fallback error en página: %s", ocr_err)
        doc.close()

        text = "\n\n".join(pages)
        # Limpiar líneas vacías repetidas
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    except Exception as e:
        logger.error("Error extrayendo texto de %s: %s", filepath, e)
        return ""


# ── 2. Chunking ─────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 400, overlap: int = 80) -> list[str]:
    """
    Divide el texto en chunks semánticos.
    1. Divide por párrafos (doble salto de línea)
    2. Si un párrafo es > chunk_size, divide por frases (punto + espacio)
    3. Respeta overlap entre chunks consecutivos
    4. Filtra chunks con < 40 caracteres
    """
    if not text:
        return []

    # Dividir por párrafos
    paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks: list[str] = []
    buffer = ""

    for para in paragraphs:
        if len(para) <= chunk_size:
            # Cabe en un chunk
            candidate = (buffer + " " + para).strip() if buffer else para
            if len(candidate) <= chunk_size:
                buffer = candidate
            else:
                if buffer:
                    chunks.append(buffer)
                buffer = para
        else:
            # Párrafo largo: dividir por frases
            if buffer:
                chunks.append(buffer)
                buffer = ""

            sentences = re.split(r"(?<=[.!?])\s+", para)
            phrase_buffer = ""
            for sent in sentences:
                candidate = (phrase_buffer + " " + sent).strip() if phrase_buffer else sent
                if len(candidate) <= chunk_size:
                    phrase_buffer = candidate
                else:
                    if phrase_buffer:
                        chunks.append(phrase_buffer)
                    phrase_buffer = sent
            if phrase_buffer:
                buffer = phrase_buffer

    if buffer:
        chunks.append(buffer)

    # Aplicar overlap
    if overlap > 0 and len(chunks) > 1:
        overlapped = []
        for i, chunk in enumerate(chunks):
            if i > 0:
                prev_end = chunks[i - 1][-overlap:]
                chunk = prev_end + " " + chunk
            overlapped.append(chunk)
        chunks = overlapped

    # Filtrar chunks cortos
    chunks = [c for c in chunks if len(c) >= 40]

    return chunks


# ── 3. Ingesta de un PDF ────────────────────────────

def ingest_pdf(
    filepath: str,
    category: str,
    route: Optional[str] = None,
    embedder: Any = None,
    db_dsn: str = "",
) -> dict:
    """
    Ingiere un PDF en la KB.
    Retorna: {"source_file": str, "chunks_total": int, "chunks_inserted": int, "chunks_skipped": int}
    """
    import psycopg

    source_file = os.path.basename(filepath)
    logger.info("Ingestando %s (categoría=%s, route=%s)", source_file, category, route)

    # Extraer texto
    text = extract_text_from_pdf(filepath)
    if not text:
        logger.warning("Texto vacío para %s", source_file)
        return {"source_file": source_file, "chunks_total": 0, "chunks_inserted": 0, "chunks_skipped": 0}

    # Chunking
    chunks = chunk_text(text)
    if not chunks:
        logger.warning("Sin chunks para %s", source_file)
        return {"source_file": source_file, "chunks_total": 0, "chunks_inserted": 0, "chunks_skipped": 0}

    logger.info("Extraídos %d chunks de %s", len(chunks), source_file)

    # Generar embeddings e insertar
    inserted = 0
    skipped = 0

    for i, chunk in enumerate(chunks):
        chunk_id = f"{source_file}_{i:04d}"

        # Generar embedding
        embedding = None
        if embedder is not None:
            try:
                vec = embedder.encode(chunk, normalize_embeddings=True)
                embedding = "[" + ",".join(f"{float(v):.6f}" for v in vec) + "]"
            except Exception as e:
                logger.warning("Embedding error chunk %s: %s", chunk_id, e)

        # Insertar en BD
        try:
            with psycopg.connect(db_dsn) as conn:
                with conn.cursor() as cur:
                    if embedding:
                        cur.execute(
                            """
                            INSERT INTO kb_documents (category, route, source_file, chunk_id, chunk_text, embedding)
                            VALUES (%s, %s, %s, %s, %s, %s::vector)
                            ON CONFLICT (source_file, chunk_id) DO UPDATE SET
                                chunk_text = EXCLUDED.chunk_text,
                                embedding = EXCLUDED.embedding,
                                created_at = now()
                            """,
                            (category, route, source_file, chunk_id, chunk, embedding),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO kb_documents (category, route, source_file, chunk_id, chunk_text)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (source_file, chunk_id) DO UPDATE SET
                                chunk_text = EXCLUDED.chunk_text,
                                created_at = now()
                            """,
                            (category, route, source_file, chunk_id, chunk),
                        )
                conn.commit()
            inserted += 1
        except Exception as e:
            logger.error("Error insertando chunk %s: %s", chunk_id, e)
            skipped += 1

    logger.info("Ingesta completada %s: %d insertados, %d saltados", source_file, inserted, skipped)
    return {
        "source_file": source_file,
        "chunks_total": len(chunks),
        "chunks_inserted": inserted,
        "chunks_skipped": skipped,
    }


# ── 4. Ingesta de directorio ────────────────────────

def _detect_category(filepath: str) -> str:
    """Detecta categoría desde el nombre del archivo."""
    name = os.path.basename(filepath).lower()
    if any(kw in name for kw in ["salud", "medico", "médico", "sanitas"]):
        return "salud"
    if "vida" in name:
        return "vida"
    if "dental" in name:
        return "dental"
    if any(kw in name for kw in ["mascota", "pet"]):
        return "mascotas"
    if any(kw in name for kw in ["viaje", "travel"]):
        return "viaje"
    if any(kw in name for kw in ["negocio", "empresa", "autonomo", "autónomo"]):
        return "negocios"
    return "salud"


def ingest_directory(
    data_dir: str = "./data",
    embedder: Any = None,
    db_dsn: str = "",
) -> list[dict]:
    """
    Procesa todos los PDFs del directorio.
    Retorna lista de resultados por archivo.
    """
    from pathlib import Path

    data_path = Path(data_dir)
    if not data_path.exists():
        logger.error("Directorio no encontrado: %s", data_dir)
        return []

    pdfs = sorted(data_path.rglob("*.pdf"))
    if not pdfs:
        logger.warning("No se encontraron PDFs en %s", data_dir)
        return []

    logger.info("Procesando %d PDFs en %s", len(pdfs), data_dir)
    results = []

    for pdf_path in pdfs:
        category = _detect_category(str(pdf_path))
        result = ingest_pdf(
            filepath=str(pdf_path),
            category=category,
            embedder=embedder,
            db_dsn=db_dsn,
        )
        results.append(result)

    return results
