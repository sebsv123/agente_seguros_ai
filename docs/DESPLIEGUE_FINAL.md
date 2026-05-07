# Despliegue Final — Valentín Protección Integral

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────────┐
│                        WINDOWS (potente)                            │
│                                                                     │
│  1. Colocar PDFs en backend/data/raw/<categoria>/                   │
│  2. pip install -r backend/requirements-win.txt                     │
│  3. python backend/ingest/ingest_pipeline.py --all                  │
│     ↓                                                               │
│     Lee PDFs → extrae texto → limpia → chunk → embeddings          │
│     → INSERTA DIRECTAMENTE en PostgreSQL/pgvector                   │
│     → Exporta knowledge_base.json (backup opcional)                 │
│                                                                     │
│  4. git add backend/ingest/ backend/rag/ backend/requirements*.txt  │
│  5. git commit -m "feat: actualización conocimiento"                │
│  6. git push                                                        │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ git push (solo código)
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        LINUX (humilde)                              │
│                                                                     │
│  1. git pull                                                        │
│  2. docker compose up -d --build                                    │
│     ↓                                                               │
│     PostgreSQL/pgvector (ya poblado desde Windows)                  │
│     FastAPI ligero (6 dependencias, ~12 MB)                         │
│     DeepSeek/Groq API para respuestas                               │
│     SIN torch, SIN numpy, SIN modelos locales                       │
│                                                                     │
│  3. Verificar: curl http://localhost:8000/health                    │
│  4. Verificar: curl http://localhost:8000/kb/stats                  │
└─────────────────────────────────────────────────────────────────────┘
```

## Lista final de archivos

### Scripts de ingesta (solo Windows)

| Archivo | Propósito |
|---------|-----------|
| `backend/ingest/ingest_pipeline.py` | Pipeline completo: PDFs → pgvector |
| `backend/ingest/__init__.py` | Módulo Python |

### Búsqueda semántica (Linux y Windows)

| Archivo | Propósito |
|---------|-----------|
| `backend/rag/search.py` | Búsqueda pgvector + fallback full-text |
| `backend/rag/schema.sql` | Esquema SQL para crear tabla e índices |
| `backend/rag/__init__.py` | Módulo Python |

### Requirements separados

| Archivo | Entorno | Dependencias | Tamaño |
|---------|---------|-------------|--------|
| `backend/requirements.txt` | **Linux producción** | fastapi, uvicorn, psycopg, pydantic, dotenv, apscheduler | ~12 MB |
| `backend/requirements-dev.txt` | Linux desarrollo | + pypdf, pdfplumber | ~17 MB |
| `backend/requirements-win.txt` | **Windows ingesta** | + sentence-transformers, numpy | ~1.6 GB |

### Infraestructura

| Archivo | Propósito |
|---------|-----------|
| `Dockerfile` | python:3.12-slim, pip --no-cache-dir |
| `.dockerignore` | Excluye basura, datos, ML models |
| `docker-compose.yml` | db (pgvector) + backend |

## Variables de entorno (`.env`)

```bash
# === Base de datos ===
DB_DSN=postgresql://agente:agente_pw@localhost:5433/agente_ai

# === API externa (DeepSeek o Groq) ===
GROQ_API_KEY=gsk_tu_key_aqui
GROQ_MODEL=llama-3.3-70b-versatile
DEEPSEEK_API_KEY=sk_tu_key_aqui  # Opcional, para embeddings vía API

# === Meta / WhatsApp ===
META_VERIFY_TOKEN=agente_seguros
META_PAGE_ACCESS_TOKEN=EAATu_token_aqui
META_APP_SECRET=702fa3eb69b3d99bc8ad996fcfba49ba
DEFAULT_WA_PHONE_E164=34603448765
WA_PHONE_NUMBER_ID=  # ID del número en Meta Business
META_WA_TOKEN=       # Token de WhatsApp Cloud API

# === Admin ===
KB_ADMIN_TOKEN=EAWDSK9DSF88GJAS33RG
DASHBOARD_URL=http://localhost:8000/dashboard?token=EAWDSK9DSF88GJAS33RG
```

## Comandos Windows (ingesta)

```powershell
# 1. Clonar repo
git clone https://github.com/sebsv123/agente_seguros_ai.git
cd agente-seguros

# 2. Crear entorno virtual
python -m venv .venv
.venv\Scripts\activate

# 3. Instalar dependencias (incluye sentence-transformers + numpy ~1.6GB)
pip install --no-cache-dir -r backend/requirements-win.txt

# 4. Asegurar que PostgreSQL local está corriendo en puerto 5433
#    (usar Docker Desktop o instalación nativa)

# 5. Crear carpetas para PDFs
mkdir backend\data\raw\salud
mkdir backend\data\raw\vida
mkdir backend\data\raw\dental
mkdir backend\data\raw\mascotas
mkdir backend\data\raw\decesos
mkdir backend\data\raw\autonomos
mkdir backend\data\raw\extranjeria
mkdir backend\data\raw\accidentes
mkdir backend\data\raw\juridica

# 6. COPIAR PDFs a cada carpeta
#    Ej: copiar folleto_salud.pdf a backend\data\raw\salud\

# 7. Ejecutar pipeline completo (inserta DIRECTAMENTE en pgvector)
python backend/ingest/ingest_pipeline.py --all

# 8. Verificar inserción
python -c "import psycopg; conn=psycopg.connect('postgresql://agente:agente_pw@localhost:5433/agente_ai'); cur=conn.cursor(); cur.execute('SELECT COUNT(*) FROM document_chunks'); print(f'Chunks: {cur.fetchone()[0]}')"

# 9. Opcional: exportar backup JSON
python backend/ingest/ingest_pipeline.py --all --export-json

# 10. Commit y push (solo código, NO datos)
git add backend/ingest/ backend/rag/ backend/requirements*.txt
git commit -m "feat: actualización base de conocimiento"
git push
```

## Comandos Linux (despliegue)

```bash
# 1. Clonar
git clone https://github.com/sebsv123/agente_seguros_ai.git
cd agente-seguros

# 2. Configurar .env
cp .env.example .env
nano .env  # Añadir GROQ_API_KEY, etc.

# 3. Levantar stack completo
docker compose up -d --build

# 4. Verificar que todo funciona
curl http://localhost:8000/health
curl http://localhost:8000/kb/stats

# 5. Probar búsqueda semántica
curl "http://localhost:8000/kb/search?q=seguro+de+salud&category=salud"

# 6. Ver logs
docker compose logs -f backend
```

## Sincronización Windows → Linux (pgvector)

### Opción A: Misma base de datos (recomendada)

Si Windows y Linux comparten la misma red, Windows inserta directamente en el PostgreSQL de Linux:

```powershell
# En Windows, apuntar al PostgreSQL de Linux
set DB_DSN=postgresql://agente:agente_pw@linux-ip:5432/agente_ai
python backend/ingest/ingest_pipeline.py --all
```

### Opción B: Dump/restore de PostgreSQL

```bash
# En Windows: hacer dump de la tabla document_chunks
pg_dump -h localhost -p 5433 -U agente -d agente_ai \
  --table=document_chunks --data-only --column-inserts \
  > document_chunks_dump.sql

# En Linux: restaurar
docker compose exec -T db psql -U agente -d agente_ai < document_chunks_dump.sql
```

### Opción C: JSON export/import (contingencia)

```powershell
# En Windows: exportar
python backend/ingest/ingest_pipeline.py --all --export-json
# Se genera: backend/data/exports/knowledge_base.json

# En Linux: importar (solo si no hay conexión directa)
docker compose exec backend python -m backend.rag.search \
  --import-json backend/data/exports/knowledge_base.json
```

## Verificación de que Linux NO tiene dependencias pesadas

```bash
# Listar dependencias instaladas en el contenedor
docker compose exec backend pip list

# Verificar que NO aparecen:
# - torch
# - sentence-transformers
# - numpy
# - transformers
# - pytesseract
# - Pillow

# Verificar espacio usado
docker compose exec backend du -sh /usr/local/lib/python3.12/site-packages/
# Debería ser ~50-80 MB (no 2+ GB)
```

## Secuencia exacta de despliegue real

### Día 1: Preparación en Windows

```powershell
git clone https://github.com/sebsv123/agente_seguros_ai.git
cd agente-seguros
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements-win.txt
# Colocar PDFs en backend/data/raw/<categoria>/
python backend/ingest/ingest_pipeline.py --all
git add backend/ingest/ backend/rag/ backend/requirements*.txt
git commit -m "feat: ingesta inicial conocimiento"
git push
```

### Día 2: Despliegue en Linux

```bash
git clone https://github.com/sebsv123/agente_seguros_ai.git
cd agente-seguros
cp .env.example .env
nano .env  # Configurar tokens
docker compose up -d --build
curl http://localhost:8000/health
```

### Día 3+: Mantenimiento

```powershell
# Windows: actualizar PDFs
python backend/ingest/ingest_pipeline.py --all
git add backend/ingest/ backend/rag/
git commit -m "feat: actualización conocimiento"
git push
```

```bash
# Linux: actualizar
git pull
docker compose up -d --build
```
