# Arquitectura — Agente WhatsApp/IG de Valentín Protección Integral

## Stack

| Componente | Tecnología |
|------------|-----------|
| API Server | FastAPI (Python 3.11+) |
| Base de datos | PostgreSQL 16 + pgvector |
| LLM | DeepSeek (deepseek-chat) → Groq → OpenAI fallback |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2) |
| Transcripción | Whisper API (OpenAI-compatible) |
| OCR | Tesseract + PyMuPDF |
| WhatsApp bridge | whatsapp-web.js (Node.js) |
| Túnel | cloudflared |
| Orquestación | n8n (workflows complementarios) |

## Flujo de un mensaje

```
Cliente (IG DM / WhatsApp)
    │
    ▼
Meta Graph API ──► webhook ──► /webhook/ig o /webhook/wa
    │
    ▼
process_message()
    │
    ├── 1. Rate limit check
    ├── 2. Bot silenced? (derivado a humano sin release)
    ├── 3. Reset request?
    ├── 4. Insurance intent detection
    ├── 5. RAG interrupt (pregunta de producto → KB)
    ├── 6. Slot extraction (edad, provincia, copago, etc.)
    ├── 7. Lead scoring en tiempo real
    │       └── score ≥ 8 → alerta al equipo (notifier.py)
    ├── 8. Auto-WA si datos mínimos + score ≥ 6
    ├── 9. Appointment setter (score ≥ 8)
    │       └── Google Calendar si acepta
    └── 10. Respuesta al cliente
```

## KB / RAG

- **PDFs** en `data/<categoria>/` (solo en servidor, nunca en git)
- **Ingesta**: `POST /kb/ingest` → `kb_ingest.py`
  - Extracción: PyMuPDF + fallback OCR (Tesseract)
  - Chunking: 400 chars con overlap de 80
  - Embeddings: all-MiniLM-L6-v2 → pgvector
- **Búsqueda**: cosine similarity con threshold configurable (`KB_SCORE_THRESHOLD`, default 0.55)
- **Respuesta**: LLM resume los chunks más relevantes

## Directorios

```
backend/
├── app.py                 # FastAPI principal (~3700 líneas)
├── system_prompt.py       # Prompt maestro (ES + EN)
├── agent_evaluator.py     # Evaluación de conversaciones
├── kb_ingest.py           # Ingesta de PDFs a pgvector
├── notifier.py            # Alertas WhatsApp al equipo
├── extract_product_rules.py  # Generación de playbooks desde PDFs
├── product_playbooks.json # Playbooks comerciales generados
data/                      # PDFs de producto (solo servidor)
```

## Variables de entorno

Ver `.env.example` en la raíz del proyecto.

## Endpoints principales

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/agent/respond` | Procesar mensaje de texto |
| POST | `/api/agent/voice` | Procesar audio (Whisper → texto → respond) |
| POST | `/kb/ingest` | Ingestar PDFs en la KB |
| GET | `/kb/stats` | Estadísticas de la KB |
| GET | `/kb/search` | Búsqueda semántica en la KB |
| GET | `/admin` | Panel de administración HTML |
| GET | `/health` | Health check |
| POST | `/internal/run-followups` | Ejecutar follow-ups automáticos |
