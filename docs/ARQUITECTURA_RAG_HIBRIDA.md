# Arquitectura RAG Híbrida — Valentín Protección Integral

## 1. Filosofía del diseño

```
┌─────────────────────────────────────────────────────────────────┐
│                    WINDOWS (potente)                            │
│  Ingesta, limpieza, chunking, embeddings → exporta JSON + DB   │
│  Dependencias pesadas: sentence-transformers, numpy, pypdf     │
└────────────────────────┬────────────────────────────────────────┘
                         │ git push (solo JSON ligeros + scripts)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    LINUX (humilde)                              │
│  FastAPI ligero, consume JSON/DB, responde vía DeepSeek API    │
│  Dependencias mínimas: fastapi, uvicorn, psycopg, pydantic     │
│  SIN torch, SIN numpy, SIN modelos locales                     │
└─────────────────────────────────────────────────────────────────┘
```

## 2. Estructura de carpetas final

```
agente-seguros/
├── backend/
│   ├── app.py                    ← FastAPI (Linux y Windows)
│   ├── agent_evaluator.py        ← Feedback loop
│   ├── extract_product_rules.py  ← Playbooks desde PDFs
│   ├── requirements.txt          ← Mínimo producción (6 deps)
│   ├── requirements-dev.txt      ← Desarrollo (con PDFs)
│   ├── requirements-win.txt      ← Windows (con embeddings)
│   │
│   ├── ingest/                   ← SOLO WINDOWS (nuevo)
│   │   ├── __init__.py
│   │   ├── ingest_pipeline.py    ← Pipeline completo
│   │   ├── text_cleaner.py       ← Limpieza de texto
│   │   ├── chunker.py            ← Chunking inteligente
│   │   ├── embedder.py           ← Embeddings (sentence-transformers)
│   │   ├── exporter.py           ← Exporta JSON + DB
│   │   └── sync_manifest.py      ← Manifiesto Git
│   │
│   ├── rag/                      ← Linux y Windows
│   │   ├── __init__.py
│   │   ├── search.py             ← Búsqueda semántica (pgvector)
│   │   └── remote_llm.py         ← Llamada a DeepSeek/Groq API
│   │
│   └── data/                     ← Solo Windows (gitignorado)
│       ├── raw/                  ← PDFs originales
│       ├── processed/            ← JSONs procesados
│       └── exports/              ← Backup exportable
│
├── docs/
│   └── ARQUITECTURA_RAG_HIBRIDA.md  ← Este documento
│
├── Dockerfile                    ← python:3.12-slim
├── docker-compose.yml            ← db + backend
└── .dockerignore
```

## 3. Flujo de trabajo

### Fase 1: Windows — Ingesta y procesamiento

```bash
# 1. Instalar dependencias Windows
pip install -r backend/requirements-win.txt

# 2. Colocar PDFs en backend/data/raw/<categoria>/
#    Ej: backend/data/raw/salud/folleto_salud.pdf

# 3. Ejecutar pipeline completo
python backend/ingest/ingest_pipeline.py --all

# 4. Opciones del pipeline
python backend/ingest/ingest_pipeline.py --help
#   --all           : pipeline completo
#   --clean-only    : solo limpiar texto
#   --chunk-only    : solo dividir en chunks
#   --embed-only    : solo generar embeddings
#   --export-json   : exportar a JSON
#   --export-db     : exportar a PostgreSQL
#   --incremental   : solo archivos nuevos/modificados

# 5. Generar manifiesto para Git
python backend/ingest/sync_manifest.py
```

### Fase 2: Git — Sincronización

```bash
# Solo se suben:
#   - backend/ingest/*.py          (scripts)
#   - backend/rag/*.py             (scripts)
#   - backend/data/exports/*.json  (JSONs ligeros)
#   - backend/data/exports/manifest.json

# NO se suben:
#   - backend/data/raw/*.pdf       (PDFs originales)
#   - backend/data/processed/*.json (JSONs pesados con embeddings)
#   - .venv/, __pycache__/
```

### Fase 3: Linux — Despliegue

```bash
# 1. Clonar
git clone https://github.com/sebsv123/agente_seguros_ai.git
cd agente_seguros_ai

# 2. Instalar solo lo mínimo
pip install --no-cache-dir -r backend/requirements.txt

# 3. Copiar JSONs exportados (desde Windows o backup)
#    Colocar en backend/data/exports/

# 4. Importar a PostgreSQL
python backend/rag/search.py --import-json backend/data/exports/knowledge_base.json

# 5. Levantar servidor
uvicorn backend.app:app --host 0.0.0.0 --port 8000

# O con Docker:
docker compose up -d --build
```

## 4. Scripts de ingesta (solo Windows)

### `backend/ingest/text_cleaner.py`

```python
# Limpieza de texto extraído de PDFs:
# - Elimina marcas de aseguradoras → términos neutros
# - Elimina números de página, encabezados, pies
# - Normaliza espacios y saltos de línea
# - Elimina caracteres de control
# - Detecta y separa secciones del documento
```

### `backend/ingest/chunker.py`

```python
# Chunking inteligente:
# - Por párrafos (respetando estructura del documento)
# - Con solape configurable (default: 100 caracteres)
# - Tamaño de chunk configurable (default: 750 caracteres)
# - Intenta romper en límites de párrafo o frase
# - Genera chunk_id determinista (hash del contenido)
```

### `backend/ingest/embedder.py`

```python
# Generación de embeddings (SOLO WINDOWS):
# - Usa sentence-transformers/all-MiniLM-L6-v2
# - Normaliza embeddings (cosine similarity)
# - Batch processing (32 chunks por lote)
# - Cache de embeddings para evitar recalcular
# - Exporta a JSON para Linux
```

### `backend/ingest/exporter.py`

```python
# Exportación a JSON:
# - knowledge_base.json: chunks + embeddings + metadatos
# - manifest.json: checksums, versiones, fechas
# - Por producto: salud_embeddings.json, vida_embeddings.json, etc.
```

## 5. Búsqueda semántica en Linux (sin modelos locales)

### `backend/rag/search.py`

```python
# Búsqueda semántica SIN sentence-transformers en Linux:
#
# Opción A (recomendada): API externa de embeddings
#   - Usa la API de DeepSeek o Groq para generar embeddings
#   - endpoint: POST https://api.deepseek.com/v1/embeddings
#   - modelo: deepseek-embedding
#   - Coste: ~$0.0001 por consulta
#
# Opción B: pgvector con cosine similarity
#   - Los embeddings ya están en la DB (importados desde Windows)
#   - Búsqueda: SELECT * FROM kb_documents ORDER BY embedding <=> $1 LIMIT 5
#   - No necesita ningún modelo local
#
# Opción C: Fallback a búsqueda por palabras clave
#   - Si no hay embeddings, busca por coincidencia de texto
#   - Usa PostgreSQL tsvector para búsqueda full-text
```

### `backend/rag/remote_llm.py`

```python
# Llamadas a API externa para respuestas:
#
# DeepSeek Chat:   https://api.deepseek.com/v1/chat/completions
#   - Modelo: deepseek-chat
#   - Contexto: 64K tokens
#   - Precio: ~$0.28/1M tokens
#
# Groq (LLaMA):    https://api.groq.com/openai/v1/chat/completions
#   - Modelo: llama-3.3-70b-versatile
#   - Contexto: 128K tokens
#   - Precio: gratuito (rate limited)
```

## 6. Requirements separados

### `backend/requirements.txt` — Linux (producción)

```
fastapi==0.115.6
uvicorn[standard]==0.32.1
psycopg[binary]==3.2.3
pydantic==2.10.3
python-dotenv==1.0.1
apscheduler==3.10.4
```

### `backend/requirements-dev.txt` — Linux (desarrollo)

```
-r requirements.txt
pypdf==5.1.0
pdfplumber==0.11.4
```

### `backend/requirements-win.txt` — Windows (ingesta)

```
-r requirements-dev.txt
sentence-transformers==3.2.1
numpy==2.1.3
```

## 7. docker-compose.yml recomendado

```yaml
version: '3.8'

services:
  db:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    environment:
      POSTGRES_USER: agente
      POSTGRES_PASSWORD: agente_pw
      POSTGRES_DB: agente_ai
    ports:
      - "5433:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agente -d agente_ai"]
      interval: 10s
      timeout: 5s
      retries: 5

  backend:
    build: .
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      DB_DSN: postgresql://agente:agente_pw@db:5432/agente_ai
      GROQ_API_KEY: ${GROQ_API_KEY}
      GROQ_MODEL: llama-3.3-70b-versatile
      KB_ADMIN_TOKEN: ${KB_ADMIN_TOKEN}
    depends_on:
      db:
        condition: service_healthy

volumes:
  pgdata:
```

## 8. Plan de despliegue paso a paso

### Semana 1 — Windows (preparación)

```bash
# 1. Clonar repo
git clone https://github.com/sebsv123/agente_seguros_ai.git
cd agente_seguros_ai

# 2. Instalar dependencias Windows
python -m venv .venv
.venv\Scripts\activate
pip install --no-cache-dir -r backend/requirements-win.txt

# 3. Colocar PDFs en backend/data/raw/<categoria>/
mkdir backend\data\raw\salud
mkdir backend\data\raw\vida
# ... copiar PDFs a cada carpeta

# 4. Ejecutar pipeline completo
python backend/ingest/ingest_pipeline.py --all --export-json --export-db

# 5. Verificar salida
dir backend\data\exports\

# 6. Generar manifiesto
python backend/ingest/sync_manifest.py

# 7. Commit y push
git add backend/ingest/ backend/rag/ backend/data/exports/manifest.json
git commit -m "feat: ingesta inicial de conocimiento"
git push
```

### Semana 2 — Linux (despliegue)

```bash
# 1. Clonar en Linux
git clone https://github.com/sebsv123/agente_seguros_ai.git
cd agente_seguros_ai

# 2. Copiar JSONs exportados (desde Windows vía USB/SCP)
scp windows-user@windows-ip:~/agente-seguros/backend/data/exports/*.json \
    ./backend/data/exports/

# 3. Configurar .env
cp .env.example .env
nano .env  # Añadir GROQ_API_KEY, etc.

# 4. Levantar con Docker
docker compose up -d --build

# 5. Importar conocimiento a DB
docker compose exec backend python -m backend.rag.search --import-json

# 6. Verificar
curl http://localhost:8000/health
curl http://localhost:8000/kb/stats
```

### Semana 3+ — Mantenimiento

```bash
# En Windows: actualizar PDFs y re-ejecutar
python backend/ingest/ingest_pipeline.py --incremental --export-json
python backend/ingest/sync_manifest.py
git add backend/data/exports/manifest.json
git commit -m "feat: actualización conocimiento"
git push

# En Linux: actualizar
git pull
scp windows-user@windows-ip:~/agente-seguros/backend/data/exports/*.json \
    ./backend/data/exports/
docker compose exec backend python -m backend.rag.search --import-json
```

## 9. Resumen de dependencias por entorno

| Entorno | Dependencias | Tamaño |
|---------|-------------|--------|
| **Linux (producción)** | fastapi, uvicorn, psycopg, pydantic, dotenv, apscheduler | ~12 MB |
| **Linux (desarrollo)** | + pypdf, pdfplumber | ~17 MB |
| **Windows (ingesta)** | + sentence-transformers, numpy | ~1.6 GB |
| **Docker (Linux)** | python:3.12-slim + requirements.txt | ~250 MB |

## 10. Diagrama de flujo de datos

```
PDFs (Windows)
    │
    ▼
text_cleaner.py ───→ texto limpio
    │
    ▼
chunker.py ───→ chunks + metadatos
    │
    ▼
embedder.py ───→ chunks + embeddings (sentence-transformers)
    │
    ├──→ exporter.py ───→ knowledge_base.json (→ Git)
    │
    └──→ PostgreSQL local (pgvector)
                        │
                        │ git push (solo JSON)
                        ▼
              Linux (producción)
                        │
                        │ docker compose up
                        ▼
              PostgreSQL (pgvector) ← import JSON
                        │
                        │ POST /rag/ask
                        ▼
              search.py (pgvector cosine)
                        │
                        │ context + pregunta
                        ▼
              remote_llm.py (DeepSeek/Groq API)
                        │
                        ▼
              Respuesta al lead
```
