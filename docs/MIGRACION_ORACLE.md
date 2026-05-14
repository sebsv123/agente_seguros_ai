# Checklist Migración a Oracle Cloud

## Pre-migración (hacer ANTES en servidor Linux actual)

- [ ] Exportar workflows n8n: n8n → Settings → Export all workflows → guardar JSON
- [ ] Copiar credentials Cloudflare Tunnel:
      scp -r ~/.cloudflared/ oracle_vm:~/agente_seguros_ai/cloudflared/
- [ ] Copiar PDFs de pólizas:
      rsync -avz ./data/ oracle_vm:~/agente_seguros_ai/data/
- [ ] Copiar .env del agente:
      scp .env oracle_vm:~/agente_seguros_ai/.env
- [ ] Copiar .env del servidor VPI:
      scp ../servidor_vpi/.env oracle_vm:~/servidor_vpi/.env
- [ ] Hacer backup de la DB:
      docker exec db pg_dump -U agente agente_ai > backup_$(date +%Y%m%d).sql

## En Oracle VM

- [ ] Ejecutar scripts/deploy_oracle.sh
- [ ] Importar workflows n8n desde la UI
- [ ] Cargar KB: POST /kb/ingest
- [ ] Verificar /health y /kb/stats

## Post-migración

- [ ] Verificar que Cloudflare Tunnel apunta a Oracle (no al servidor viejo)
- [ ] Apagar servidor Linux antiguo (después de 24-48h de pruebas)
- [ ] Actualizar DNS si es necesario

## Comandos útiles en Oracle

```bash
# Ver logs en tiempo real
docker compose logs -f backend
docker compose logs -f n8n

# Reiniciar un servicio
docker compose restart backend

# Actualizar código
git pull && docker compose up -d --build backend

# Backup DB en Oracle
docker exec db pg_dump -U agente agente_ai > backup_oracle_$(date +%Y%m%d).sql
```
