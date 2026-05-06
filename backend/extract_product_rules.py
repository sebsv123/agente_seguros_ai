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

# Marcas a sanitizar (NUNCA mencionar en outputs)
BRAND_PATTERNS = [
    (re.compile(r"\bASISA\b", re.I), "la aseguradora"),
    (re.compile(r"\bMapfre\b", re.I), "la compañía"),
    (re.compile(r"\bSanitas\b", re.I), "la aseguradora"),
    (re.compile(r"\bAdeslas\b", re.I), "la compañía"),
    (re.compile(r"\bDKV\b", re.I), "la aseguradora"),
    (re.compile(r"\bAXA\b", re.I), "la compañía"),
    (re.compile(r"\bCigna\b", re.I), "la aseguradora"),
    (re.compile(r"\bCaser\b", re.I), "la compañía"),
    (re.compile(r"\bMutua Madrileña\b", re.I), "la aseguradora"),
    (re.compile(r"\bMutua\b", re.I), "la aseguradora"),
    (re.compile(r"\bAllianz\b", re.I), "la compañía"),
    (re.compile(r"\bZurich\b", re.I), "la aseguradora"),
    (re.compile(r"\bGenerali\b", re.I), "la compañía"),
    (re.compile(r"\bFIATC\b", re.I), "la aseguradora"),
    (re.compile(r"\bLínea Directa\b", re.I), "la compañía"),
    (re.compile(r"\bLinea Directa\b", re.I), "la compañía"),
    (re.compile(r"\bPelayo\b", re.I), "la aseguradora"),
    (re.compile(r"\bReale\b", re.I), "la compañía"),
    (re.compile(r"\bSegurCaixa\b", re.I), "la aseguradora"),
    (re.compile(r"\bBBVA\b", re.I), "el banco"),
    (re.compile(r"\bSantander\b", re.I), "el banco"),
    (re.compile(r"\bING\b", re.I), "el banco"),
]


def sanitize_brands(text: str) -> str:
    """Reemplaza nombres de marcas por términos genéricos."""
    for pattern, replacement in BRAND_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


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


def extract_text(filepath: Path) -> Optional[str]:
    """Extrae texto de un PDF, probando múltiples extractores."""
    text = extract_text_pypdf(filepath)
    if not text:
        text = extract_text_pdfplumber(filepath)
    if not text:
        logger.error("No se pudo extraer texto de %s", filepath.name)
        return None
    # Limpiar texto
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return sanitize_brands(text.strip())


# ── Generación de playbook ────────────────────────────
def generar_playbook(producto: str, texto: str, source_file: str) -> Dict[str, Any]:
    """
    Genera un playbook estructurado a partir del texto extraído del PDF.
    
    Usa reglas heurísticas + extracción de secciones del PDF para construir
    el playbook. En una versión futura se podría usar un LLM para mejorar
    la extracción semántica.
    """
    texto_lower = texto.lower()
    
    # --- resumen_comercial ---
    # Intentar extraer las primeras líneas con pinta de resumen/descripción
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
    }


def _extraer_resumen(texto: str, producto: str) -> str:
    """Extrae un resumen comercial de 1-3 frases."""
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    
    # Buscar líneas que parezcan un resumen/descripción del producto
    keywords_resumen = [
        "seguro", "cobertura", "protección", "plan", "póliza",
        "incluye", "ofrece", "garantiza", "cubre"
    ]
    
    candidatas = []
    for i, linea in enumerate(lineas):
        linea_lower = linea.lower()
        # Saltar encabezados, pies de página, números de página
        if len(linea_lower) < 20 or len(linea_lower) > 500:
            continue
        if any(kw in linea_lower for kw in keywords_resumen):
            # Preferir líneas al principio del documento
            peso = max(0, 100 - i)
            candidatas.append((peso, linea))
    
    if candidatas:
        candidatas.sort(reverse=True)
        # Tomar las 2-3 mejores
        mejores = [c[1] for c in candidatas[:3]]
        resumen = " ".join(mejores)
        # Acortar a máximo 3 frases
        frases = re.split(r'(?<=[.!?])\s+', resumen)
        resumen = " ".join(frases[:3])
        return resumen[:500]
    
    # Fallback: primeras líneas con sentido
    for linea in lineas[:20]:
        if len(linea) > 30 and len(linea) < 400:
            return linea[:400]
    
    return f"Seguro de {producto} con diversas coberturas adaptadas a las necesidades del cliente."


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
    
    output = {
        "metadata": {
            "generado": "extract_product_rules.py",
            "total_pdfs": len(pdfs),
            "total_productos": len(playbooks_consolidados),
            "productos_con_pdf": list(productos_vistos),
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
