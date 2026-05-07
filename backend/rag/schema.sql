-- ============================================================
-- schema.sql — Esquema completo para pgvector
-- Ejecutar en Windows (ingesta) y Linux (consulta)
-- Crea extensión, tabla document_chunks e índices
-- ============================================================

-- 1. Extensión pgvector (necesita que la imagen sea pgvector/pgvector:pg16)
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Tabla principal de chunks con embeddings
CREATE TABLE IF NOT EXISTS document_chunks (
    id          BIGSERIAL PRIMARY KEY,
    document_id VARCHAR(64) NOT NULL,       -- salud__folleto__a1b2c3d4
    source_file TEXT NOT NULL,               -- ruta relativa del PDF original
    title       TEXT,                        -- nombre del documento
    category    VARCHAR(50) NOT NULL DEFAULT 'general',  -- salud, vida, dental...
    chunk_id    VARCHAR(64) NOT NULL UNIQUE, -- folleto__0001__a1b2c3d4
    chunk_text  TEXT NOT NULL,               -- contenido del chunk
    chunk_order INT NOT NULL DEFAULT 0,      -- orden dentro del documento
    tags        JSONB DEFAULT '[]'::jsonb,   -- ["salud", "familia"]
    metadata    JSONB DEFAULT '{}'::jsonb,   -- {"source_stem": "folleto", "file_ext": ".pdf"}
    embedding   vector(384),                 -- vector de 384 dimensiones (all-MiniLM-L6-v2)
    checksum    VARCHAR(32),                 -- md5 del texto limpio (para detectar cambios)
    version     INT DEFAULT 1,               -- versión del chunk (incrementa al actualizar)
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- 3. Índices para búsqueda
CREATE INDEX IF NOT EXISTS idx_doc_chunks_category ON document_chunks(category);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_source ON document_chunks(source_file);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc_id ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_checksum ON document_chunks(checksum);

-- 4. Índice para búsqueda full-text (fallback cuando no hay embedding)
CREATE INDEX IF NOT EXISTS idx_doc_chunks_fts
    ON document_chunks USING GIN (to_tsvector('spanish', chunk_text));

-- 5. Índice para búsqueda por similitud coseno (pgvector)
-- NOTA: Se necesita crear un índice IVFFlat para rendimiento en producción
-- El número de listas depende del tamaño de los datos:
--   - < 100K filas: lists = filas / 1000
--   - > 100K filas: lists = sqrt(filas)
-- Ejecutar DESPUÉS de haber insertado datos:
-- CREATE INDEX IF NOT EXISTS idx_doc_chunks_embedding
--     ON document_chunks USING ivfflat (embedding vector_cosine_ops)
--     WITH (lists = 100);

-- 6. Verificación
SELECT 'Schema ready' AS status,
       (SELECT COUNT(*) FROM document_chunks) AS total_chunks;
