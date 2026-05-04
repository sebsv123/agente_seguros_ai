import os
import re
import numpy as np
import psycopg
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

DB_DSN = os.getenv("DB_DSN", "postgresql://agente:agente_pw@localhost:5433/agente_ai")
PDF_DIR = os.getenv("PDF_DIR", r"..\data")
CATEGORY = "salud"

embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 200):
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks = []
    i = 0
    while i < len(text):
        end = min(len(text), i + max_chars)
        chunks.append(text[i:end])
        i = max(0, end - overlap)
        if end == len(text):
            break
    return chunks


def infer_route_from_filename(fn: str) -> str | None:
    f = fn.lower()
    if "internacional" in f:
        return "R5"
    if "reembolso" in f:
        return "R4"
    if "integral" in f:
        return "R4"  # integral pymes es reembolso en tus docs
    if "completa" in f:
        return "R3"
    if "ya" in f:
        return "R2"
    if "esencial" in f or "proxima" in f:
        return "R1"
    # pymes genérico puede ser completo; lo dejamos None si no queremos suponer
    return None


def extract_pdf_text(path: str) -> str:
    reader = PdfReader(path)
    pages = []
    for p in reader.pages:
        pages.append(p.extract_text() or "")
    return "\n".join(pages)


def main():
    pdfs = [p for p in os.listdir(PDF_DIR) if p.lower().endswith(".pdf")]
    if not pdfs:
        raise SystemExit(f"No hay PDFs en {PDF_DIR}")

    with psycopg.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            for pdf in pdfs:
                path = os.path.join(PDF_DIR, pdf)
                text = extract_pdf_text(path)

                route = infer_route_from_filename(pdf)

                chunks = chunk_text(text)
                if not chunks:
                    print(f"[SKIP] Sin texto: {pdf}")
                    continue

                embs = embedder.encode(chunks, normalize_embeddings=True).astype(np.float32)

                for idx, (ch, emb) in enumerate(zip(chunks, embs), start=1):
                    cur.execute(
                        """
                        INSERT INTO kb_documents (category, route, plan_name_generic, source_file, chunk_id, chunk_text, metadata, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector)
                        """,
                        (CATEGORY, route, None, pdf, idx, ch, "{}", emb.tolist())
                    )

                conn.commit()
                print(f"[OK] Ingestado: {pdf} | route={route} | chunks={len(chunks)}")


if __name__ == "__main__":
    main()