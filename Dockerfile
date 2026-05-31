FROM python:3.11-slim

# Metadatos
LABEL maintainer="Valentín Protección Integral"
LABEL version="2.0.0"

# Variables de build
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias sistema (curl para healthcheck + tesseract para OCR)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Dependencias Python (capa separada para cache de Docker)
COPY backend/requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Código fuente
COPY backend/ ./backend/
COPY dashboard/ ./dashboard/

# Directorio de datos y logs (se montan como volúmenes)
RUN mkdir -p /app/data /app/logs

# Usuario no-root (seguridad)
RUN useradd -m -u 1000 rosa && chown -R rosa:rosa /app
USER rosa

EXPOSE 8000

# Healthcheck interno del contenedor
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--log-level", "info", "--access-log"]
