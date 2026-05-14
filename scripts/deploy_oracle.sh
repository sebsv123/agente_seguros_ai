#!/bin/bash
set -e

echo "🚀 Deploy VPI — Oracle Cloud (Madrid)"
echo "======================================="
echo "Servicios: Rosa (FastAPI) + servidor_vpi (Node) + n8n + DB + Cloudflare"
echo ""

# ── 0. Docker ──────────────────────────────────────────
if ! command -v docker &> /dev/null; then
    echo "→ Instalando Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo "✅ Docker instalado. Cierra sesión, vuelve a entrar y ejecuta este script de nuevo."
    exit 0
fi

# ── 1. Repos ───────────────────────────────────────────
BASE_DIR="/home/$USER"

if [ -d "$BASE_DIR/agente_seguros_ai" ]; then
    echo "→ Actualizando agente_seguros_ai..."
    git -C "$BASE_DIR/agente_seguros_ai" pull origin main
else
    echo "→ Clonando agente_seguros_ai..."
    git clone https://github.com/sebsv123/agente_seguros_ai.git "$BASE_DIR/agente_seguros_ai"
fi

if [ -d "$BASE_DIR/servidor_vpi" ]; then
    echo "→ Actualizando servidor_vpi..."
    git -C "$BASE_DIR/servidor_vpi" pull origin main
else
    echo "→ Clonando servidor_vpi..."
    git clone https://github.com/sebsv123/servidor_vpi.git "$BASE_DIR/servidor_vpi"
fi

# ── 2. .env checks ────────────────────────────────────
cd "$BASE_DIR/agente_seguros_ai"

if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  FALTA .env en agente_seguros_ai"
    echo "   cp .env.example .env && nano .env"
    exit 1
fi

if [ ! -f "$BASE_DIR/servidor_vpi/.env" ]; then
    echo ""
    echo "⚠️  FALTA .env en servidor_vpi"
    echo "   cd $BASE_DIR/servidor_vpi && cp .env.example .env && nano .env"
    exit 1
fi

# ── 3. Cloudflare credentials ─────────────────────────
if [ ! -f "./cloudflared/credentials.json" ]; then
    echo ""
    echo "⚠️  FALTA cloudflared/credentials.json"
    echo "   Copia el archivo desde tu servidor actual:"
    echo "   scp usuario@servidor-linux:~/.cloudflared/*.json ./cloudflared/"
    exit 1
fi

# ── 4. Directorios ────────────────────────────────────
mkdir -p data logs

# ── 5. Arrancar ───────────────────────────────────────
echo ""
echo "→ Arrancando todos los servicios..."
docker compose up -d --build

# ── 6. Esperar backend ────────────────────────────────
echo "→ Esperando al agente Rosa..."
for i in {1..24}; do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ Rosa online"
        break
    fi
    echo "   ($i/24) esperando..."
    sleep 5
done

# ── 7. Esperar n8n ────────────────────────────────────
echo "→ Esperando a n8n..."
for i in {1..12}; do
    if curl -sf http://localhost:5678/healthz > /dev/null 2>&1; then
        echo "✅ n8n online"
        break
    fi
    sleep 5
done

# ── 8. Estado final ───────────────────────────────────
echo ""
echo "======================================="
docker compose ps
echo ""
echo "→ Health checks:"
echo -n "  Rosa:   "; curl -s http://localhost:8000/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'])"
echo -n "  n8n:    "; curl -sf http://localhost:5678/healthz > /dev/null && echo "ok" || echo "error"
echo -n "  VPI:    "; curl -sf http://localhost:3000/ > /dev/null && echo "ok" || echo "error"
echo ""
echo "✅ Deploy completado"
echo ""
echo "PRÓXIMOS PASOS:"
echo "  1. Importar workflows n8n: https://n8n.valentinproteccionintegral.com"
echo "  2. Subir PDFs KB: POST /kb/sync-from-cloud o rsync ./data/"
echo "  3. Cargar KB: curl -X POST http://localhost:8000/kb/ingest -H 'X-Admin-Token: TOKEN' -d '{}'"
echo "  4. Verificar KB: curl http://localhost:8000/kb/stats"
