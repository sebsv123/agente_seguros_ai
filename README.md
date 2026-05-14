<div align="center">

# 🤖 Agente Rosa

### AI lead capture agent for insurance brokers — Instagram DM, web widget, and WhatsApp handoff. Fully self-hostable.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

</div>

---

## 🤔 What is this?

Agente Rosa is a conversational AI agent that qualifies insurance leads automatically. A prospect messages on Instagram or clicks your web widget — Rosa engages them, scores the lead using RAG over your product catalog, and when the score is high enough, fires a WhatsApp handoff to a human agent.

No cloud lock-in. No per-message SaaS fees. Runs entirely on your own infra with one `docker compose up`.

---

## ✨ How it works

```
Instagram DM / Web Widget
        │
        ▼
   FastAPI backend  ◄── RAG (pgvector + sentence-transformers)
        │
        ▼
   Lead scoring (LLM)
        │
   score ≥ threshold?
     ├── YES ──►  WhatsApp handoff (auto or manual)
     └── NO  ──►  Continue conversation / nurture
        │
        ▼
  n8n workflow → CRM / Notion / email
```

---

## 🛠️ Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.11 · FastAPI · psycopg3 |
| **Database** | PostgreSQL 16 + pgvector |
| **RAG** | sentence-transformers (product catalog embeddings) |
| **LLM** | OpenAI GPT-4o-mini · Groq Llama 3.3 (swappable via env) |
| **Automation** | n8n (lead intake workflows, CRM push) |
| **Tunnel** | Cloudflare Tunnel (no exposed ports) |
| **Frontend** | Next.js web · floating HTML widget |
| **Infra** | Docker Compose · Cloudflare Zero Trust |

---

## 🚀 Quick Start

```bash
git clone https://github.com/sebsv123/agente_seguros_ai.git
cd agente_seguros_ai
cp .env.example .env   # fill in your keys (see table below)
docker compose up -d
```

Then start the backend:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Health check:

```bash
curl http://localhost:8000/health
# {"ok": true, "agent": "Rosa", "llm_available": true, ...}
```

---

## ⚙️ Configuration

Minimum required variables in `.env`:

```env
# Database
POSTGRES_PASSWORD=your_password

# LLM — pick one
OPENAI_API_KEY=sk-...
# or
GROQ_API_KEY=gsk-...

# Meta (Instagram webhook)
META_VERIFY_TOKEN=any_secret_you_choose
META_PAGE_ACCESS_TOKEN=from_meta_developer_portal
META_APP_SECRET=from_meta_developer_portal

# Cloudflare Tunnel
CLOUDFLARE_TUNNEL_TOKEN=eyJh...

# WhatsApp handoff
DEFAULT_WA_PHONE_E164=34600000000
```

Full reference: [`.env.example`](.env.example)

---

## 📱 Web Widget

Drop `rosa_widget.html` into any webpage. It renders a floating button that opens an Instagram DM directly with your agent.

See [`WIDGET_GUIDE.md`](WIDGET_GUIDE.md) for embedding instructions.

---

## 🧱 Architecture

See [`ARQUITECTURA.md`](ARQUITECTURA.md) for the full system diagram.

```
agente_seguros_ai/
├── backend/
│   ├── app.py              # FastAPI agent core
│   ├── requirements.txt
│   ├── ingest_pdfs.py      # Ingest product PDFs into KB
│   └── ingest_salud.py     # Ingest health product data
├── web/                    # Next.js frontend
├── n8n_extracted/          # n8n workflow exports
├── docker-compose.yml      # db + n8n + cloudflared
├── cloudflared.yml         # Tunnel config
├── rosa_widget.html        # Embeddable web widget
└── .env.example
```

---

## 🤝 Contributing

PRs welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## 📝 License

MIT

---

<div align="center">

*Built for insurance brokers who want AI that works on their terms.*

</div>
