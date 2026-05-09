#!/usr/bin/env python3
"""
extract_product_rules.py — Genera playbooks comerciales desde PDFs de producto.

Lee los PDFs de docs/productos/<categoria>/ (o KB_DATA_DIR del .env),
extrae el texto con PyMuPDF/pdfplumber, y para cada PDF genera un playbook
estructurado con campos comerciales clave para el agente de WhatsApp.

Uso:
    python backend/extract_product_rules.py
    python backend/extract_product_rules.py --data_dir docs/productos
    python backend/extract_product_rules.py --output backend/product_playbooks.json
    python backend/extract_product_rules.py --dry_run

Salida:
    backend/product_playbooks.json  (por defecto)
    Estructura: { "productos": [ { ...playbook... }, ... ] }

Reglas:
    - NUNCA menciona marcas de aseguradoras en los playbooks
    - Tono cercano, breve, español natural (orientado a WhatsApp)
    - El agente trabaja para Valentín Protección Integral (agentes vinculados DGSFP)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# ── Optional dependencies with guards ──────────────────
try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except Exception:
    fitz = None  # type: ignore
    _HAS_FITZ = False

try:
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
    _HAS_PIL = False

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [extract] %(message)s",
)
logger = logging.getLogger("extract")

# ── Config ────────────────────────────────────────────
DEFAULT_DATA_DIR = os.getenv("KB_DATA_DIR", "./data")
DEFAULT_OUTPUT = "backend/product_playbooks.json"

# Productos soportados
PRODUCTOS = [
    "salud",
    "vida",
    "dental",
    "mascotas",
    "decesos",
    "autonomos",
    "extranjeria",
    "accidentes",
    "juridica",
]

# Sinónimos para detectar producto por nombre de archivo/carpeta
PRODUCT_KEYWORDS: Dict[str, List[str]] = {
    "salud": ["salud", "health", "medico", "medica", "asistencia sanitaria", "seguro medico"],
    "vida": ["vida", "life", "fallecimiento", "invalidez"],
    "dental": ["dental", "dentista", "odontologia", "bucal", "dientes"],
    "mascotas": ["mascota", "mascotas", "pet", "animal", "perro", "gato"],
    "decesos": ["deceso", "decesos", "funeraria", "entierro", "muerte"],
    "autonomos": ["autonomo", "autonomos", "autónomo", "freelance", "profesional"],
    "extranjeria": ["extranjero", "extranjeria", "extranjería", "internacional", "foreigner", "expat"],
    "accidentes": ["accidente", "accidentes"],
    "juridica": ["juridico", "juridica", "jurídico", "defensa", "legal", "abogado"],
}

# ── Marcas a sanitizar (NUNCA mencionar en outputs) ──
# Lista completa de aseguradoras, mutuas, bancos y otras compañías
# que deben ser reemplazadas por términos neutros en cualquier output.
_BRAND_NAMES: List[str] = [
    # Salud
    "Adeslas", "DKV", "Sanitas", "Asisa", "Cigna", "Humana",
    # Generales
    "Mapfre", "Allianz", "AXA", "Generali", "Zurich", "Caser",
    "Mutua Madrileña", "Mutua", "Pelayo", "Reale", "SegurCaixa",
    "FIATC", "Fiatc", "MGS", "Helvetia", "Berkley", "Berkley",
    # Vida y decesos
    "Aegon", "CNP", "Nationale Nederlanden", "Previsora General",
    "Preventiva", "Santa Lucía", "Santa Lucia", "Ocaso",
    "Funespaña", "Funeraria",
    # Autos y hogar
    "Línea Directa", "Linea Directa",
    # Extranjería
    "Grupo ASV",
    # Bancos (mencionados en contextos de seguro)
    "BBVA", "Santander", "ING", "CaixaBank", "Bankinter", "Sabadell",
    # Otras
    "Catalana Occidente", "Plus Ultra", "Liberty", "Liberty Seguros",
]

# Compilar patrones una sola vez (insensible a mayúsculas/minúsculas)
BRAND_PATTERNS: List[Any] = []
for name in _BRAND_NAMES:
    # Escapar caracteres especiales (como ñ, í, etc.)
    escaped = re.escape(name)
    pattern = re.compile(r"\b" + escaped + r"\b", re.I)
    # Determinar reemplazo según contexto
    replacement = "la aseguradora"
    name_lower = name.lower()
    if any(kw in name_lower for kw in ["banco", "bbva", "santander", "ing", "caixabank", "bankinter", "sabadell"]):
        replacement = "la entidad bancaria"
    elif any(kw in name_lower for kw in ["mutua"]):
        replacement = "la mutua"
    elif any(kw in name_lower for kw in ["funeraria", "funespaña"]):
        replacement = "el servicio funerario"
    elif any(kw in name_lower for kw in ["línea directa", "linea directa"]):
        replacement = "la compañía"
    BRAND_PATTERNS.append((pattern, replacement))

# Patrón adicional para frases comunes con marcas
_BRAND_CONTEXT_PATTERNS = [
    (re.compile(r"según\s+(?:la\s+)?(?:aseguradora|compañía|mutua)", re.I), "según la póliza"),
    (re.compile(r"(?:en|para|de)\s+(?:la\s+)?(?:aseguradora|compañía|mutua)", re.I), "en la póliza"),
    (re.compile(r"(?:contratado|contratar)\s+con\s+(?:la\s+)?(?:aseguradora|compañía)", re.I), "contratado con la aseguradora"),
]


def sanitize_brands(text: str) -> str:
    """
    Reemplaza nombres de marcas de aseguradoras por términos genéricos.
    
    - Insensible a mayúsculas/minúsculas
    - Funciona con variaciones (Adeslas, ADESLAS, adeslas, AdeslaS, etc.)
    - Aplica contexto semántico para elegir el reemplazo adecuado
    """
    for pattern, replacement in BRAND_PATTERNS:
        text = pattern.sub(replacement, text)
    for pattern, replacement in _BRAND_CONTEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize_text(text: str) -> str:
    """
    Capa completa de sanitización para aplicar ANTES de generar cualquier playbook.
    
    - Sanitiza marcas de aseguradoras
    - Limpia espacios múltiples
    - Normaliza saltos de línea
    - Elimina caracteres de control
    """
    if not text:
        return ""
    # Sanitizar marcas
    text = sanitize_brands(text)
    # Limpiar caracteres de control (excepto saltos de línea y tabs)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    # Normalizar saltos de línea
    text = re.sub(r"\r\n?", "\n", text)
    # Eliminar líneas que solo contienen números de página o encabezados
    text = re.sub(r"\n\s*\d+\s*\n", "\n", text)
    # Limpiar espacios múltiples
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def validate_playbook_brands(playbook: Dict[str, Any]) -> List[str]:
    """
    Verifica que ningún campo del playbook contenga nombres de marcas.
    Devuelve una lista de warnings encontrados.
    """
    warnings: List[str] = []
    producto = playbook.get("producto", "desconocido")
    
    # Compilar regex de todas las marcas para verificación
    all_brands_pattern = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in _BRAND_NAMES) + r")\b",
        re.I
    )
    
    campos_a_verificar = [
        "resumen_comercial",
        "perfil_objetivo",
        "preguntas_iniciales",
        "datos_minimos",
        "objeciones_frecuentes",
        "limites",
        "cuando_derivar_humano",
    ]
    
    for campo in campos_a_verificar:
        valor = playbook.get(campo)
        if isinstance(valor, str):
            if all_brands_pattern.search(valor):
                warnings.append(f"  ⚠️ [{producto}] Campo '{campo}' contiene marca: {all_brands_pattern.findall(valor)}")
        elif isinstance(valor, list):
            for i, item in enumerate(valor):
                if isinstance(item, str) and all_brands_pattern.search(item):
                    warnings.append(f"  ⚠️ [{producto}] Campo '{campo}[{i}]' contiene marca: {all_brands_pattern.findall(item)}")
    
    return warnings


# ── Detección de producto ─────────────────────────────
def detect_producto(filepath: Path) -> str:
    """Detecta el producto por nombre de archivo o subcarpeta."""
    name_lower = filepath.stem.lower()
    # Buscar en el nombre del archivo
    for producto, keywords in PRODUCT_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                return producto
    # Buscar en la ruta (subcarpeta)
    for part in filepath.parts:
        part_lower = str(part).lower()
        for producto, keywords in PRODUCT_KEYWORDS.items():
            for kw in keywords:
                if kw in part_lower:
                    return producto
    return "general"


# ── Extracción de texto del PDF ──────────────────────
def extract_text_pypdf(filepath: Path) -> Optional[str]:
    try:
        import pypdf
        reader = pypdf.PdfReader(str(filepath))
        pages = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                pages.append(t)
        return "\n".join(pages) if pages else None
    except Exception as e:
        logger.warning("pypdf error on %s: %s", filepath.name, e)
        return None


def extract_text_pdfplumber(filepath: Path) -> Optional[str]:
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(str(filepath)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages.append(t)
        return "\n".join(pages) if pages else None
    except Exception as e:
        logger.warning("pdfplumber error on %s: %s", filepath.name, e)
        return None


def extract_text_pymupdf_ocr(filepath: Path) -> Optional[str]:
    try:
        import fitz
        try:
            from PIL import Image
            from io import BytesIO
            import pytesseract
        except ImportError:
            return None
        doc = fitz.open(str(filepath))
        pages = []
        for page in doc:
            t = page.get_text()
            if t and len(t.strip()) > 50:
                pages.append(t)
            else:
                pix = page.get_pixmap(dpi=200)
                img_bytes = pix.tobytes("png")
                img = Image.open(BytesIO(img_bytes))
                t = pytesseract.image_to_string(img, lang="spa+eng")
                if t.strip():
                    pages.append(t)
        doc.close()
        return "\n".join(pages) if pages else None
    except Exception as e:
        logger.warning("pymupdf_ocr error on %s: %s", filepath.name, e)
        return None


def extract_text(filepath: Path) -> Optional[str]:
    """Extrae texto de un PDF, probando múltiples extractores en orden."""
    text = extract_text_pypdf(filepath)
    if text and len(text.strip()) > 50:
        return sanitize_text(text)
    
    text = extract_text_pdfplumber(filepath)
    if text and len(text.strip()) > 50:
        return sanitize_text(text)
    
    text = extract_text_pymupdf_ocr(filepath)
    if text and len(text.strip()) > 50:
        return sanitize_text(text)
    
    logger.error("No se pudo extraer texto de %s (probados: pypdf, pdfplumber, pymupdf_ocr)", filepath.name)
    return None


# ── Generación de playbook ────────────────────────────
def _generar_argumentos(producto: str) -> List[str]:
    """Devuelve argumentos comerciales hardcoded por producto."""
    args = {
        "salud": [
            "+10 años en Madrid Oeste",
            "Desde 22,50€/mes",
            "Sin copagos disponible",
            "Sin carencias en urgencias",
            "Asesoría personal incluida",
        ],
        "extranjeria": [
            "+100 NIE/TIE aprobados",
            "Sin copagos desde el día 1",
            "Certificado en 24h",
            "Válido para visado y residencia",
            "Te atendemos en tu idioma",
        ],
        "extranjeros": [
            "+100 NIE/TIE aprobados",
            "Sin copagos desde el día 1",
            "Certificado en 24h",
            "Válido para visado y residencia",
            "Te atendemos en tu idioma",
        ],
        "vida": [
            "Cobertura desde el primer día",
            "Capital desde 50.000€",
            "Protege tu hipoteca y familia",
            "Sin médico en muchos casos",
        ],
        "dental": [
            "Revisiones ilimitadas",
            "Sin esperas para limpieza",
            "Precios muy competitivos",
            "Red amplia de clínicas en Madrid",
        ],
        "mascotas": [
            "Cubre enfermedades y accidentes",
            "Red veterinaria en Madrid",
            "Sin franquicia en urgencias",
        ],
        "autonomos": [
            "Baja desde el día 1 en algunos planes",
            "Responsabilidad civil incluida",
            "Adaptado a tu actividad",
        ],
        "accidentes": [
            "Cobertura 24h en todo el mundo",
            "Indemnización desde el primer accidente",
            "Sin periodos de espera",
        ],
        "decesos": [
            "Gestión completa del sepelio",
            "Sin límite de edad en muchos planes",
            "Tranquilidad para toda la familia",
        ],
    }
    result = args.get(producto, [
        "Asesoría personalizada",
        "Precios competitivos",
        "Más de 10 años de experiencia",
        "+1.200 familias protegidas",
    ])
    return [sanitize_brands(a) for a in result]


def _generar_respuestas_objeciones(producto: str) -> List[Dict[str, str]]:
    """Devuelve objeciones con respuestas comerciales estilo WhatsApp."""
    base = {
        "salud": [
            {"objecion": "Ya tengo la seguridad social", "respuesta": "La pública es un derecho, claro. Pero tener médico sin listas de espera que te ve hoy o mañana cambia la vida — muchos clientes lo tienen complementario."},
            {"objecion": "Es muy caro", "respuesta": "Desde 22,50€/mes. Para muchas familias es menos que una cena, y cuando lo necesitas no tiene precio."},
            {"objecion": "Tengo preexistencias", "respuesta": "Muchas preexistencias sí tienen cobertura. Cuéntame cuál es y lo miramos juntos sin compromiso."},
            {"objecion": "Ya tengo seguro con mi banco", "respuesta": "Los seguros de banco suelen tener coberturas más limitadas y son más caros. Podemos comparar sin que tengas que cambiar nada todavía."},
        ],
        "extranjeria": [
            {"objecion": "No sé si vale para mi visado", "respuesta": "Llevamos más de 100 tramitaciones aprobadas. Dime qué tipo de visado es y te confirmo en minutos si cubre."},
            {"objecion": "Es complicado el proceso", "respuesta": "Nosotros lo gestionamos todo. Solo necesitas contratarlo, el certificado te llega en 24h."},
            {"objecion": "Voy a volver a mi país en unos meses", "respuesta": "Puedes contratar por meses, sin permanencia. Mientras estés aquí, estás cubierto."},
        ],
        "extranjeros": [
            {"objecion": "No sé si vale para mi visado", "respuesta": "Llevamos más de 100 tramitaciones aprobadas. Dime qué tipo de visado es y te confirmo en minutos si cubre."},
            {"objecion": "Es complicado el proceso", "respuesta": "Nosotros lo gestionamos todo. Solo necesitas contratarlo, el certificado te llega en 24h."},
            {"objecion": "Voy a volver a mi país en unos meses", "respuesta": "Puedes contratar por meses, sin permanencia. Mientras estés aquí, estás cubierto."},
        ],
        "vida": [
            {"objecion": "Soy joven y no lo necesito", "respuesta": "Cuanto más joven, más barato. Y si mañana pasa algo, tus seres queridos te lo agradecerán."},
            {"objecion": "Ya tengo uno con la hipoteca", "respuesta": "El del banco solo cubre la deuda pendiente. Con un seguro aparte, además proteges a tu familia."},
            {"objecion": "Es caro para lo que cubre", "respuesta": "Desde 50.000€ de capital por menos de 1€ al día. Cuando piensas en quienes dependen de ti, no tiene precio."},
        ],
        "dental": [
            {"objecion": "Me sale más barato pagar por separado", "respuesta": "Una limpieza cuesta unos 50€. Con el seguro pagas menos de 15€ al mes y tienes revisiones ilimitadas."},
            {"objecion": "Solo voy una vez al año", "respuesta": "Justo por eso merece la pena: por el precio de una visita al año, tienes revisiones ilimitadas y descuentos en todo."},
            {"objecion": "Mi médico ya cubre dental básico", "respuesta": "El dental de los seguros médicos suele ser muy básico. Con un plan específico tienes ortodoncia, implantes y mucho más."},
        ],
        "mascotas": [
            {"objecion": "Mi mascota está sana", "respuesta": "Eso es lo mejor. Justo ahora es el mejor momento para contratar, antes de que tenga cualquier problema."},
            {"objecion": "Es caro para un animal", "respuesta": "Una urgencia veterinaria puede costar 300€ o más. Por menos de 1€ al día, duermes tranquilo."},
        ],
        "autonomos": [
            {"objecion": "Ya cotizo bastante", "respuesta": "La mutua cubre lo básico. Con un seguro privado tienes especialistas sin esperas y baja desde el día 1 en algunos planes."},
            {"objecion": "Es un gasto más", "respuesta": "Piénsalo como proteger tu herramienta de trabajo: tú. Si no estás al 100%, tu negocio lo nota."},
        ],
        "accidentes": [
            {"objecion": "A mí no me va a pasar nada", "respuesta": "Ojalá tengas razón. Pero los accidentes pasan sin avisar, y una cobertura de 24h en todo el mundo es muy barata comparada con lo que cuesta no tenerla."},
            {"objecion": "Mi trabajo no es de riesgo", "respuesta": "No hace falta. Un accidente de coche, una caída de fin de semana... esto cubre tu tiempo libre también."},
        ],
        "decesos": [
            {"objecion": "No quiero pensar en eso", "respuesta": "Lo entiendo. Pero tenerlo organizado es un regalo para tu familia. Ellos no tendrán que preocuparse de nada."},
            {"objecion": "Mis hijos se harán cargo", "respuesta": "Claro, pero un sepelio cuesta entre 3.000 y 6.000€. Con el seguro, ellos solo tienen que despedirse."},
        ],
        "juridica": [
            {"objecion": "Nunca he tenido problemas legales", "respuesta": "Ojalá siga siendo así. Pero si llega el día, tener un abogado disponible 24h marca la diferencia."},
            {"objecion": "Es caro para lo que ofrece", "respuesta": "Una consulta con abogado cuesta 100€ fácil. Por menos de 30€ al mes tienes defensa completa."},
        ],
        "hogar": [
            {"objecion": "Mi casa es nueva y está bien", "respuesta": "Justo por eso: una avería eléctrica o una fuga de agua puede costarte miles de euros. Mejor prevenir."},
            {"objecion": "Ya tengo seguro de hogar", "respuesta": "Podemos revisar tu póliza actual y ver si estás pagando de más por coberturas que no necesitas."},
        ],
        "viaje": [
            {"objecion": "Viajo poco", "respuesta": "Con un viaje al año ya compensa. Una repatriación cuesta miles de euros y con el seguro la tienes cubierta."},
            {"objecion": "Mi tarjeta del banco ya me cubre", "respuesta": "Las tarjetas suelen tener coberturas muy limitadas. Comparamos tu cobertura actual con la nuestra sin compromiso."},
        ],
        "negocios": [
            {"objecion": "Mi negocio es pequeño", "respuesta": "Los pequeños son los que más lo notan. Una reclamación de un cliente puede hundir un negocio pequeño."},
            {"objecion": "Ya tengo seguro", "respuesta": "Podemos revisar tu póliza actual y ver si se ajusta a tu actividad real. Muchas veces se paga de más."},
        ],
    }
    result = base.get(producto, [
        {"objecion": "No lo necesito", "respuesta": "Ojalá tengas razón. Pero por lo que cuesta, merece la pena estar tranquilo."},
        {"objecion": "Es demasiado caro", "respuesta": "Hay opciones para todos los presupuestos. Cuéntame qué buscas y te ajustamos algo."},
    ])
    # Sanitizar marcas en todas las respuestas
    for item in result:
        item["respuesta"] = sanitize_brands(item["respuesta"])
    return result


def generar_playbook(producto: str, texto: str, source_file: str) -> Dict[str, Any]:
    """
    Genera un playbook estructurado a partir del texto extraído del PDF.
    
    Usa reglas heurísticas + extracción de secciones del PDF para construir
    el playbook. En una versión futura se podría usar un LLM para mejorar
    la extracción semántica.
    """
    texto_lower = texto.lower()
    
    # --- resumen_comercial ---
    resumen = _extraer_resumen(texto, producto)
    
    # --- perfil_objetivo ---
    perfiles = _extraer_perfiles(texto, producto)
    
    # --- preguntas_iniciales ---
    preguntas = _generar_preguntas(producto, texto)
    
    # --- datos_minimos ---
    datos = _extraer_datos_minimos(producto, texto)
    
    # --- objeciones_frecuentes ---
    objeciones = _generar_objeciones(producto, texto)
    
    # --- limites ---
    limites = _generar_limites(producto)
    
    # --- cuando_derivar_humano ---
    derivar = _generar_derivacion(producto)
    
    # --- argumentos_clave ---
    argumentos = _generar_argumentos(producto)
    
    # --- respuestas_objeciones ---
    respuestas_obj = _generar_respuestas_objeciones(producto)
    
    return {
        "producto": producto,
        "source_file": source_file,
        "resumen_comercial": resumen,
        "perfil_objetivo": perfiles,
        "preguntas_iniciales": preguntas,
        "datos_minimos": datos,
        "objeciones_frecuentes": objeciones,
        "limites": limites,
        "cuando_derivar_humano": derivar,
        "argumentos_clave": argumentos,
        "respuestas_objeciones": respuestas_obj,
    }


def _extraer_resumen(texto: str, producto: str) -> Optional[str]:
    """
    Extrae un resumen comercial del texto del PDF.
    
    Estrategia:
    1. Buscar secciones con cabeceras conocidas (OBJETO DEL SEGURO, etc.)
    2. Fallback: primeras líneas con sentido comercial
    3. Si no se encuentra nada útil, devuelve None (se usará resumen hardcoded)
    """
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    
    # ── Estrategia 1: Buscar secciones por cabecera ──────────────────────
    cabeceras = [
        "OBJETO DEL SEGURO", "¿QUÉ CUBRE", "QUÉ ES", "DESCRIPCIÓN DEL PRODUCTO",
        "DESCRIPCION DEL PRODUCTO", "RESUMEN", "MODALIDADES", "QUÉ INCLUYE",
        "QUE INCLUYE", "CARACTERÍSTICAS", "CARACTERISTICAS", "COBERTURAS",
    ]
    
    for i, linea in enumerate(lineas):
        linea_upper = linea.strip().upper()
        # Comprobar si esta línea es una cabecera conocida
        for cab in cabeceras:
            if linea_upper.startswith(cab) or linea_upper == cab:
                # Recoger texto desde la siguiente línea hasta el siguiente encabezado
                partes = []
                for j in range(i + 1, min(i + 30, len(lineas))):
                    sig_linea = lineas[j]
                    # Detener si encontramos otro posible encabezado
                    if (sig_linea.isupper() and len(sig_linea) > 3 and len(sig_linea) < 80):
                        break
                    if sig_linea and len(sig_linea) > 10:
                        partes.append(sig_linea)
                    if len(" ".join(partes)) > 500:
                        break
                
                if partes:
                    resumen = " ".join(partes)
                    # Limpiar
                    resumen = re.sub(r"\n{2,}", "\n", resumen)
                    resumen = resumen.strip()
                    # Acortar a máximo 300 caracteres
                    if len(resumen) > 300:
                        resumen = resumen[:300].rsplit(" ", 1)[0] + "."
                    if len(resumen) >= 30:
                        return sanitize_brands(resumen)
    
    # ── Estrategia 2 (fallback): primeras líneas con sentido ─────────────
    _REJECT_PATTERNS = re.compile(
        r"(página|índice|www\.|@|s\.?a\.?|s\.?l\.?|nif|cif|nº|n\.º)",
        re.I
    )
    _DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
    
    candidatas = []
    for linea in lineas[:80]:  # Buscar en las primeras 80 líneas
        if len(linea) < 40:
            continue
        if _DATE_RE.search(linea):
            continue
        if _REJECT_PATTERNS.search(linea):
            continue
        if re.match(r"^[\d\s\-\.]+$", linea):
            continue
        candidatas.append(linea)
        if len(candidatas) >= 3:
            break
    
    if candidatas:
        resumen = " ".join(candidatas)
        resumen = re.sub(r"\n{2,}", "\n", resumen)
        resumen = resumen.strip()
        if len(resumen) > 300:
            resumen = resumen[:300].rsplit(" ", 1)[0] + "."
        if len(resumen) >= 30:
            return sanitize_brands(resumen)
    
    # ── Sin resultado útil ───────────────────────────────────────────────
    return None


def _extraer_perfiles(texto: str, producto: str) -> List[str]:
    """Extrae perfiles objetivo del texto."""
    texto_lower = texto.lower()
    perfiles = []
    
    # Patrones comunes en PDFs de seguros
    patrones_perfil = [
        (r"mayores?\s+de\s+(\d+)", "Personas mayores de {0} años"),
        (r"menores?\s+de\s+(\d+)", "Personas menores de {0} años"),
        (r"entre\s+(\d+)\s+y\s+(\d+)\s*(?:años)?", "Personas de entre {0} y {1} años"),
        (r"(?:para|dirigido a|orientado a)\s+([^\.]+)", "{0}"),
        (r"(?:autónomo|autonomo|freelance)", "Autónomos y profesionales independientes"),
        (r"(?:familia|pareja|cónyuge)", "Familias y parejas"),
        (r"(?:extranjero|extranjeria|inmigrante)", "Extranjeros residentes en España"),
        (r"(?:mayor|senior|jubilado)", "Personas mayores y jubilados"),
        (r"(?:joven|jóvenes)", "Jóvenes"),
        (r"(?:empresa|pyme|negocio)", "Empresas y autónomos"),
    ]
    
    for patron, template in patrones_perfil:
        matches = re.findall(patron, texto_lower)
        for m in matches:
            if isinstance(m, tuple):
                perfil = template.format(*m)
            else:
                perfil = template.format(m)
            # Capitalizar primera letra
            perfil = perfil[0].upper() + perfil[1:]
            if perfil not in perfiles:
                perfiles.append(perfil)
    
    # Perfiles por defecto según producto
    perfiles_default = {
        "salud": [
            "Personas que buscan un seguro médico privado",
            "Familias que quieren evitar listas de espera",
            "Autónomos sin acceso a mutua",
        ],
        "vida": [
            "Personas con cargas familiares",
            "Titulares de hipoteca",
            "Autónomos que quieren proteger su negocio",
        ],
        "dental": [
            "Personas que necesitan revisiones periódicas",
            "Familias con niños",
            "Personas sin cobertura dental en su seguro médico",
        ],
        "mascotas": [
            "Dueños de perros y gatos",
            "Personas que quieren evitar gastos veterinarios imprevistos",
        ],
        "decesos": [
            "Personas mayores que quieren dejar todo organizado",
            "Familias que buscan aliviar la carga económica a sus seres queridos",
        ],
        "autonomos": [
            "Autónomos y profesionales liberales",
            "Pequeños empresarios",
        ],
        "extranjeria": [
            "Extranjeros residentes en España sin acceso a sanidad pública",
            "Estudiantes internacionales",
        ],
        "accidentes": [
            "Personas con trabajos de riesgo",
            "Deportistas y aficionados a actividades al aire libre",
        ],
        "juridica": [
            "Autónomos y empresas",
            "Propietarios de viviendas",
            "Conductores",
        ],
    }
    
    if not perfiles:
        perfiles = perfiles_default.get(producto, ["Personas interesadas en contratar un seguro"])
    
    return perfiles[:5]  # Máximo 5 perfiles


def _generar_preguntas(producto: str, texto: str) -> List[str]:
    """Genera preguntas iniciales para el agente de WhatsApp."""
    
    preguntas_base = {
        "salud": [
            "¿Para quién buscas el seguro? ¿Para ti, para tu familia o para un familiar mayor?",
            "¿Tienes alguna enfermedad o condición preexistente que debamos tener en cuenta?",
            "¿Prefieres un seguro con copagos (más barato) o sin copagos (más completo)?",
            "¿Necesitas cobertura dental incluida o solo médico?",
            "¿Qué tipo de especialista sueles visitar con más frecuencia?",
        ],
        "vida": [
            "¿Tienes alguna hipoteca o préstamo que quieras proteger?",
            "¿Tienes personas a tu cargo (hijos, cónyuge, padres)?",
            "¿Qué capital te gustaría dejar asegurado?",
            "¿Eres autónomo o trabajas por cuenta ajena?",
        ],
        "dental": [
            "¿Hace cuánto no te haces una revisión dental?",
            "¿Necesitas un tratamiento concreto (ortodoncia, implantes) o solo revisiones?",
            "¿Buscas seguro solo para ti o para toda la familia?",
            "¿Tienes algún problema dental actual que necesites tratar?",
        ],
        "mascotas": [
            "¿Qué tipo de mascota tienes? ¿Perro, gato u otro?",
            "¿Tu mascota tiene alguna enfermedad crónica o condición preexistente?",
            "¿Buscas solo accidentes o también quieres cobertura de enfermedad y vacunación?",
            "¿Tu mascota está identificada con microchip?",
        ],
        "decesos": [
            "¿Buscas un seguro de decesos para ti o para un familiar?",
            "¿Te gustaría incluir cobertura para toda la familia?",
            "¿Tienes preferencia por alguna funeraria o servicio concreto?",
            "¿Quieres contratar el seguro ahora o es para planificar a futuro?",
        ],
        "autonomos": [
            "¿De qué sector es tu actividad profesional?",
            "¿Tienes empleados a tu cargo?",
            "¿Qué tipo de cobertura buscas? ¿Baja laboral, responsabilidad civil, accidentes?",
            "¿Actualmente tienes alguna mutua o seguro profesional?",
        ],
        "extranjeria": [
            "¿Cuál es tu país de origen y cuánto tiempo llevas en España?",
            "¿Tienes tarjeta sanitaria europea o acceso a la sanidad pública?",
            "¿Necesitas el seguro para trámites de residencia o por tranquilidad personal?",
            "¿Prefieres atención en español, inglés u otro idioma?",
        ],
        "accidentes": [
            "¿Qué tipo de actividad o trabajo realizas?",
            "¿Practicas algún deporte de riesgo habitualmente?",
            "¿Buscas cobertura para accidentes laborales o también para tu tiempo libre?",
            "¿Quieres incluir cobertura para tu familia?",
        ],
        "juridica": [
            "¿Necesitas defensa jurídica para ti, tu negocio o tu hogar?",
            "¿Qué tipo de conflicto legal te preocupa más?",
            "¿Eres autónomo o empresa?",
            "¿Tienes ya algún abogado de confianza o prefieres que te asignemos uno?",
        ],
    }
    
    preguntas = preguntas_base.get(producto, [
        "¿Qué tipo de cobertura estás buscando?",
        "¿Para quién sería el seguro?",
        "¿Cuándo te gustaría empezar con la cobertura?",
    ])
    
    return preguntas


def _extraer_datos_minimos(producto: str, texto: str) -> List[str]:
    """Extrae los campos mínimos necesarios para cotizar."""
    
    datos_base = {
        "salud": [
            "Edad del asegurado",
            "Código postal",
            "Sexo",
            "¿Tiene preexistencias?",
        ],
        "vida": [
            "Edad del asegurado",
            "Capital asegurado deseado",
            "¿Tiene hipoteca?",
            "Profesión",
        ],
        "dental": [
            "Edad del asegurado",
            "Código postal",
            "¿Necesita tratamiento concreto?",
        ],
        "mascotas": [
            "Tipo de mascota (perro/gato/otro)",
            "Edad de la mascota",
            "Raza",
            "¿Tiene microchip?",
        ],
        "decesos": [
            "Edad del asegurado principal",
            "Número de personas a incluir",
            "Código postal",
        ],
        "autonomos": [
            "Actividad profesional (epígrafe IAE)",
            "Edad",
            "Facturación anual aproximada",
            "¿Tiene empleados?",
        ],
        "extranjeria": [
            "Nacionalidad",
            "Edad",
            "¿Tiene NIE o pasaporte?",
            "¿Necesita el seguro para el permiso de residencia?",
        ],
        "accidentes": [
            "Edad",
            "Profesión o actividad",
            "Capital asegurado deseado",
        ],
        "juridica": [
            "Tipo de defensa (hogar/negocio/personal)",
            "Edad",
            "¿Tiene algún conflicto legal actual?",
        ],
    }
    
    return datos_base.get(producto, [
        "Edad",
        "Código postal",
        "Tipo de cobertura deseada",
    ])


def _generar_objeciones(producto: str, texto: str) -> List[str]:
    """Genera objeciones frecuentes y cómo responderlas."""
    
    objeciones_base = {
        "salud": [
            "Ya tengo seguridad social",
            "Es muy caro para lo que cubre",
            "Tengo preexistencias y no me van a cubrir",
            "Prefiero esperar en la pública antes que pagar",
            "Ya tengo seguro médico en el trabajo",
        ],
        "vida": [
            "Soy joven y no lo necesito",
            "Es caro y no me va a pasar nada",
            "Ya tengo uno con la hipoteca",
            "Prefiero ahorrar ese dinero",
        ],
        "dental": [
            "Me sale más barato pagar las visitas por separado",
            "Solo voy al dentista una vez al año",
            "Mi seguro médico ya cubre dental básico",
        ],
        "mascotas": [
            "Mi mascota está sana, no lo necesita",
            "Es caro para un animal",
            "Ya tengo un fondo de ahorro para emergencias",
        ],
        "decesos": [
            "Es un tema del que no quiero hablar",
            "Ya tengo uno contratado",
            "Mis hijos se harán cargo",
            "Es dinero tirado, no me lo voy a gastar yo",
        ],
        "autonomos": [
            "Ya cotizo bastante a la seguridad social",
            "Es un gasto más que no me puedo permitir",
            "Nunca he tenido un problema",
            "Ya tengo un seguro a través de mi asociación",
        ],
        "extranjeria": [
            "Voy a volver a mi país en unos meses",
            "La sanidad pública es gratuita para todos",
            "Mi seguro de viaje me cubre",
        ],
        "accidentes": [
            "A mí no me va a pasar nada",
            "Mi trabajo no es de riesgo",
            "Ya tengo cobertura por accidentes laborales",
        ],
        "juridica": [
            "Nunca he tenido problemas legales",
            "Es caro para lo que ofrece",
            "Si tengo un problema, busco un abogado entonces",
        ],
    }
    
    return objeciones_base.get(producto, [
        "No lo necesito",
        "Es demasiado caro",
        "Ya tengo algo similar",
    ])


def _generar_limites(producto: str) -> List[str]:
    """Genera límites: qué NO debe prometer el agente."""
    
    limites_base = {
        "salud": [
            "No prometer que todas las preexistencias estarán cubiertas (depende del asegurador)",
            "No garantizar tiempos de espera concretos para cirugías",
            "No comparar directamente con otras aseguradoras mencionando nombres",
            "No asegurar que un tratamiento concreto estará cubierto sin verificarlo antes",
        ],
        "vida": [
            "No prometer que la cobertura por suicidio está incluida (tiene carencia legal de 1 año)",
            "No dar cifras exactas de prima sin tener todos los datos",
            "No asegurar que cubre cualquier profesión de riesgo",
        ],
        "dental": [
            "No prometer que todos los tratamientos estético-dentales están cubiertos",
            "No dar presupuestos exactos de tratamientos sin ver la póliza concreta",
            "No asegurar tiempos de espera para citas",
        ],
        "mascotas": [
            "No prometer que cubre enfermedades preexistentes",
            "No asegurar que todas las razas están aceptadas",
            "No garantizar que cubre tratamientos estéticos",
        ],
        "decesos": [
            "No prometer servicios funerarios específicos sin verificarlo en la póliza",
            "No dar precios exactos sin tener todos los datos del tomador",
            "No asegurar que cubre fallecimiento en el extranjero sin verificarlo",
        ],
        "autonomos": [
            "No prometer que cubre todas las actividades profesionales",
            "No dar cifras exactas sin conocer el epígrafe IAE y facturación",
            "No asegurar que la cobertura de baja laboral se activa desde el día 1",
        ],
        "extranjeria": [
            "No prometer que cubre todas las nacionalidades",
            "No asegurar que sirve para todos los trámites de residencia",
            "No garantizar atención en todos los idiomas",
        ],
        "accidentes": [
            "No prometer que cubre todos los deportes de riesgo",
            "No asegurar que la cobertura es mundial sin verificarlo",
            "No dar capitales exactos sin conocer la actividad",
        ],
        "juridica": [
            "No prometer que cubre cualquier tipo de conflicto legal",
            "No asegurar resultados concretos en juicios",
            "No garantizar que cubre litigios ya iniciados",
        ],
    }
    
    return limites_base.get(producto, [
        "No prometer coberturas sin verificarlas en la póliza",
        "No dar precios exactos sin tener todos los datos del cliente",
        "No comparar con otras compañías mencionando nombres",
    ])


def _generar_derivacion(producto: str) -> List[str]:
    """Genera condiciones para derivar a un agente humano."""
    
    derivar_base = {
        "salud": [
            "El cliente tiene enfermedades preexistentes complejas o múltiples",
            "El cliente pide comparativa detallada entre varias aseguradoras",
            "El cliente quiere un seguro para un colectivo o empresa",
            "El cliente solicita información sobre hospitales o cuadros médicos específicos",
            "El cliente quiere negociar condiciones especiales",
        ],
        "vida": [
            "El cliente tiene más de 65 años",
            "El cliente tiene enfermedades graves diagnosticadas",
            "El cliente quiere un capital asegurado superior a 300.000€",
            "El cliente tiene profesiones de alto riesgo no cubiertas",
        ],
        "dental": [
            "El cliente necesita un presupuesto detallado para un tratamiento complejo",
            "El cliente tiene múltiples tratamientos pendientes",
            "El cliente quiere un seguro dental para toda una empresa",
        ],
        "mascotas": [
            "La mascota tiene una enfermedad crónica o condición preexistente",
            "La mascota es de una raza considerada peligrosa (PPP)",
            "El cliente tiene más de 3 mascotas",
        ],
        "decesos": [
            "El cliente quiere personalizar el servicio funerario al detalle",
            "El cliente tiene más de 80 años",
            "El cliente quiere incluir coberturas muy específicas",
        ],
        "autonomos": [
            "El cliente tiene una actividad profesional de alto riesgo no tipificada",
            "El cliente factura más de 300.000€ anuales",
            "El cliente quiere un seguro a medida para su sector específico",
        ],
        "extranjeria": [
            "El cliente no tiene NIE ni documentación española",
            "El cliente tiene una enfermedad preexistente grave",
            "El cliente necesita el seguro para un visado específico no estándar",
        ],
        "accidentes": [
            "El cliente practica deportes de riesgo extremo no cubiertos",
            "El cliente quiere un capital asegurado muy elevado",
            "El cliente ha tenido múltiples siniestros previos",
        ],
        "juridica": [
            "El cliente ya tiene un litigio en curso",
            "El cliente necesita defensa penal",
            "El cliente es una empresa con necesidades legales complejas",
        ],
    }
    
    return derivar_base.get(producto, [
        "El cliente tiene una situación compleja que requiere asesoramiento personalizado",
        "El cliente solicita condiciones especiales fuera de lo estándar",
        "El cliente no queda satisfecho con las opciones presentadas",
    ])


# ── Búsqueda de PDFs ─────────────────────────────────
def find_pdfs(data_dir: Path) -> List[Path]:
    """Encuentra todos los PDFs en el directorio (incluyendo subcarpetas)."""
    pdfs = []
    for p in sorted(data_dir.rglob("*.pdf")):
        pdfs.append(p)
    for p in sorted(data_dir.rglob("*.txt")):
        pdfs.append(p)
    return pdfs


# ── Proceso principal ────────────────────────────────
def run_extraction(
    data_dir: Path,
    output_path: Path,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Ejecuta la extracción de playbooks desde PDFs.
    
    Args:
        data_dir: Directorio con PDFs (puede tener subcarpetas por producto)
        output_path: Ruta del JSON de salida
        dry_run: Si True, no escribe el archivo de salida
    
    Returns:
        Dict con resumen de la operación
    """
    if not data_dir.exists():
        logger.error("Directorio no encontrado: %s", data_dir)
        sys.exit(1)
    
    pdfs = find_pdfs(data_dir)
    if not pdfs:
        logger.warning("No se encontraron PDFs en %s", data_dir)
        return {"files": 0, "productos": 0, "playbooks": []}
    
    logger.info("Encontrados %d PDFs en %s", len(pdfs), data_dir)
    
    playbooks = []
    productos_vistos = set()
    
    for filepath in pdfs:
        producto = detect_producto(filepath)
        logger.info("Procesando: %s → producto=%s", filepath.name, producto)
        
        if dry_run:
            logger.info("  [DRY] Producto detectado: %s", producto)
            playbook = {
                "producto": producto,
                "source_file": filepath.name,
                "resumen_comercial": "[DRY RUN - no se extrajo texto]",
                "perfil_objetivo": [],
                "preguntas_iniciales": [],
                "datos_minimos": [],
                "objeciones_frecuentes": [],
                "limites": [],
                "cuando_derivar_humano": [],
            }
            playbooks.append(playbook)
            productos_vistos.add(producto)
            continue
        
        # Extraer texto
        if filepath.suffix.lower() == ".txt":
            try:
                texto = filepath.read_text(encoding="utf-8", errors="ignore")
                texto = sanitize_brands(texto.strip())
            except Exception as e:
                logger.error("Error leyendo %s: %s", filepath.name, e)
                continue
        else:
            texto = extract_text(filepath)
        
        if not texto:
            logger.warning("Sin texto extraído de %s — usando playbook genérico", filepath.name)
            # Generar playbook genérico para este producto
            playbook = generar_playbook(producto, "", filepath.name)
        else:
            playbook = generar_playbook(producto, texto, filepath.name)
        
        playbooks.append(playbook)
        productos_vistos.add(producto)
        logger.info("  ✓ Playbook generado para %s", producto)
    
    # Si hay productos sin PDF, generar playbooks genéricos
    for prod in PRODUCTOS:
        if prod not in productos_vistos:
            logger.info("Generando playbook genérico para %s (sin PDF)", prod)
            playbook = generar_playbook(prod, "", f"generico_{prod}.txt")
            playbooks.append(playbook)
    
    # Consolidar: si hay múltiples playbooks para el mismo producto, fusionarlos
    playbooks_consolidados = _consolidar_playbooks(playbooks)
    
    # Validar que ningún playbook contenga marcas de aseguradoras
    total_warnings = 0
    for pb in playbooks_consolidados:
        warnings = validate_playbook_brands(pb)
        for w in warnings:
            logger.warning(w)
            total_warnings += 1
    if total_warnings > 0:
        logger.warning("VALIDATION: %d campos contienen marcas de aseguradoras en los playbooks", total_warnings)
    else:
        logger.info("VALIDATION: Todos los playbooks están libres de marcas de aseguradoras ✓")
    
    output = {
        "metadata": {
            "generado": "extract_product_rules.py",
            "total_pdfs": len(pdfs),
            "total_productos": len(playbooks_consolidados),
            "productos_con_pdf": list(productos_vistos),
            "brand_validation_warnings": total_warnings,
        },
        "productos": playbooks_consolidados,
    }
    
    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        logger.info("Playbooks guardados en: %s", output_path)
    else:
        logger.info("[DRY] No se guardó el archivo de salida")
        # Mostrar preview
        for pb in playbooks_consolidados[:2]:
            logger.info("[DRY] Preview: %s → %s", pb["producto"], pb["resumen_comercial"][:80])
    
    return {
        "files": len(pdfs),
        "productos": len(playbooks_consolidados),
        "playbooks": playbooks_consolidados,
    }


def _consolidar_playbooks(playbooks: List[Dict]) -> List[Dict]:
    """
    Si hay múltiples playbooks para el mismo producto, los fusiona.
    - resumen: se queda con el más largo
    - listas: se unen sin duplicados
    """
    consolidados: Dict[str, Dict] = {}
    
    for pb in playbooks:
        prod = pb["producto"]
        if prod not in consolidados:
            consolidados[prod] = dict(pb)
        else:
            existente = consolidados[prod]
            # Resumen: el más largo
            if len(pb.get("resumen_comercial", "")) > len(existente.get("resumen_comercial", "")):
                existente["resumen_comercial"] = pb["resumen_comercial"]
            # Listas: unir sin duplicados
            for campo in ["perfil_objetivo", "preguntas_iniciales", "datos_minimos",
                          "objeciones_frecuentes", "limites", "cuando_derivar_humano"]:
                existentes = set(existente.get(campo, []))
                nuevos = [item for item in pb.get(campo, []) if item not in existentes]
                existente[campo] = existente.get(campo, []) + nuevos
    
    return list(consolidados.values())


# ── CLI ──────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genera playbooks comerciales desde PDFs de producto"
    )
    parser.add_argument("--data_dir", default=DEFAULT_DATA_DIR, help="Directorio con PDFs de productos")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Ruta del JSON de salida")
    parser.add_argument("--dry_run", action="store_true", help="Simular sin escribir archivo")
    args = parser.parse_args()

    run_extraction(
        data_dir=Path(args.data_dir),
        output_path=Path(args.output),
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
