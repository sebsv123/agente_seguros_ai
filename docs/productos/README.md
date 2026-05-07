# 📋 Playbooks comerciales — Productos de Valentín Protección Integral

Este directorio contiene los PDFs con la documentación técnica y comercial de cada producto de seguro. El script `backend/extract_product_rules.py` los procesa para generar `backend/product_playbooks.json`, que alimenta al agente de WhatsApp.

## Estructura de carpetas

```
docs/productos/
├── README.md              ← Este archivo
├── salud/                 ← Seguros de salud
├── vida/                  ← Seguros de vida
├── dental/                ← Seguros dentales
├── mascotas/              ← Seguros para mascotas
├── decesos/               ← Seguros de decesos
├── autonomos/             ← Seguros para autónomos
├── extranjeria/           ← Seguros para extranjeros
├── accidentes/            ← Seguros de accidentes
└── juridica/              ← Defensa jurídica
```

## Cómo añadir un PDF nuevo

1. Coloca el PDF en la carpeta del producto correspondiente:
   - `docs/productos/salud/mi_pdf.pdf`
   - `docs/productos/vida/otro_pdf.pdf`
   - etc.

2. **Formato esperado del nombre de archivo:**
   - El script detecta automáticamente el producto por palabras clave en el nombre:
     - `salud`, `health`, `medico` → salud
     - `vida`, `life` → vida
     - `dental`, `dentista` → dental
     - `mascota`, `pet`, `perro` → mascotas
     - `deceso`, `funeraria` → decesos
     - `autonomo`, `freelance` → autonomos
     - `extranjero`, `foreigner` → extranjeria
     - `accidente` → accidentes
     - `juridico`, `defensa`, `legal` → juridica
   - Si el nombre no contiene palabras clave, se usará la carpeta donde está alojado.
   - Ejemplos válidos: `salud_completa_2025.pdf`, `vida_hipoteca.pdf`, `seguro_mascotas.pdf`

3. **Formato del PDF:**
   - Preferiblemente PDF con texto seleccionable (no escaneado)
   - Si es escaneado, el script intentará extraer texto con pypdf y pdfplumber
   - Para OCR completo, se necesita instalar `pytesseract` adicionalmente

## Cómo ejecutar la extracción

### Requisitos

```bash
pip install pypdf pdfplumber
```

### Comando básico

```bash
python backend/extract_product_rules.py
```

Esto genera `backend/product_playbooks.json` con todos los playbooks.

### Opciones

```bash
# Especificar directorio de PDFs
python backend/extract_product_rules.py --data_dir docs/productos

# Especificar archivo de salida
python backend/extract_product_rules.py --output backend/product_playbooks.json

# Simular sin escribir archivo
python backend/extract_product_rules.py --dry_run
```

### Dry run

El modo `--dry_run` muestra qué productos se detectarían sin generar el JSON:

```bash
python backend/extract_product_rules.py --dry_run
```

## Formato de salida

El archivo `backend/product_playbooks.json` tiene esta estructura:

```json
{
  "metadata": {
    "generado": "extract_product_rules.py",
    "total_pdfs": 5,
    "total_productos": 9,
    "productos_con_pdf": ["salud", "vida", "dental"]
  },
  "productos": [
    {
      "producto": "salud",
      "source_file": "salud_completa.pdf",
      "resumen_comercial": "...",
      "perfil_objetivo": ["..."],
      "preguntas_iniciales": ["..."],
      "datos_minimos": ["..."],
      "objeciones_frecuentes": ["..."],
      "limites": ["..."],
      "cuando_derivar_humano": ["..."]
    }
  ]
}
```

## Reglas importantes

- ❌ **NUNCA** mencionar marcas de aseguradoras (ASISA, Mapfre, Sanitas, etc.)
- ✅ El agente trabaja para **Valentín Protección Integral** (agentes vinculados DGSFP)
- ✅ Tono cercano, breve, español natural (orientado a WhatsApp)
- ✅ Los playbooks están diseñados para un agente que recibe leads de Google Ads

## Política de marcas

Los PDFs de producto pueden contener nombres de compañías aseguradoras (Adeslas, DKV, Sanitas, Mapfre, etc.). El sistema **sanitiza automáticamente** estos nombres antes de generar cualquier playbook.

### ¿Qué se sanitiza?

La lista completa de términos reemplazados incluye:

| Categoría | Compañías |
|-----------|-----------|
| **Salud** | Adeslas, DKV, Sanitas, Asisa, Cigna, Humana |
| **Generales** | Mapfre, Allianz, AXA, Generali, Zurich, Caser, Mutua Madrileña, Pelayo, Reale, SegurCaixa, FIATC, MGS, Helvetia, Berkley |
| **Vida/Decesos** | Aegon, CNP, Nationale Nederlanden, Previsora General, Preventiva, Santa Lucía, Ocaso, Funespaña |
| **Autos/Hogar** | Línea Directa |
| **Extranjería** | Grupo ASV |
| **Bancos** | BBVA, Santander, ING, CaixaBank, Bankinter, Sabadell |
| **Otras** | Catalana Occidente, Plus Ultra, Liberty |

### ¿Cómo funciona?

1. **`sanitize_text()`** — Capa principal que se aplica ANTES de generar cualquier playbook:
   - Reemplaza nombres de marcas por términos neutros ("la aseguradora", "la compañía", "la mutua", etc.)
   - Limpia caracteres de control y espacios múltiples
   - Normaliza saltos de línea
   - Elimina números de página y encabezados

2. **`sanitize_brands()`** — Función específica para marcas:
   - Insensible a mayúsculas/minúsculas (Adeslas, ADESLAS, adeslas)
   - Detecta variaciones ortográficas
   - Aplica contexto semántico para elegir el reemplazo adecuado

3. **`validate_playbook_brands()`** — Validación post-generación:
   - Verifica todos los campos del JSON de salida
   - Si detecta alguna marca, lanza un WARNING en el log
   - Incluye el contador de warnings en los metadatos del JSON

### ¿Qué hacer si aparece una marca nueva?

Si encuentras una marca que no está siendo sanitizada, añádela a la lista `_BRAND_NAMES` en `backend/extract_product_rules.py` y vuelve a ejecutar la extracción.

## Notas técnicas

- El script reutiliza `pypdf` y `pdfplumber` (ya disponibles en el proyecto)
- Si no hay PDFs para un producto, se genera un playbook genérico con valores por defecto
- Si hay múltiples PDFs para el mismo producto, los playbooks se fusionan automáticamente
- Las marcas de aseguradoras se sanitizan automáticamente en el texto extraído
