#!/bin/bash
# upload_kb_to_oci.sh
# Sube todos los PDFs de ./data/ al bucket OCI Object Storage
# Ejecutar desde el portátil local donde están los PDFs
# Requiere: oci CLI instalado (pip install oci-cli)

set -e

BUCKET="${OCI_BUCKET_NAME:-agente-rosa-kb}"
DATA_DIR="${1:-./data}"

echo "📤 Subiendo PDFs a OCI Object Storage"
echo "   Bucket: $BUCKET"
echo "   Directorio: $DATA_DIR"
echo ""

if ! command -v oci &> /dev/null; then
    echo "❌ OCI CLI no instalado. Instalar con: pip install oci-cli"
    echo "   Después configurar: oci setup config"
    exit 1
fi

count=0
errors=0

find "$DATA_DIR" -name "*.pdf" | while read -r pdf; do
    rel="${pdf#$DATA_DIR/}"
    echo -n "  → $rel ... "
    if oci os object put \
        --bucket-name "$BUCKET" \
        --name "$rel" \
        --file "$pdf" \
        --force \
        --no-multipart 2>/dev/null; then
        echo "✅"
        count=$((count + 1))
    else
        echo "❌"
        errors=$((errors + 1))
    fi
done

echo ""
echo "✅ Subida completada"
echo "   Para cargar en el agente ejecuta:"
echo "   curl -X POST https://agente.valentinproteccionintegral.com/kb/sync-from-cloud \\"
echo "     -H 'X-Admin-Token: TU_TOKEN' -H 'Content-Type: application/json' -d '{}'"
