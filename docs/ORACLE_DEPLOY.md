# Deploy en Oracle Cloud (OCI)

## Infraestructura recomendada

| Componente | Servicio OCI | Notas |
|-----------|-------------|-------|
| Servidor agente | Compute VM (AMD, 1 OCPU, 6GB RAM) | Always Free tier |
| Base de datos | PostgreSQL en VM (con pgvector) | O Autonomous DB |
| Archivos PDFs KB | Object Storage (bucket privado) | Sin límite práctico |
| Dominio/SSL | Cloudflare (ya configurado) | cloudflared tunnel |
| Secrets | OCI Vault o .env en servidor | Variables de entorno |

## Pasos de deploy

### 1. Crear VM en OCI
- Shape: VM.Standard.A1.Flex (ARM) o VM.Standard.E2.1.Micro (AMD)
- OS: Ubuntu 22.04
- Abrir puertos: 22 (SSH), 8000 (backend), 443 (Cloudflare)

### 2. Instalar Docker en la VM
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu
```

### 3. Clonar repo y configurar
```bash
git clone https://github.com/sebsv123/agente_seguros_ai.git
cd agente_seguros_ai
cp .env.example .env
nano .env   # Rellenar todas las variables
```

### 4. Subir PDFs de pólizas
```bash
# Desde tu portátil local:
rsync -avz ./data/ ubuntu@IP_ORACLE:/home/ubuntu/agente_seguros_ai/data/
```

### 5. Arrancar el agente
```bash
docker compose up -d --build
docker compose logs -f backend
```

### 6. Verificar que funciona
```bash
curl http://localhost:8000/health
curl http://localhost:8000/kb/stats
```

### 7. Cargar KB completa
```bash
curl -X POST http://localhost:8000/kb/ingest \
  -H "X-Admin-Token: TU_KB_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### 8. Activar Cloudflare Tunnel
```bash
cloudflared tunnel run agente-rosa
```

## Healthcheck Oracle Load Balancer
- URL: /health
- Protocolo: HTTP
- Puerto: 8000
- Respuesta esperada: 200 OK
- Intervalo: 30s

## Variables críticas para producción
