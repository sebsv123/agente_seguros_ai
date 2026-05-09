#!/bin/bash
set -e

echo "🚀 Deploy Agente Rosa — Oracle Cloud"
echo "======================================"

# 1. Actualizar sistema
echo "→ Actualizando sistema..."
sudo apt-get update -qq && sudo apt-get upgrade -y -qq

# 2. Instalar Docker si no está
if ! command -v docker &> /dev/null; then
    echo "→ Instalando Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo "⚠️  Cierra sesión y vuelve a entrar para aplicar grupo docker"
    exit 0
fi

# 3. Clonar o actualizar repo
REPO_DIR="/home/$USER/agente_seguros_ai"
if [ -d "$REPO_DIR" ]; then
    echo "→ Actualizando repo..."
    cd "$REPO_DIR" && git pull origin main
else
    echo "→ Clonando repo..."
    git clone https://github.com/sebsv123/agente_seguros_ai.git "$REPO_DIR"
    cd "$REPO_DIR"
fi

# 4. Verificar .env
if [ ! -f ".env" ]; then
    echo "⚠️  Falta el archivo .env"
    echo "   Copia .env.example a .env y rellena las variables:"
    echo "   cp .env.example .env && nano .env"
    exit 1
fi

# 5. Crear directorios necesarios
mkdir -p data logs

# 6. Arrancar servicios
echo "→ Arrancando servicios..."
docker compose pull 2>/dev/null || true
docker compose up -d --build

# 7. Esperar a que el backend esté listo
echo "→ Esperando a que el agente esté listo..."
for i in {1..20}; do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ Agente online"
        break
    fi
    echo "   Intento $i/20..."
    sleep 5
done

# 8. Estado final
echo ""
echo "======================================"
docker compose ps
echo ""
curl -s http://localhost:8000/health | python3 -m json.tool
echo ""
echo "✅ Deploy completado"
echo "   Health:    http://localhost:8000/health"
echo "   KB Stats:  http://localhost:8000/kb/stats"
echo "   Dashboard: http://localhost:8000/admin"
