#!/usr/bin/env python3
"""
Optimización completa del proyecto para portátil modesto.

1. Fix f-string syntax error en app.py (emoji en f-string)
2. Hacer imports pesados opcionales (try/except)
3. Crear requirements.txt mínimo de producción
4. Crear requirements-dev.txt para desarrollo
5. Crear Dockerfile optimizado (python:slim)
6. Crear .dockerignore
7. Verificar docker-compose.yml existente
"""

import os
import re

# ============================================================
# 1. FIX F-STRING SYNTAX ERROR en app.py
# ============================================================

APP_PY = 'backend/app.py'

with open(APP_PY, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix: emoji 🔔 en f-string multilínea (línea 611 aprox)
# Cambiar f"🔔 Lead..." por "🔔 Lead..." (no necesita f-string)
old_fstring = '''    mensaje = (
        f"\U0001f514 Lead listo para asesoría\n"
        f"Nombre: {datos.get('nombre', '?')}\n"
        f"Producto: {producto}\n"
        f"Datos: {json.dumps(datos, ensure_ascii=False)}\n"
        f"Último mensaje: {ultimo_texto[:100]}\n"
        f"Responder: wa.me/{sender_id}"
    )'''

new_fstring = '''    mensaje = (
        "\\U0001f514 Lead listo para asesoría\\n"
        f"Nombre: {datos.get('nombre', '?')}\\n"
        f"Producto: {producto}\\n"
        f"Datos: {json.dumps(datos, ensure_ascii=False)}\\n"
        f"Último mensaje: {ultimo_texto[:100]}\\n"
        f"Responder: wa.me/{sender_id}"
    )'''

if old_fstring in content:
    content = content.replace(old_fstring, new_fstring)
    print("[OK] F-string syntax error fixed (emoji)")
else:
    print("[WARN] F-string pattern not found - may already be fixed")

# ============================================================
# 2. HACER IMPORTS PESADOS OPCIONALES
# ============================================================

# pytesseract y PIL -> opcionales (solo para OCR de documentos)
old_ocr_imports = '''import pytesseract
from PIL import Image
from io import BytesIO'''

new_ocr_imports = '''try:
    import pytesseract
    _HAS_TESSERACT = True
except Exception:
    pytesseract = None  # type: ignore
    _HAS_TESSERACT = False

try:
    from PIL import Image
    from io import BytesIO
    _HAS_PIL = True
except Exception:
    Image = None  # type: ignore
    BytesIO = None  # type: ignore
    _HAS_PIL = False'''

if old_ocr_imports in content:
    content = content.replace(old_ocr_imports, new_ocr_imports)
    print("[OK] pytesseract y PIL ahora son opcionales")
else:
    print("[WARN] OCR imports not found")

# ============================================================
# 3. CREAR requirements.txt MÍNIMO DE PRODUCCIÓN
# ============================================================

REQ_PROD = """# ====== Producción mínima ======
# Solo lo necesario para arrancar el backend FastAPI
fastapi==0.115.6
uvicorn[standard]==0.32.1
psycopg[binary]==3.2.3
pydantic==2.10.3
python-dotenv==1.0.1
apscheduler==3.10.4

# ====== Opcionales (no críticos para arranque) ======
# pypdf          # Extracción de texto de PDFs (solo para ingest)
# pdfplumber     # Alternativa a pypdf
# sentence-transformers  # NO INSTALAR: requiere torch (~2GB)
# numpy          # NO INSTALAR: solo necesario con sentence-transformers
# pytesseract    # NO INSTALAR: OCR local, requiere Tesseract binario
# Pillow         # NO INSTALAR: solo para OCR
"""

with open('backend/requirements.txt', 'w', encoding='utf-8') as f:
    f.write(REQ_PROD)
print("[OK] requirements.txt mínimo de producción creado")

# ============================================================
# 4. CREAR requirements-dev.txt
# ============================================================

REQ_DEV = """# ====== Desarrollo / Extracción de PDFs ======
-r requirements.txt

# Extracción de texto de PDFs
pypdf==5.1.0
pdfplumber==0.11.4

# OCR (opcional, requiere Tesseract binario)
# pytesseract
# Pillow

# Embeddings locales (NO INSTALAR en producción: requiere torch + numpy ~2GB)
# sentence-transformers
# numpy
"""

with open('backend/requirements-dev.txt', 'w', encoding='utf-8') as f:
    f.write(REQ_DEV)
print("[OK] requirements-dev.txt creado")

# ============================================================
# 5. CREAR Dockerfile OPTIMIZADO
# ============================================================

DOCKERFILE = """FROM python:3.12-slim

WORKDIR /app

# Evita escribir .pyc y buffers
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema mínimas
RUN apt-get update && apt-get install -y --no-install-recommends \\
    libpq-dev \\
    && rm -rf /var/lib/apt/lists/*

# Copiar solo requirements primero (caché de Docker)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código
COPY backend/ ./backend/
COPY dashboard/ ./dashboard/

# Puerto
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Arrancar con uvicorn
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
"""

with open('Dockerfile', 'w', encoding='utf-8') as f:
    f.write(DOCKERFILE)
print("[OK] Dockerfile optimizado creado")

# ============================================================
# 6. CREAR .dockerignore
# ============================================================

DOCKERIGNORE = """# Python
__pycache__/
*.py[cod]
*$py.class
.venv/
env/
venv/

# Git
.git/
.gitignore

# Data (no copiar datos locales al contenedor)
data/
data_*/
backups/
n8n_extracted/
backup_linux/

# PDFs comerciales
docs/productos/**/*.pdf

# Entorno
.env
.env.example

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db

# Scripts de desarrollo
_add_*.py
_create_*.py

# ML models (no caben en el contenedor)
*.bin
*.gguf
*.pt
*.pth
*.onnx
"""

with open('.dockerignore', 'w', encoding='utf-8') as f:
    f.write(DOCKERIGNORE)
print("[OK] .dockerignore creado")

# ============================================================
# 7. VERIFICAR docker-compose.yml
# ============================================================

DOCKER_COMPOSE = """version: '3.8'

services:
  db:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    environment:
      POSTGRES_USER: agente
      POSTGRES_PASSWORD: agente_pw
      POSTGRES_DB: agente_ai
    ports:
      - "5433:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agente -d agente_ai"]
      interval: 10s
      timeout: 5s
      retries: 5

  backend:
    build: .
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      DB_DSN: postgresql://agente:agente_pw@db:5432/agente_ai
      N8N_WEBHOOK_URL: ""
      META_VERIFY_TOKEN: ""
      META_PAGE_ACCESS_TOKEN: ""
      META_APP_SECRET: ""
      KB_ADMIN_TOKEN: ""
      GROQ_API_KEY: ""
      GROQ_MODEL: llama-3.3-70b-versatile
      DEFAULT_WA_PHONE_E164: ""
      WA_PHONE_NUMBER_ID: ""
      META_WA_TOKEN: ""
      DASHBOARD_URL: "http://localhost:8000/dashboard"
    depends_on:
      db:
        condition: service_healthy

volumes:
  pgdata:
"""

# Check if docker-compose.yml exists and update it
compose_path = 'docker-compose.yml'
if os.path.exists(compose_path):
    with open(compose_path, 'r', encoding='utf-8') as f:
        existing = f.read()
    # Only update if it doesn't have the backend service
    if 'backend:' not in existing:
        with open(compose_path, 'w', encoding='utf-8') as f:
            f.write(DOCKER_COMPOSE)
        print("[OK] docker-compose.yml actualizado con servicio backend")
    else:
        print("[OK] docker-compose.yml ya tiene servicio backend")
else:
    with open(compose_path, 'w', encoding='utf-8') as f:
        f.write(DOCKER_COMPOSE)
    print("[OK] docker-compose.yml creado")

# ============================================================
# 8. GUARDAR app.py
# ============================================================

with open(APP_PY, 'w', encoding='utf-8') as f:
    f.write(content)
print(f"[OK] {APP_PY} guardado con imports opcionales")

print()
print("=" * 60)
print("OPTIMIZACIÓN COMPLETADA")
print("=" * 60)
print()
print("Cambios realizados:")
print("  1. F-string syntax error corregido (emoji)")
print("  2. pytesseract y PIL ahora son opcionales (try/except)")
print("  3. requirements.txt: solo producción (sin torch/numpy/sentence-transformers)")
print("  4. requirements-dev.txt: para desarrollo con PDFs")
print("  5. Dockerfile: python:3.12-slim, pip --no-cache-dir")
print("  6. .dockerignore: excluye basura, datos, ML models")
print("  7. docker-compose.yml: verificado/actualizado")
print()
print("Para instalar en producción:")
print("  pip install --no-cache-dir -r backend/requirements.txt")
print()
print("Para desarrollo (con PDFs):")
print("  pip install --no-cache-dir -r backend/requirements-dev.txt")
print()
print("Para levantar con Docker:")
print("  docker compose up -d --build")
