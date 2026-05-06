# 🤖 Agente Rosa — Lead Capture AI para Seguros

Agente de IA conversacional que capta leads de seguros a través de Instagram DM y web, con flujo automatizado de calificación, seguimiento y derivación a WhatsApp.

## Stack

| Componente | Tecnología |
|---|---|
| **Backend** | Python / FastAPI + psycopg3 + pgvector |
| **Base de datos** | PostgreSQL 16 + pgvector |
| **Orquestación** | n8n (workflows de lead intake) |
| **Túnel** | Cloudflare Tunnel (sin exponer puertos) |
| **Widget web** | Botón flotante → Instagram DM |
| **LLM** | OpenAI GPT-4o-mini / Groq Llama 3.3 (configurable) |
| **Embeddings** | sentence-transformers (RAG sobre productos) |

## Requisitos

- [Docker](https://docs.docker.com/engine/install/) + [Docker Compose](https://docs.docker.com/compose/install/) (plugin incluido en Docker Desktop)
- [Git](https://git-scm.com/)
- [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) (para el túnel)

## Instalación paso a paso

### 1. Clonar el repositorio

```bash
git clone https://github.com/sebsv123/agente_seguros_ai.git
cd agente_seguros_ai
```

### 2. Configurar variables de entorno

Copia el archivo de ejemplo y edítalo con tus valores reales:

```bash
cp .env.example .env
nano .env   # o vim, o cualquier editor
```

> **Importante:** `.env` está en `.gitignore`. Nunca se sube al repositorio.

### 3. Arrancar los servicios

```bash
docker compose up -d
```

Esto levanta:
- **PostgreSQL + pgvector** (`db`) — base de datos del agente y de n8n
- **n8n** — orquestación de flujos de leads
- **Cloudflare Tunnel** (`cloudflared`) — expone n8n de forma segura

### 4. Verificar que todo está corriendo

```bash
docker compose ps
```

Debes ver los tres servicios con estado `Up`.

### 5. Arrancar el backend (fuera de Docker)

El backend FastAPI se ejecuta directamente en el host (no containerizado aún):

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
python app.py
```

El backend arranca en `http://localhost:8000`.

> **Nota:** En Linux, necesitarás tener instalado Tesseract OCR (`sudo apt install tesseract-ocr`) si usas la funcionalidad de escaneo de documentos.

## Variables de entorno

| Variable | Descripción | Dónde conseguirla |
|---|---|---|
| `POSTGRES_PASSWORD` | Contraseña de la base de datos | La que tú quieras |
| `DB_POSTGRESDB_PASSWORD` | Contraseña para n8n (misma que POSTGRES_PASSWORD) | La que tú quieras |
| `N8N_ENCRYPTION_KEY` | Clave de cifrado de n8n | `openssl rand -hex 32` |
| `CLOUDFLARE_TUNNEL_TOKEN` | Token del túnel Cloudflare | Cloudflare Zero Trust → Tunnels |
| `META_VERIFY_TOKEN` | Token de verificación del webhook de Meta | El que tú inventes |
| `META_PAGE_ACCESS_TOKEN` | Token de acceso de la página de Facebook | Meta Developer → Instagram API |
| `META_APP_SECRET` | App Secret de la app de Meta | Meta Developer → Apps |
| `META_SIGNATURE_MODE` | `"dev"` (sin verificar) o `"prod"` (con firma HMAC) | Según entorno |
| `DEFAULT_WA_PHONE_E164` | Teléfono por defecto para WhatsApp (ej: `34603448765`) | Tu número |
| `WA_TOKEN` | Token de autenticidad para WhatsApp link | El que tú inventes |
| `OPENAI_API_KEY` | API Key de OpenAI (opcional, para LLM) | [platform.openai.com](https://platform.openai.com) |
| `OPENAI_MODEL` | Modelo de OpenAI (ej: `gpt-4o-mini`) | Por defecto `gpt-4o-mini` |
| `GROQ_API_KEY` | API Key de Groq (alternativa a OpenAI) | [console.groq.com](https://console.groq.com) |
| `GROQ_MODEL` | Modelo de Groq (ej: `llama-3.3-70b-versatile`) | Por defecto `llama-3.3-70b-versatile` |
| `N8N_WEBHOOK_URL` | URL del webhook de n8n para leads | `https://n8n.tudominio.com/webhook/lead` |
| `N8N_TIMEOUT` | Timeout en segundos para llamadas a n8n | Por defecto `8` |
| `DB_DSN` | DSN de conexión a PostgreSQL | `postgresql://agente:${POSTGRES_PASSWORD}@localhost:5433/agente` |
| `KB_ADMIN_TOKEN` | Token opcional para proteger endpoints KB | El que tú quieras |
| `AUTO_WA_ENABLED` | Activar envío automático de WhatsApp (`1`/`0`) | Por defecto `1` |
| `AUTO_WA_SCORE_THRESHOLD` | Puntuación mínima para auto-enviar WhatsApp | Por defecto `0.7` |

## Configurar Cloudflare Tunnel

### 1. Instalar cloudflared

```bash
# Linux (amd64)
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# macOS
brew install cloudflare/cloudflare/cloudflared

# Windows — descarga el .exe desde https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
```

### 2. Autenticarse

```bash
cloudflared tunnel login
```

Se abrirá el navegador. Inicia sesión en Cloudflare y selecciona el dominio.

### 3. Crear el túnel

```bash
cloudflared tunnel create agente-rosa
```

Esto genera un `TUNNEL_TOKEN` y un archivo JSON de credenciales en `~/.cloudflared/`.

### 4. Configurar el túnel

El repositorio ya incluye un `cloudflared.yml` con la configuración de ingreso. Asegúrate de que apunte a tu servicio:

```yaml
ingress:
  - hostname: n8n.tudominio.com
    service: http://n8n:5678
  - service: http_status:404
```

### 5. Asignar DNS

```bash
cloudflared tunnel route dns agente-rosa n8n.tudominio.com
```

### 6. Configurar el token en `.env`

Copia el token del túnel desde Cloudflare Zero Trust → Tunnels → agente-rosa → `TUNNEL_TOKEN` y pégalo en tu `.env`:

```
CLOUDFLARE_TUNNEL_TOKEN=eyJh... (token largo)
```

### 7. El túnel arranca automáticamente con Docker

El servicio `cloudflared` en `docker-compose.yml` ya está configurado para usar el token y el archivo de configuración.

## Verificar que funciona

### Health check del backend

```bash
curl http://localhost:8000/health
```

Respuesta esperada:

```json
{
  "ok": true,
  "agent": "Rosa",
  "llm_available": true,
  "embedder_available": true,
  "kb_data_dir": "./data",
  "auto_wa_enabled": true,
  "auto_wa_threshold": 0.7
}
```

### Verificar la base de datos

```bash
docker compose exec db pg_isready -U agente -d agente
```

### Verificar n8n

```bash
curl http://localhost:5678/healthz
```

### Verificar el túnel

```bash
cloudflared tunnel info agente-rosa
```

## Mantenimiento

### Ver logs

```bash
# Todos los servicios
docker compose logs -f

# Un servicio específico
docker compose logs -f n8n
docker compose logs -f db
docker compose logs -f cloudflared
```

### Reiniciar un servicio

```bash
docker compose restart n8n
```

### Actualizar el proyecto

```bash
git pull
docker compose up -d --build
```

### Backup de la base de datos

```bash
docker compose exec db pg_dump -U agente agente > backup_$(date +%Y%m%d_%H%M%S).sql
```

### Detener todo

```bash
docker compose down
```

Para detener y eliminar volúmenes (¡cuidado! borra todos los datos):

```bash
docker compose down -v
```

## Estructura del proyecto

```
agente_seguros_ai/
├── backend/
│   ├── app.py              # API FastAPI (el agente)
│   ├── requirements.txt    # Dependencias Python
│   ├── ingest_pdfs.py      # Script para ingerir PDFs en la KB
│   └── ingest_salud.py     # Script para ingerir datos de salud
├── web/
│   └── src/app/page.tsx    # Frontend web (Next.js)
├── docker-compose.yml      # Servicios: db, n8n, cloudflared
├── cloudflared.yml         # Configuración del túnel
├── rosa_widget.html        # Widget flotante para la web
├── .env.example            # Plantilla de variables de entorno
└── README.md               # Este archivo
```

## Widget web

Incluye un widget flotante (`rosa_widget.html`) que puedes incrustar en cualquier web. Al hacer clic, abre Instagram DM directamente con el agente.

Ver `WIDGET_GUIDE.md` para instrucciones detalladas de integración.
