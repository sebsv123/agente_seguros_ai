-- ============================================================
-- setup_db.sql — Configuración de PostgreSQL/pgvector
-- Se ejecuta automáticamente al iniciar el contenedor db
-- Crea: extensión pgvector, tabla document_chunks, índices
--
-- NOTA: Las contraseñas se configuran vía variables de entorno
-- en docker-compose.yml (POSTGRES_USER, POSTGRES_PASSWORD)
-- No hardcodees credenciales aquí.
-- ============================================================

-- 1. Extensión pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Tabla principal de chunks con embeddings
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

-- 3. Índices
CREATE INDEX IF NOT EXISTS idx_doc_chunks_category ON document_chunks(category);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_source ON document_chunks(source_file);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc_id ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_checksum ON document_chunks(checksum);

-- 4. Índice full-text (fallback cuando no hay embedding)
CREATE INDEX IF NOT EXISTS idx_doc_chunks_fts
    ON document_chunks USING GIN (to_tsvector('spanish', chunk_text));

-- 5. Verificación
SELECT 'Setup complete' AS status,
       (SELECT COUNT(*) FROM document_chunks) AS total_chunks;
