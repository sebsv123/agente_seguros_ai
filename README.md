# 🤖 Agente Seguros AI

**Agente de ventas automatizado para seguros** — Pre-cualifica leads desde Instagram, los guía con un flujo conversacional inteligente, y los entrega "casi cerrados" por WhatsApp.

> 🇪🇸 Diseñado para el mercado español de seguros. Toda la interacción en castellano.

---

## 📐 Arquitectura

```
┌─────────────────────────────────────────────────────────────────┐
│                     FLUJO DEL CLIENTE                           │
│                                                                 │
│  📱 Instagram Ads                                               │
│       │                                                         │
│       ▼                                                         │
│  DM Automation (Meta Messaging API)                             │
│       │                                                         │
│       ▼                                                         │
│  Pre-cualificación (Flow conversacional)                        │
│       │                                                         │
│       ├─── Scoring heurístico (v0)                              │
│       │    └── (→ Random Forest cuando haya datos)              │
│       │                                                         │
│       ▼                                                         │
│  RAG (Retrieval-Augmented Generation)                           │
│       │  embeddings MiniLM-L6-v2 + pgvector                    │
│       │                                                         │
│       ▼                                                         │
│  Handoff → WhatsApp (wa.me link con mensaje prefilled)          │
│       │                                                         │
│       ▼                                                         │
│  🟢 Lead cerrado o casi cerrado llega al WhatsApp corporativo   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🧩 Componentes

| Componente | Tecnología | Descripción |
|---|---|---|
| **API Core** | FastAPI (Python) | Motor principal del agente |
| **Base de datos** | PostgreSQL + pgvector | Leads, conversaciones, knowledge base con embeddings |
| **Embeddings** | `sentence-transformers/all-MiniLM-L6-v2` | Vectorización de documentos para RAG |
| **Instagram** | Meta Messaging API (Webhook) | Recibe y responde DMs automáticamente |
| **WhatsApp** | wa.me deep links (prefilled) | Handoff con mensaje pre-construido |
| **Scoring** | Heurístico v0 → Random Forest (futuro) | Probabilidad de cierre del lead |
| **Ingesta** | `ingest_salud.py` + pypdf | Extracción y chunking de PDFs de productos |

---

## 🗂️ Estructura del Proyecto

```
agente_seguros_ai/
├── app.py                 # API principal (FastAPI)
├── ingest_salud.py        # Script de ingesta de PDFs → embeddings
├── requirements.txt       # Dependencias Python
├── .env.example           # Plantilla de variables de entorno
├── .gitignore
├── db/
│   └── schema.sql         # Schema PostgreSQL + pgvector
├── data/                  # PDFs de productos (no versionados)
│   └── *.pdf
└── README.md
```

---

## 🚀 Instalación y Setup

### 1. Requisitos previos

- Python 3.11+
- PostgreSQL 15+ con [pgvector](https://github.com/pgvector/pgvector)
- Cuenta Meta Business con Instagram Messaging API configurada

### 2. Clonar y configurar

```bash
git clone https://github.com/sebsv123/agente_seguros_ai.git
cd agente_seguros_ai

# Crear entorno virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env
# Edita .env con tus credenciales reales
```

### 3. Base de datos

```bash
# Crear la base de datos
createdb agente_ai

# Ejecutar el schema
psql -d agente_ai -f db/schema.sql
```

### 4. Ingestar documentos de productos

Coloca los PDFs en `data/` y ejecuta:

```bash
python ingest_salud.py
```

Esto extrae el texto, lo divide en chunks, genera embeddings y los almacena en `kb_documents`.

### 5. Arrancar el servidor

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

La API estará disponible en `http://localhost:8000`.

---

## 📡 Endpoints

### Health Check
```
GET /health → {"ok": true}
```

### RAG (búsqueda semántica)
```
POST /rag/ask
{
  "question": "¿Qué cubre la hospitalización?",
  "category": "salud",
  "route": "R3",
  "top_k": 5
}
```

### Lead Management
```
POST /lead/upsert     → Crear lead
POST /conversation/log → Registrar mensaje
```

### Flow Conversacional
```
POST /flow/next    → Siguiente pregunta del flujo
POST /flow/submit  → Enviar respuesta del usuario
```

### Quote Preview
```
POST /quote/preview → Genera mensaje de propuesta con coberturas
```

### Handoff (IG → WA)
```
POST /handoff/pack          → Resumen completo del lead
POST /handoff/next-message  → Siguiente mensaje a enviar
POST /handoff/wa-link       → Genera link wa.me con mensaje prefilled
```

### Meta Webhook
```
GET  /meta/webhook  → Verificación del webhook de Meta
POST /meta/webhook  → Recepción de mensajes de Instagram
```

---

## 🔄 Flujo Conversacional (Flow)

El agente guía al usuario paso a paso:

```
1. Provincia
2. Nº de asegurados
3. Edades
4. ¿Hospitalización? (sí/no)
5. ¿Libre elección / reembolso? (sí/no)
6. Preferencia de copago
7. ¿España o también fuera?
8. Urgencia
9. Presupuesto
10. ¿Preexistencias? (sí/no)
    10a. Detalle
    10b. ¿Controlado/estable?
    10c. ¿Medicación o pruebas recientes?
```

Al completar → **routing automático** a una de 5 rutas de producto:

| Ruta | Tipo de producto |
|------|-----------------|
| R1 | Ambulatorio (consultas y pruebas) |
| R2 | Pago por uso (cuota baja + copago) |
| R3 | Completo con hospitalización |
| R4 | Reembolso (libre elección) |
| R5 | Internacional |

---

## 🧠 Scoring de Cierre

### v0 (actual): Heurístico

Basado en reglas simples:
- +20% si urgencia = hoy/esta semana
- +10% si presupuesto medio/alto
- +10% si ruta R3/R4
- +6% si sin copago
- -10% si preexistencias

### v1 (próximo): Random Forest

Cuando haya suficientes datos de conversiones:
- `scikit-learn.RandomForestClassifier`
- Target: cerrado (1) / no cerrado (0)
- Features: todas las del perfil + métricas de interacción
- Threshold ajustable para definir "lead caliente"

---

## 📚 RAG (Retrieval-Augmented Generation)

- Los PDFs se procesan con `pypdf`
- Se dividen en chunks de ~1200 caracteres con overlap de 200
- Se generan embeddings con `all-MiniLM-L6-v2` (384 dimensiones)
- Se almacenan en PostgreSQL con `pgvector`
- Las búsquedas usan distancia coseno con índice IVFFlat
- Se filtran por `category` y opcionalmente por `route`

---

## 🔒 Seguridad

- **Variables de entorno**: Todas las credenciales via `.env` (nunca hardcodeadas)
- **Firma Meta**: Validación opcional de `X-Hub-Signature-256`
- **Sanitización**: Los textos del RAG se limpian de marcas comerciales y datos personales
- **`.gitignore`**: Excluye `.env`, PDFs y modelos

---

## 🗺️ Roadmap

- [x] Fase 0: Base de datos de productos (RAG) — Salud
- [x] Fase 1: Flow conversacional + pre-cualificación
- [x] Fase 2: Integración Instagram DM (webhook Meta)
- [x] Fase 3: Handoff IG → WhatsApp (wa.me prefilled)
- [ ] Fase 4: Scoring ML (Random Forest) con datos reales
- [ ] Fase 5: WhatsApp Business API bidireccional
- [ ] Fase 6: CRM + Dashboard
- [ ] Fase 7: Meta Conversions API (CAPI) + retargeting
- [ ] Fase 8: Categorías adicionales (8 más pendientes)
- [ ] Fase 9: Despliegue en Cloudflare Workers + n8n

---

## 🏗️ Stack de Producción (planificado)

```
Cloudflare Workers / Tunnel → FastAPI (uvicorn)
                                  │
                           PostgreSQL + pgvector
                                  │
                           n8n (orquestación)
                                  │
                    ┌─────────────┼─────────────┐
                    │             │              │
              Meta APIs    WhatsApp API    ML Scoring
```

---

## 📝 Notas

- El proyecto está en **desarrollo activo**
- Se irán añadiendo las **8 categorías restantes** de producto
- El sistema de scoring evolucionará de heurístico → ML supervisado
- El multilogin en desarrollo puede integrarse como capa de gestión de sesiones

---

## 📄 Licencia

Uso privado. Todos los derechos reservados.