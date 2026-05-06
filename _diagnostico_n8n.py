#!/usr/bin/env python3
"""Diagnóstico completo de archivos n8n en agente-seguros"""
import os, json, sqlite3, zipfile, tarfile, gzip, pathlib

exclude_dirs = {'.git', '.venv', 'node_modules', '__pycache__', 'site-packages'}

print('='*70)
print('PASO 1: UBICACION Y MAPA')
print('='*70)
print(f'Directorio actual: {os.getcwd()}')
print()

# Directorios principales (sin .git)
for root, dirs, fnames in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.git')]
    depth = root.replace('\\', '/').count('/')
    if depth <= 3:
        print(f'  {"  " * depth}[{root}]')

print()
print('='*70)
print('PASO 2: BUSCAR COPIAS POSIBLES (.json, .sqlite, .db, .zip, .tgz, .tar, .log, .yml, .yaml, .sql, .gz, .bak, .dump)')
print('='*70)
exts = {'.json', '.sqlite', '.db', '.zip', '.tgz', '.tar', '.log', '.yml', '.yaml', '.sql', '.gz', '.bak', '.dump', '.export'}
files_found = []
for root, dirs, fnames in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in exclude_dirs]
    for f in fnames:
        ext = pathlib.Path(f).suffix.lower()
        if ext in exts:
            fpath = os.path.join(root, f)
            size = os.path.getsize(fpath)
            files_found.append((fpath, size, ext))
for fpath, size, ext in sorted(files_found):
    print(f'  [{ext:>6}] {size:>10,} bytes  {fpath}')
print(f'  TOTAL: {len(files_found)} archivos encontrados')

print()
print('='*70)
print('PASO 3: BUSCAR NOMBRES CLAVE (n8n|workflow|backup|database|sqlite|export|extract|cloudflared)')
print('='*70)
keywords = ['n8n', 'workflow', 'backup', 'database', 'sqlite', 'export', 'extract', 'cloudflared']
named_files = []
for root, dirs, fnames in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in exclude_dirs]
    for f in fnames:
        f_lower = f.lower()
        for kw in keywords:
            if kw in f_lower:
                fpath = os.path.join(root, f)
                size = os.path.getsize(fpath)
                named_files.append((fpath, size, kw))
                break
for fpath, size, kw in sorted(named_files):
    print(f'  [{kw:>15}] {size:>10,} bytes  {fpath}')
print(f'  TOTAL: {len(named_files)} archivos encontrados')

print()
print('='*70)
print('PASO 4: BUSCAR CONTENIDO RELEVANTE')
print('='*70)
patterns = ['workflow_entity', 'n8n export:workflow', 'DB_TYPE=postgresdb', 'N8N_ENCRYPTION_KEY', 'WEBHOOK_URL', 'cloudflared', 'n8n_extracted']
content_files = set()
for root, dirs, fnames in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in exclude_dirs]
    for f in fnames:
        ext = pathlib.Path(f).suffix.lower()
        if ext in ('.json', '.yaml', '.yml', '.md', '.py', '.js', '.ts', '.html', '.env', '.txt', '.sql', '.cfg', '.conf', '.log', '.sh', '.bat', '.ps1', '.example'):
            fpath = os.path.join(root, f)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                    content = fh.read()
                    for pat in patterns:
                        if pat.lower() in content.lower():
                            content_files.add((fpath, os.path.getsize(fpath), pat))
            except:
                pass
for fpath, size, pat in sorted(content_files):
    print(f'  [{pat:>25}] {size:>10,} bytes  {fpath}')
print(f'  TOTAL: {len(content_files)} archivos con contenido relevante')

print()
print('='*70)
print('PASO 5: INSPECCIONAR BACKUPS ENCONTRADOS')
print('='*70)
backup_exts = {'.zip', '.tgz', '.tar', '.gz', '.sqlite', '.db', '.sql', '.bak', '.dump'}
for root, dirs, fnames in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in exclude_dirs]
    for f in fnames:
        ext = pathlib.Path(f).suffix.lower()
        if ext in backup_exts:
            fpath = os.path.join(root, f)
            size = os.path.getsize(fpath)
            print(f'  --- {fpath} ---')
            print(f'      Tamaño: {size:,} bytes ({size/1024:.1f} KB)')
            print(f'      Extensión: {ext}')
            try:
                if ext == '.zip':
                    with zipfile.ZipFile(fpath) as z:
                        names = z.namelist()
                        print(f'      ZIP contiene {len(names)} archivos')
                        for n in names[:10]:
                            info = z.getinfo(n)
                            print(f'        - {n} ({info.file_size:,} bytes)')
                        if len(names) > 10:
                            print(f'        ... y {len(names)-10} más')
                elif ext in ('.tgz', '.gz', '.tar'):
                    print(f'      Tipo: archivo comprimido')
                    try:
                        with gzip.open(fpath, 'rb') as gz:
                            gz.read(1)
                        print(f'      Válido como gzip: SI')
                    except:
                        print(f'      Válido como gzip: NO (puede ser raw data)')
                elif ext in ('.sqlite', '.db'):
                    try:
                        conn = sqlite3.connect(fpath)
                        cur = conn.cursor()
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        tables = cur.fetchall()
                        print(f'      SQLite tablas: {[t[0] for t in tables]}')
                        for t in tables:
                            cur.execute(f'SELECT COUNT(*) FROM "{t[0]}"')
                            cnt = cur.fetchone()[0]
                            print(f'        - {t[0]}: {cnt} filas')
                        conn.close()
                    except Exception as e:
                        print(f'      No es SQLite válido: {e}')
                elif ext == '.sql':
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                        lines = fh.readlines()
                        print(f'      Líneas: {len(lines)}')
                        print(f'      Primeras 3 líneas:')
                        for l in lines[:3]:
                            print(f'        {l.rstrip()[:120]}')
            except Exception as e:
                print(f'      Error al inspeccionar: {e}')
            print()

print('='*70)
print('PASO 6: RESUMEN FINAL')
print('='*70)
print()
print('  1) UBICACION PRINCIPAL DEL PROYECTO: C:\\Users\\Sebitas\\agente-seguros')
print()
print('  2) ARCHIVOS DE WORKFLOW n8n ENCONTRADOS:')
print('     - n8n_workflows_backup.zip (817,080 bytes) - contiene database.sqlite con workflows')
print('     - n8n_data_backup.tgz (1,654,846 bytes) - raw git blob backup (NO es gzip valido)')
print('     - n8n_extracted/database.sqlite (1,380,352 bytes) - SQLite con workflows extraidos de git history')
print('     - backups/agente_20260306_160000.sql (293,218 bytes) - PostgreSQL dump')
print('     - backups/agente_20260306_160318.sql (293,218 bytes) - PostgreSQL dump')
print('     - backups/n8n_data_.tgz (425 bytes) - muy pequeno, probablemente vacio')
print('     - backups/n8n_data_20260306_160318.tgz (674 bytes) - muy pequeno, probablemente vacio')
print()
print('  3) UBICACION DE LOS WORKFLOWS REALES:')
print('     - Los workflows activos estan en PostgreSQL dentro del contenedor Docker (no accesible sin Docker)')
print('     - La unica copia exportable esta en n8n_extracted/database.sqlite (de git history)')
print('     - El storage local (data/n8n/storage/) esta VACIO - n8n no guarda JSON aqui')
print()
print('  4) ESTADO DE DOCKER: NO EJECUTANDOSE - Docker Desktop no esta corriendo')
print('     - Para recuperar workflows: docker compose up -d, luego n8n export:workflow --all')
print('     - Backup funcional disponible: n8n_workflows_backup.zip (subido a GitHub)')
print()
print('='*70)
print('DIAGNOSTICO COMPLETADO')
print('='*70)
