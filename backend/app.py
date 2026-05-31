"""
Rosa — Agente de seguros IG→WhatsApp
FastAPI + psycopg3 + OpenAI (opcional) + n8n + estado persistente en Postgres

Incluye:
- Flujos por slots (salud) con copago + preexistencias
- WhatsApp link con prefill (sin “copia/pega”)
- Logs seguros (no imprime valores sensibles)
- Province resolver con CP + heurística + LLM opcional + cache
- RAG (KB en Postgres/pgvector) para responder preguntas de producto en cualquier punto del flujo
- Endpoints KB: /kb/stats, /kb/search, /kb/ingest
- UPGRADE (Producción): Lead scoring + Auto WhatsApp en el mejor momento
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import json
import logging
import os
import random
import re
import requests
import threading
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
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request as URequest, urlopen

from system_prompt import get_system_prompt, get_system_prompt_en

import psycopg
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request as FastAPIRequest
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

load_dotenv()

import json as _json_mod

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "time":    self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
            "file":    f"{record.filename}:{record.lineno}",
        }
        if record.exc_info:
            log_obj["exc"] = self.formatException(record.exc_info)
        return _json_mod.dumps(log_obj, ensure_ascii=False)

_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])

logger = logging.getLogger("rosa")

try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    _HAS_ST = True
except Exception:
    np = None  # type: ignore
    SentenceTransformer = None  # type: ignore
    _HAS_ST = False

try:
    from openai import OpenAI as _OpenAIClient
    _HAS_OPENAI = True
except Exception:
    _OpenAIClient = None  # type: ignore
    _HAS_OPENAI = False

from contextlib import asynccontextmanager

def wait_for_db(max_retries: int = 10, delay_s: float = 3.0) -> bool:
    """
    Espera a que la DB esté disponible antes de hacer bootstrap.
    Reintenta max_retries veces con delay_s segundos entre intentos.
    """
    for attempt in range(1, max_retries + 1):
        try:
            with db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            logger.info("DB lista (intento %d/%d)", attempt, max_retries)
            return True
        except Exception as e:
            logger.warning(
                "DB no disponible (intento %d/%d): %s — reintentando en %.0fs",
                attempt, max_retries, e, delay_s
            )
            time.sleep(delay_s)
    logger.error("DB no disponible tras %d intentos — abortando bootstrap", max_retries)
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ──────────────────────────────
    logger.info("STARTUP esperando DB...")
    if wait_for_db(max_retries=10, delay_s=3.0):
        try:
            bootstrap_schema()
            logger.info("STARTUP bootstrap OK")
        except Exception as e:
            logger.error("STARTUP bootstrap FAILED: %s", e)
    else:
        logger.error("STARTUP sin DB — el agente arranca SIN schema")

    if embedder is not None:
        logger.info("STARTUP embedder OK")
    else:
        logger.warning("STARTUP embedder NO disponible")

    if _ai_client is not None:
        logger.info("STARTUP AI client OK")
    else:
        logger.warning("STARTUP AI client NO disponible")

    logger.info("STARTUP agente Rosa listo ✓")
    yield
    logger.info("SHUTDOWN agente Rosa detenido")


app = FastAPI(
    title="Agente Rosa — Seguros IG→WhatsApp",
    version="2.0.0",
    lifespan=lifespan,
)

# ══════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════

DB_DSN                  = os.getenv("DB_DSN", "postgresql://agente:agente_pw@localhost:5433/agente_ai")
META_VERIFY_TOKEN       = os.getenv("META_VERIFY_TOKEN", "")
META_PAGE_ACCESS_TOKEN  = os.getenv("META_PAGE_ACCESS_TOKEN", "")
META_APP_SECRET         = os.getenv("META_APP_SECRET", "")
META_SIGNATURE_MODE     = os.getenv("META_SIGNATURE_MODE", "dev").strip().lower()
DEFAULT_WA_PHONE_E164   = os.getenv("DEFAULT_WA_PHONE_E164", "34603448765")

OPENAI_API_KEY          = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL            = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

META_ACCESS_TOKEN       = os.getenv("META_ACCESS_TOKEN", META_PAGE_ACCESS_TOKEN)
META_IG_SENDER_ENDPOINT = os.getenv("META_IG_SENDER_ENDPOINT", "")
N8N_WEBHOOK_URL         = os.getenv("N8N_WEBHOOK_URL", "")
N8N_TIMEOUT             = int(os.getenv("N8N_TIMEOUT", "8"))
GROQ_API_KEY            = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL              = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
WA_TOKEN                = os.getenv("WA_TOKEN", "")
WA_PHONE_NUMBER_ID      = os.getenv("WA_PHONE_NUMBER_ID", "")
META_WA_TOKEN           = os.getenv("META_WA_TOKEN", "")
TESSERACT_PATH          = os.getenv("TESSERACT_PATH", "/usr/bin/tesseract")

# KB / RAG
KB_SCORE_THRESHOLD      = float(os.getenv("KB_SCORE_THRESHOLD", "0.55"))
KB_TOP_K                = int(os.getenv("KB_TOP_K", "5"))
KB_ADMIN_TOKEN          = os.getenv("KB_ADMIN_TOKEN", "")  # opcional
KB_DATA_DIR             = os.getenv("KB_DATA_DIR", "./data")

# Upgrade: Lead scoring + Auto WA
AUTO_WA_ENABLED         = os.getenv("AUTO_WA_ENABLED", "1").strip() not in {"0", "false", "no"}
AUTO_WA_SCORE_THRESHOLD = int(os.getenv("AUTO_WA_SCORE_THRESHOLD", "6"))  # recomendado 6-8
AUTO_WA_MIN_GATES       = os.getenv("AUTO_WA_MIN_GATES", "1").strip() not in {"0", "false", "no"}

ALLOWED_SOURCE_CHANNELS  = {"instagram_dm", "whatsapp", "web"}
LEAD_UUID_NAMESPACE      = uuid.UUID("4c1f6a2d-8a93-4b8e-8a3b-6e2ce0fb3e31")
IG_UUID_NAMESPACE        = uuid.UUID("2f8b6b0b-7bfe-4d3b-8c2f-13df1d4c7d10")

AGENT_NAME               = "Rosa"
LAG_MIN_S                = 4.0
LAG_MAX_S                = 12.0
WA_COOLDOWN_HOURS        = 6
NON_INS_TURNS_THRESHOLD  = 2
META_BLOCKED_TTL_S       = 3600
FOLLOWUP_DELAY_HOURS     = 48
FOLLOWUP_MAX_ATTEMPTS    = 2

# ── Mensajes de bienvenida y cierre ────────────────────
WELCOME_MESSAGE = """Hola 👋 Gracias por escribir a *Valentín Protección Integral*.

En breves momentos te atendemos personalmente. Mientras tanto, cuéntanos qué necesitas para preparar tu caso desde ya 👇

¿Para qué tipo de seguro te podemos ayudar?

🏥 *Salud* — particulares, autónomos o empresas
🌍 *Visados y extranjería* — NIE, TIE, residencia
🎓 *Estudiantes*
🦷 *Dental*
⚡ *Accidentes*
🐾 *Mascotas*

Solo dinos cuál y empezamos 🙏"""

WELCOME_MESSAGE_EN = """Hi 👋 Thank you for contacting *Valentín Protección Integral*.

We'll be with you personally very shortly. In the meantime, tell us what you need so we can prepare your case right away 👇

What type of insurance are you looking for?

🏥 *Health* — individuals, self-employed or businesses
🌍 *Visa & immigration* — NIE, TIE, residence permit
🎓 *Students*
🦷 *Dental*
⚡ *Accidents*
🐾 *Pets*

Just tell us which one and we'll get started 🙏"""

HANDOFF_MESSAGE_ES = """Perfecto, ya tengo todo lo necesario 🙌

Rosa o Sebastián te contactan personalmente en breve para darte las opciones exactas para tu caso.

¡Hasta ahora! 😊"""

HANDOFF_MESSAGE_EN = """Perfect, I have everything I need 🙌

Rosa or Sebastián will contact you personally shortly to give you the exact options for your case.

Talk soon! 😊"""

# ── Flujos de steps por perfil ──────────────────────────
STEPS_GENERAL = [
    "product_interest",
    "ask_coverage_type",
    "ask_name",
    "ask_birth_date",
    "ask_province",
    "ask_email",
    "handoff",
]

STEPS_EXTRANJERIA = [
    "product_interest",
    "ask_purpose",
    "ask_name",
    "ask_birth_date",
    "ask_num_people",
    "ask_email",
    "ask_address",
    "ask_passport",
    "handoff",
]

MAX_AGENT_TURNS_GENERAL     = 5
MAX_AGENT_TURNS_EXTRANJERIA = 7

# Google Calendar (opcional)
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
_HAS_GCAL = bool(GOOGLE_CALENDAR_ID and GOOGLE_SERVICE_ACCOUNT_JSON)

# Anti-spam / rate limit
_RATE_LIMIT: Dict[str, List[float]] = {}
RATE_LIMIT_WINDOW_S      = 10
RATE_LIMIT_MAX_MSGS      = 5

LOG_SAFE_SLOTS: frozenset = frozenset({
    "product_interest", "province", "num_people", "ages",
    "budget", "name", "copay_preference", "has_preexisting",
})

_DEDUP_CACHE: Dict[str, Tuple[float, str]] = {}
_DEDUP_TTL   = 30
_META_BLOCKED: Dict[str, float] = {}

# Embedder global (si hay ST)
embedder = None
if _HAS_ST:
    try:
        embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        logger.info("Embedder listo (all-MiniLM-L6-v2)")
    except Exception as e:
        logger.warning("Embedder init failed: %s", e)
        embedder = None

# AI client (DeepSeek → Groq → OpenAI)
_ai_client = None
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
if DEEPSEEK_API_KEY:
    try:
        from openai import OpenAI as _OpenAIWrapper
        _ai_client = _OpenAIWrapper(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")
        logger.info("DeepSeek client listo (model=%s)", DEEPSEEK_MODEL)
    except Exception as e:
        logger.warning("DeepSeek init failed: %s", e)
if _ai_client is None and GROQ_API_KEY:
    try:
        from openai import OpenAI as _OpenAIWrapper
        _ai_client = _OpenAIWrapper(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
        logger.info("Groq client listo (model=%s)", GROQ_MODEL)
    except Exception as e:
        logger.warning("Groq init failed: %s", e)
if _ai_client is None and _HAS_OPENAI and OPENAI_API_KEY:
    try:
        _ai_client = _OpenAIClient(api_key=OPENAI_API_KEY)
        logger.info("OpenAI client listo (model=%s)", OPENAI_MODEL)
    except Exception as e:
        logger.warning("OpenAI init failed: %s", e)

# ══════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════

_IG_SOURCES: frozenset = frozenset({"instagram_dm", "ig", "ig_dm", "dm", "instagram"})
_WA_SOURCES: frozenset = frozenset({"whatsapp", "wa", "wsp", "whats"})

def channel_from_source(source: str) -> str:
    s = (source or "").strip().lower()
    if s in _IG_SOURCES: return "ig"
    if s in _WA_SOURCES: return "wa"
    return "ig"

def _conversations_order_by_sql(conn: psycopg.Connection) -> str:
    """Detecta qué columna usar para ORDER BY en conversations."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'conversations'")
            cols = {r[0] for r in cur.fetchall()}
            if "id" in cols: return "id"
            if "created_at" in cols: return "created_at"
            if "timestamp" in cols: return "timestamp"
            if "inserted_at" in cols: return "inserted_at"
            return "ctid"
    except Exception:
        return "ctid"

def db_connect():
    return psycopg.connect(DB_DSN)

def is_uuid(s: str) -> bool:
    try:
        uuid.UUID(str(s))
        return True
    except Exception:
        return False

def normalize_source_channel(s: Optional[str]) -> str:
    t = (s or "").strip().lower()
    if t in {"manual_test", "test", "curl", "n8n"}:             return "web"
    if t in {"ig", "instagram", "instagram_dm", "ig_dm", "dm"}: return "instagram_dm"
    if t in {"wa", "whatsapp", "whats"}:                        return "whatsapp"
    if t in ALLOWED_SOURCE_CHANNELS:                            return t
    return "web"

def normalize_lead_id(lead_id: Optional[str], ig_user_id: Optional[str]) -> str:
    if ig_user_id:
        return str(uuid.uuid5(IG_UUID_NAMESPACE, f"ig:{ig_user_id}"))
    if lead_id:
        return (
            str(uuid.UUID(lead_id)) if is_uuid(lead_id)
            else str(uuid.uuid5(LEAD_UUID_NAMESPACE, f"lead:{lead_id}"))
        )
    return str(uuid.uuid4())

def lead_id_from_ig_user(ig_user_id: str) -> str:
    return str(uuid.uuid5(IG_UUID_NAMESPACE, f"ig:{ig_user_id}"))

# ══════════════════════════════════════════════════════
# SCHEMA BOOTSTRAP (incluye KB y conversations)
# ══════════════════════════════════════════════════════

def bootstrap_schema() -> None:
    ddl_ext_uuid   = 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
    ddl_ext_vector = 'CREATE EXTENSION IF NOT EXISTS vector;'

    ddl_leads = """
    CREATE TABLE IF NOT EXISTS leads (
        lead_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        source_channel   VARCHAR(50)  DEFAULT 'web',
        source_campaign  VARCHAR(120),
        source_adset     VARCHAR(120),
        source_ad        VARCHAR(120),
        category         VARCHAR(50)  DEFAULT 'salud',
        last_activity_at TIMESTAMPTZ  DEFAULT now(),
        created_at       TIMESTAMPTZ  DEFAULT now()
    );"""

    ddl_profile = """
    CREATE TABLE IF NOT EXISTS lead_profile (
        lead_id                   UUID PRIMARY KEY
                                  REFERENCES leads(lead_id) ON DELETE CASCADE,
        province                  VARCHAR(100),
        num_insured               INT,
        ages                      JSONB,
        copay_preference          VARCHAR(30),
        has_preexisting           BOOLEAN,
        preexisting_details       TEXT,
        updated_at                TIMESTAMPTZ DEFAULT now()
    );"""

    ddl_state = """
    CREATE TABLE IF NOT EXISTS conversation_state (
        lead_id       UUID         PRIMARY KEY,
        ig_user_id    VARCHAR(50),
        channel       VARCHAR(20)  DEFAULT 'ig',
        source        VARCHAR(30)  DEFAULT 'web',
        step          VARCHAR(60)  DEFAULT 'product_interest',
        slots         JSONB        DEFAULT '{}'::jsonb,
        last_question TEXT,
        wa_sent_at    TIMESTAMPTZ,
        mode          VARCHAR(20)  DEFAULT 'idle',
        non_insurance_turns INT    DEFAULT 0,
        ab_version    VARCHAR(10)  DEFAULT 'A',
        updated_at    TIMESTAMPTZ  DEFAULT now()
    );"""

    ddl_lead_state = """
    CREATE TABLE IF NOT EXISTS lead_state (
        lead_id              UUID         PRIMARY KEY
                               REFERENCES leads(lead_id) ON DELETE CASCADE,
        fase                 VARCHAR(30)  DEFAULT 'nuevo',
        producto_detectado   VARCHAR(50),
        datos_recogidos      JSONB        DEFAULT '{}'::jsonb,
        mensajes_intercambiados INT       DEFAULT 0,
        ultimo_mensaje       TIMESTAMPTZ,
        derivado_a_humano    BOOLEAN      DEFAULT FALSE,
        notas                JSONB        DEFAULT '[]'::jsonb,
        created_at           TIMESTAMPTZ  DEFAULT now(),
        updated_at           TIMESTAMPTZ  DEFAULT now()
    );"""

    ddl_conversations = """
    CREATE TABLE IF NOT EXISTS conversations (
        id        BIGSERIAL PRIMARY KEY,
        lead_id   UUID NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
        channel   VARCHAR(20) NOT NULL,
        direction VARCHAR(10) NOT NULL,
        text      TEXT NOT NULL,
        intent    VARCHAR(80),
        created_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_conversations_lead
        ON conversations(lead_id, created_at DESC);
    """

    ddl_kb = """
    CREATE TABLE IF NOT EXISTS kb_documents (
        id          BIGSERIAL PRIMARY KEY,
        category    TEXT NOT NULL,
        route       TEXT,
        source_file TEXT NOT NULL,
        chunk_id    TEXT NOT NULL,
        chunk_text  TEXT NOT NULL,
        embedding   vector(384),
        created_at  TIMESTAMPTZ DEFAULT now(),
        UNIQUE(source_file, chunk_id)
    );
    CREATE INDEX IF NOT EXISTS idx_kb_category ON kb_documents(category);
    """

    ddl_place_cache = """
    CREATE TABLE IF NOT EXISTS place_cache (
        place_text TEXT PRIMARY KEY,
        province   TEXT NOT NULL,
        source     VARCHAR(20) DEFAULT 'llm',
        created_at TIMESTAMPTZ DEFAULT now()
    );
    """

    ddl_indexes = """
    CREATE INDEX IF NOT EXISTS idx_leads_activity ON leads(last_activity_at DESC);
    CREATE INDEX IF NOT EXISTS idx_conv_state_updated ON conversation_state(updated_at DESC);
    """

    with db_connect() as conn:
        with conn.cursor() as cur:
            for ddl in (
                ddl_ext_uuid, ddl_ext_vector,
                ddl_leads, ddl_profile, ddl_state, ddl_lead_state,
                ddl_conversations,
                ddl_kb, ddl_place_cache,
                ddl_indexes,
            ):
                cur.execute(ddl)
            # Migration check: add ab_version, scoring, followup, and human_released columns if missing
            try:
                cur.execute("ALTER TABLE conversation_state ADD COLUMN IF NOT EXISTS ab_version VARCHAR(10) DEFAULT 'A'")
                cur.execute("ALTER TABLE conversation_state ADD COLUMN IF NOT EXISTS followup_attempts INT DEFAULT 0")
                cur.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS lead_score SMALLINT DEFAULT 0")
                cur.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS temp_tag VARCHAR(20) DEFAULT 'cold'")
                cur.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS closure_prob DECIMAL(3,2) DEFAULT 0.00")
                cur.execute("ALTER TABLE lead_state ADD COLUMN IF NOT EXISTS human_released BOOLEAN DEFAULT FALSE")
            except Exception:
                pass
            # Migration: nuevos campos de perfil (v2)
            try:
                cur.execute("ALTER TABLE lead_profile ADD COLUMN IF NOT EXISTS email TEXT")
                cur.execute("ALTER TABLE lead_profile ADD COLUMN IF NOT EXISTS fecha_nacimiento_completa TEXT")
                cur.execute("ALTER TABLE lead_profile ADD COLUMN IF NOT EXISTS direccion_espana TEXT")
                cur.execute("ALTER TABLE lead_profile ADD COLUMN IF NOT EXISTS codigo_postal_espana TEXT")
                cur.execute("ALTER TABLE lead_profile ADD COLUMN IF NOT EXISTS num_pasaporte TEXT")
                cur.execute("ALTER TABLE lead_profile ADD COLUMN IF NOT EXISTS fecha_vencimiento_pasaporte TEXT")
                cur.execute("ALTER TABLE lead_profile ADD COLUMN IF NOT EXISTS foto_pasaporte_recibida BOOLEAN DEFAULT FALSE")
                cur.execute("ALTER TABLE lead_profile ADD COLUMN IF NOT EXISTS perfil_extranjeria BOOLEAN DEFAULT FALSE")
            except Exception:
                pass
        conn.commit()

    logger.info("Schema bootstrap OK (incl. KB)")

# ══════════════════════════════════════════════════════
# LOGGING + STATE
# ══════════════════════════════════════════════════════

def ensure_lead_row(lead_id: str, source_channel: str = "web", category: str = "salud") -> None:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO leads (lead_id, source_channel, category) "
                "VALUES (%s, %s, %s) ON CONFLICT (lead_id) DO NOTHING",
                (lead_id, normalize_source_channel(source_channel), category),
            )
        conn.commit()

def ensure_profile_row(lead_id: str) -> None:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lead_profile (lead_id) VALUES (%s) ON CONFLICT (lead_id) DO NOTHING",
                (lead_id,),
            )
        conn.commit()

def _coerce_channel(c: str) -> str:
    return c if c in ("ig", "wa") else "ig"

def _coerce_direction(d: str) -> str:
    return d if d in ("in", "out") else "out"

def log_event(
    lead_id: str, channel: str, direction: str,
    text: str, intent: Optional[str] = None,
) -> None:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversations (lead_id, channel, direction, text, intent) "
                "VALUES (%s, %s, %s, %s, %s)",
                (lead_id, _coerce_channel(channel), _coerce_direction(direction), text, intent),
            )
            cur.execute(
                "UPDATE leads SET last_activity_at = now() WHERE lead_id = %s",
                (lead_id,),
            )
        conn.commit()

def _safe_json(x: Any) -> Any:
    if x is None: return None
    if isinstance(x, (list, dict)): return x
    if isinstance(x, (bytes, bytearray)):
        try: x = x.decode("utf-8", errors="ignore")
        except Exception: return None
    if isinstance(x, str):
        try: return json.loads(x)
        except Exception: return None
    return None

def load_state(lead_id: str) -> dict:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT step, slots, last_question, ig_user_id, channel, source, wa_sent_at, mode, non_insurance_turns, ab_version "
                "FROM conversation_state WHERE lead_id = %s",
                (lead_id,),
            )
            row = cur.fetchone()
    if row:
        step, slots_raw, last_q, ig_uid, ch, src, wa_sent_at, mode, non_ins, ab_version = row
        return {
            "step": step or "product_interest",
            "slots": _safe_json(slots_raw) or {},
            "last_question": last_q or "",
            "ig_user_id": ig_uid or "",
            "channel": ch or "ig",
            "source": src or "web",
            "wa_sent_at": wa_sent_at,
            "mode": mode or "idle",
            "non_insurance_turns": int(non_ins or 0),
            "ab_version": ab_version or "A",
        }
    return {
        "step": "product_interest", "slots": {}, "last_question": "",
        "ig_user_id": "", "channel": "ig", "source": "web",
        "wa_sent_at": None, "mode": "idle", "non_insurance_turns": 0,
        "ab_version": random.choice(["A", "B"]),
    }

def save_state(lead_id: str, state: dict) -> None:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversation_state
                    (lead_id, step, slots, last_question,
                     ig_user_id, channel, source, wa_sent_at,
                     mode, non_insurance_turns, updated_at)
                VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (lead_id) DO UPDATE SET
                    step = EXCLUDED.step,
                    slots = EXCLUDED.slots,
                    last_question = EXCLUDED.last_question,
                    ig_user_id = EXCLUDED.ig_user_id,
                    channel = EXCLUDED.channel,
                    source = EXCLUDED.source,
                    wa_sent_at = EXCLUDED.wa_sent_at,
                    mode = EXCLUDED.mode,
                    non_insurance_turns = EXCLUDED.non_insurance_turns,
                    ab_version = EXCLUDED.ab_version,
                    updated_at = now()
                """,
                (
                    lead_id,
                    state.get("step", "product_interest"),
                    json.dumps(state.get("slots", {})),
                    state.get("last_question", ""),
                    state.get("ig_user_id", ""),
                    state.get("channel", "ig"),
                    state.get("source", "web"),
                    state.get("wa_sent_at"),
                    state.get("mode", "idle"),
                    state.get("non_insurance_turns", 0),
                    state.get("ab_version", "A"),
                ),
            )
        conn.commit()

def reset_state(lead_id: str) -> None:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM conversation_state WHERE lead_id = %s", (lead_id,))
        conn.commit()

# ══════════════════════════════════════════════════════
# LEAD STATE (fases persistentes)
# ══════════════════════════════════════════════════════

FASES = ["nuevo", "calificando", "datos_minimos", "listo_para_humano", "cerrado"]

LEAD_STATE_DEFAULT = {
    "fase": "nuevo",
    "producto_detectado": None,
    "datos_recogidos": {
        "nombre": None,
        "edad": None,
        "codigo_postal": None,
        "num_asegurados": None,
        "tiene_preexistencias": None,
        "email": None,
        "fecha_nacimiento_completa": None,
        "direccion_espana": None,
        "codigo_postal_espana": None,
        "num_pasaporte": None,
        "fecha_vencimiento_pasaporte": None,
        "foto_pasaporte_recibida": False,
        "perfil_extranjeria": False,
    },
    "mensajes_intercambiados": 0,
    "ultimo_mensaje": None,
    "derivado_a_humano": False,
    "human_released": False,
    "notas": [],
}


def init_lead_state(lead_id: str) -> None:
    """Crea fila lead_state si no existe."""
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lead_state (lead_id) VALUES (%s) ON CONFLICT (lead_id) DO NOTHING",
                (lead_id,),
            )
        conn.commit()


def load_lead_state(lead_id: str) -> dict:
    """Carga el lead_state desde PostgreSQL."""
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fase, producto_detectado, datos_recogidos, mensajes_intercambiados, "
                "ultimo_mensaje, derivado_a_humano, human_released, notas "
                "FROM lead_state WHERE lead_id = %s",
                (lead_id,),
            )
            row = cur.fetchone()
    if row:
        return {
            "fase": row[0] or "nuevo",
            "producto_detectado": row[1],
            "datos_recogidos": _safe_json(row[2]) or dict(LEAD_STATE_DEFAULT["datos_recogidos"]),
            "mensajes_intercambiados": int(row[3] or 0),
            "ultimo_mensaje": row[4],
            "derivado_a_humano": bool(row[5]),
            "human_released": bool(row[6]) if len(row) > 6 else False,
            "notas": _safe_json(row[7]) or [],
        }
    return dict(LEAD_STATE_DEFAULT)


def save_lead_state(lead_id: str, ls: dict) -> None:
    """Guarda el lead_state en PostgreSQL."""
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO lead_state
                    (lead_id, fase, producto_detectado, datos_recogidos,
                     mensajes_intercambiados, ultimo_mensaje, derivado_a_humano,
                     human_released, notas, updated_at)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT (lead_id) DO UPDATE SET
                    fase = EXCLUDED.fase,
                    producto_detectado = EXCLUDED.producto_detectado,
                    datos_recogidos = EXCLUDED.datos_recogidos,
                    mensajes_intercambiados = EXCLUDED.mensajes_intercambiados,
                    ultimo_mensaje = EXCLUDED.ultimo_mensaje,
                    derivado_a_humano = EXCLUDED.derivado_a_humano,
                    human_released = EXCLUDED.human_released,
                    notas = EXCLUDED.notas,
                    updated_at = now()
                """,
                (
                    lead_id,
                    ls.get("fase", "nuevo"),
                    ls.get("producto_detectado"),
                    json.dumps(ls.get("datos_recogidos", {})),
                    ls.get("mensajes_intercambiados", 0),
                    ls.get("ultimo_mensaje"),
                    ls.get("derivado_a_humano", False),
                    ls.get("human_released", False),
                    json.dumps(ls.get("notas", [])),
                ),
            )
        conn.commit()


def advance_lead_phase(lead_id: str, ls: dict, nueva_fase: str, nota: Optional[str] = None) -> dict:
    """
    Avanza el lead a una nueva fase si es válido.
    Devuelve el lead_state actualizado.
    """
    fases_ordenadas = {f: i for i, f in enumerate(FASES)}
    actual = fases_ordenadas.get(ls["fase"], -1)
    nueva = fases_ordenadas.get(nueva_fase, -1)
    
    if nueva < actual:
        logger.warning("LEAD_PHASE_REGRESS lead=%s %s -> %s (no permitido)", lead_id, ls["fase"], nueva_fase)
        return ls
    
    ls["fase"] = nueva_fase
    if nota:
        ls["notas"].append(nota)
    
    logger.info("LEAD_PHASE lead=%s %s -> %s", lead_id, FASES[actual] if actual >= 0 else "?", nueva_fase)
    save_lead_state(lead_id, ls)
    return ls


def _check_datos_minimos_completos(ls: dict) -> bool:
    """Verifica si los datos mínimos del lead están completos."""
    datos = ls.get("datos_recogidos", {})
    campos_requeridos = ["nombre", "edad", "codigo_postal"]
    for campo in campos_requeridos:
        if not datos.get(campo):
            return False
    return True


def _notify_human_handoff(lead_id: str, ls: dict, ultimo_texto: str, sender_id: str) -> None:
    """Notifica a los agentes humanos con un resumen limpio del lead."""
    slots = load_state(lead_id).get("slots", {})
    datos = ls.get("datos_recogidos", {})
    temp_tag = ls.get("temp_tag", "no evaluado")

    nombre   = slots.get("name") or datos.get("nombre") or "no indicado"
    email    = slots.get("email") or datos.get("email") or "no indicado"
    producto = ls.get("producto_detectado") or slots.get("product_interest") or "no detectado"
    provincia = slots.get("province") or datos.get("codigo_postal") or "no indicada"
    num_people = slots.get("num_people") or datos.get("num_asegurados") or "no indicado"
    ages = slots.get("ages") or datos.get("fecha_nacimiento_completa") or datos.get("edad") or "no indicadas"
    preex = slots.get("has_preexisting")
    preex_str = "sí" if preex is True else ("no" if preex is False else "no indicado")
    turnos = ls.get("mensajes_intercambiados", 0)
    es_extranjeria = bool(slots.get("perfil_extranjeria") or datos.get("perfil_extranjeria"))

    mensaje = (
        f"🔔 *LEAD LISTO — Valentín Protección Integral*\n\n"
        f"👤 Nombre: {nombre}\n"
        f"📱 Teléfono: wa.me/{sender_id}\n"
        f"📧 Email: {email}\n"
        f"📦 Producto: {producto}\n"
        f"🌡️ Score: {temp_tag}\n"
        f"📍 Zona: {provincia}\n"
        f"👥 Personas: {num_people}\n"
        f"🎂 Fechas de nacimiento: {ages}\n"
        f"🩺 Preexistencias: {preex_str}\n"
    )

    if es_extranjeria:
        direccion = datos.get("direccion_espana") or slots.get("direccion_espana") or "no indicada"
        pasaporte = datos.get("num_pasaporte") or slots.get("num_pasaporte") or "no indicado"
        vencimiento = datos.get("fecha_vencimiento_pasaporte") or slots.get("fecha_vencimiento_pasaporte") or "no indicada"
        foto_ok = bool(datos.get("foto_pasaporte_recibida") or slots.get("foto_pasaporte_recibida"))
        mensaje += (
            f"\n🏠 Dirección en España: {direccion}\n"
            f"🛂 Pasaporte: {pasaporte} | Vence: {vencimiento}\n"
            f"📎 Foto pasaporte: {'Sí ✅' if foto_ok else 'No ❌'}\n"
        )

    mensaje += (
        f"\n💬 Último mensaje: \"{ultimo_texto[:120]}\"\n"
        f"📊 Turnos: {turnos}\n\n"
        f"👉 Abrir chat: wa.me/{sender_id}"
    )

    logger.info("HANDOFF_NOTIFY lead=%s\n%s", lead_id, mensaje)

    try:
        _meta_send_wa(DEFAULT_WA_PHONE_E164, mensaje)
        logger.info("HANDOFF_NOTIFIED lead=%s to=%s", lead_id, DEFAULT_WA_PHONE_E164)
    except Exception as e:
        logger.error("HANDOFF_NOTIFY_ERR lead=%s %s", lead_id, e)


def update_lead_state_from_message(lead_id: str, text: str, sender_id: str, state: dict, ls: dict) -> dict:
    """
    Actualiza el lead_state basado en el mensaje actual y el estado de la conversación.
    Se llama en cada mensaje entrante.
    """
    ls["mensajes_intercambiados"] += 1
    ls["ultimo_mensaje"] = datetime.now()
    
    slots = state.get("slots", {})
    producto = slots.get("product_interest") or ls.get("producto_detectado")
    
    # Detectar producto
    if producto and not ls["producto_detectado"]:
        ls["producto_detectado"] = producto
        ls = advance_lead_phase(lead_id, ls, "calificando", f"Producto detectado: {producto}")
    
    # Actualizar datos recogidos desde slots
    datos = ls["datos_recogidos"]
    if slots.get("name"): datos["nombre"] = slots["name"]
    if slots.get("age") or slots.get("ages"):
        ages = slots.get("ages") or [slots.get("age")]
        if ages and isinstance(ages, list) and len(ages) > 0:
            datos["edad"] = str(ages[0])
    if slots.get("province") or slots.get("cp"):
        datos["codigo_postal"] = slots.get("cp") or slots.get("province")
    if slots.get("num_people"): datos["num_asegurados"] = int(slots["num_people"])
    if slots.get("has_preexisting") is not None: datos["tiene_preexistencias"] = bool(slots["has_preexisting"])
    
    # Avanzar fases según el estado
    if ls["fase"] == "calificando":
        # Si ya tenemos producto y estamos en flujo de datos
        if state.get("step") not in ("product_interest", None, ""):
            ls = advance_lead_phase(lead_id, ls, "datos_minimos", "Inicio recogida de datos")
    
    if ls["fase"] == "datos_minimos":
        if _check_datos_minimos_completos(ls) and not ls["derivado_a_humano"]:
            ls = advance_lead_phase(lead_id, ls, "listo_para_humano", "Datos mínimos completos")
            ls["derivado_a_humano"] = True
            _notify_human_handoff(lead_id, ls, text, sender_id)
            # Mensaje de cierre al cliente
            _lang = state.get("slots", {}).get("lang", "es") if state else "es"
            _handoff_msg = HANDOFF_MESSAGE_EN if _lang == "en" else HANDOFF_MESSAGE_ES
            log_event(lead_id, "ig", "out", _handoff_msg, intent="handoff_client_msg")
            try:
                _meta_send_wa(sender_id, _handoff_msg)
            except Exception:
                pass
            # Evaluar conversación al derivar
            try:
                from backend.agent_evaluator import evaluate_conversation
                evaluate_conversation(lead_id)
            except Exception as e:
                logger.warning("EVALUATE_ERR lead=%s %s", lead_id, e)
    
    # Si el lead dice que no le interesa
    text_lower = text.lower().strip()
    if any(p in text_lower for p in ["no me interesa", "no gracias", "no quiero", "déjalo", "no, gracias"]):
        ls = advance_lead_phase(lead_id, ls, "cerrado", f"Lead desistió: {text[:50]}")
        # Evaluar conversación al cerrar
        try:
            from backend.agent_evaluator import evaluate_conversation
            evaluate_conversation(lead_id)
        except Exception as e:
            logger.warning("EVALUATE_ERR lead=%s %s", lead_id, e)
    
    save_lead_state(lead_id, ls)
    return ls


def check_lead_expiry(lead_id: str, ls: dict) -> Optional[dict]:
    """
    Si el lead lleva más de 24h sin actividad, lo cierra.
    Si vuelve a escribir, se crea una nueva fase pero conserva historial.
    """
    if ls["fase"] == "cerrado":
        return None  # Ya está cerrado
    
    ultimo = ls.get("ultimo_mensaje")
    if ultimo:
        ahora = datetime.now()
        if isinstance(ultimo, str):
            try:
                from datetime import datetime as dt
                ultimo = dt.fromisoformat(ultimo.replace("Z", "+00:00"))
            except:
                ultimo = None
        if ultimo and (ahora - ultimo).total_seconds() > 86400:  # 24h
            ls = advance_lead_phase(None, ls, "cerrado", "Inactividad 24h+")
            return ls
    return None

def update_profile_from_slots(lead_id: str, slots: dict) -> None:
    FIELD_MAP: dict[str, tuple] = {
        "province":                    ("province",                    lambda v: str(v),        False),
        "num_people":                  ("num_insured",                 lambda v: int(v),        False),
        "ages":                        ("ages",                        lambda v: json.dumps(v), True),
        "has_preexisting":             ("has_preexisting",             lambda v: bool(v),       False),
        "preexisting_details":         ("preexisting_details",         lambda v: str(v),        False),
        "email":                       ("email",                       lambda v: str(v),        False),
        "fecha_nacimiento_completa":   ("fecha_nacimiento_completa",   lambda v: str(v),        False),
        "direccion_espana":            ("direccion_espana",            lambda v: str(v),        False),
        "codigo_postal_espana":        ("codigo_postal_espana",        lambda v: str(v),        False),
        "num_pasaporte":               ("num_pasaporte",               lambda v: str(v),        False),
        "fecha_vencimiento_pasaporte": ("fecha_vencimiento_pasaporte", lambda v: str(v),        False),
        "foto_pasaporte_recibida":     ("foto_pasaporte_recibida",     lambda v: bool(v),       False),
        "perfil_extranjeria":          ("perfil_extranjeria",          lambda v: bool(v),       False),
    }
    updates: list[tuple[str, Any, bool]] = []
    for slot_key, (col, coerce, is_json) in FIELD_MAP.items():
        if slots.get(slot_key) is not None:
            try:
                updates.append((col, coerce(slots[slot_key]), is_json))
            except Exception:
                pass
    if not updates:
        return

    ensure_profile_row(lead_id)
    with db_connect() as conn:
        with conn.cursor() as cur:
            for col, val, is_json in updates:
                if is_json:
                    cur.execute(
                        f"UPDATE lead_profile SET {col} = %s::jsonb, updated_at = now() WHERE lead_id = %s",
                        (val, lead_id),
                    )
                else:
                    cur.execute(
                        f"UPDATE lead_profile SET {col} = %s, updated_at = now() WHERE lead_id = %s",
                        (val, lead_id),
                    )
        conn.commit()

# ══════════════════════════════════════════════════════
# NORMALIZACIÓN TEXTO
# ══════════════════════════════════════════════════════

_ACCENT_MAP: dict[int, str] = {
    ord("á"): "a", ord("é"): "e", ord("í"): "i", ord("ó"): "o", ord("ú"): "u",
    ord("à"): "a", ord("è"): "e", ord("ì"): "i", ord("ò"): "o", ord("ù"): "u",
    ord("â"): "a", ord("ê"): "e", ord("î"): "i", ord("ô"): "o", ord("û"): "u",
    ord("ä"): "a", ord("ë"): "e", ord("ï"): "i", ord("ö"): "o", ord("ü"): "u",
    ord("Á"): "A", ord("É"): "E", ord("Í"): "I", ord("Ó"): "O", ord("Ú"): "U",
    ord("ñ"): "n", ord("Ñ"): "N", ord("ç"): "c", ord("Ç"): "C",
}

def nt(s: str) -> str:
    if not s: return ""
    return re.sub(r"\s+", " ", s.translate(_ACCENT_MAP).strip().lower())

# ══════════════════════════════════════════════════════
# BRAND SANITIZER (RAG output)
# ══════════════════════════════════════════════════════

_BRAND_RE = re.compile(
    r"\b(asisa|mapfre|sanitas|adeslas|dkv|axa|cigna|caser|allianz|zurich|generali|mutua)\b",
    re.I
)

def sanitize_brands(text: str) -> str:
    cleaned = _BRAND_RE.sub("la aseguradora", text)
    cleaned = re.sub(r"segun\s+la\s+aseguradora", "según la póliza", cleaned, flags=re.I)
    return cleaned

# ══════════════════════════════════════════════════════
# SLOT FLOW (multi-producto) + condicionales
# ══════════════════════════════════════════════════════

# Preguntas específicas por producto (Vida / Hogar)
_VIDA_QUESTIONS: Dict[str, str] = {
    "coverage_preferences": (
        "¿Qué tipo de cobertura buscas? 🛡️\n"
        "Ej: fallecimiento, invalidez, enfermedades graves...\n"
        "Si no estás seguro, escribe 'estándar'."
    ),
    "has_preexisting": (
        "¿Hay alguna enfermedad previa o condición médica relevante? 🩺\n"
        "(sí / no)"
    ),
    "budget": (
        "¿Tienes en mente algún presupuesto mensual? 💰\n"
        "Ej: hasta 30€, unos 50€, o 'no lo sé todavía'."
    ),
}

_HOGAR_QUESTIONS: Dict[str, str] = {
    "coverage_preferences": (
        "¿Qué coberturas son más importantes para ti? 🏠\n"
        "Ej: incendio, robo, daños por agua, responsabilidad civil...\n"
        "Si no estás seguro, escribe 'estándar'."
    ),
    "has_preexisting": (
        "¿Ha habido algún siniestro previo en la vivienda? 🏠\n"
        "(sí / no)"
    ),
    "budget": (
        "¿Tienes en mente algún presupuesto anual? 💰\n"
        "Ej: hasta 200€, unos 350€, o 'no lo sé todavía'."
    ),
}

_MASCOTAS_QUESTIONS: Dict[str, str] = {
    "pet_type": "¿Es para un perro 🐶 o un gato 🐱?",
    "pet_name": "¿Cómo se llama el afortunado/a? 😊",
    "pet_breed": "¿De qué raza es? (Si no la sabes exacta, pon 'cruce' o el tamaño)",
    "pet_age": "¿Qué edad tiene? (Aproximada me vale)",
    "budget": "¿Cuánto sueles gastar al año en veterinario? O dime un presupuesto que tengas en mente.",
}

_VIAJE_QUESTIONS: Dict[str, str] = {
    "travel_destination": "¿A dónde te vas de viaje? ✈️ (País o zona)",
    "travel_dates": "¿En qué fechas viajas? O dime cuántos días estaréis fuera.",
    "num_travelers": "¿Para cuántos viajeros sería el seguro? 👥",
    "coverage_preferences": "¿Buscas algo en especial? Repatriación, anulación, deportes de riesgo...",
}

_NEGOCIOS_QUESTIONS: Dict[str, str] = {
    "business_type": "¿A qué se dedica tu negocio? 💼 (Ej: oficina, tienda, taller...)",
    "annual_revenue": "¿Cuál es vuestra facturación anual aproximada? (Ayuda a ajustar la RC)",
    "num_employees": "¿Cuántos empleados sois aproximadamente?",
    "budget": "¿Cuál es vuestro presupuesto anual máximo para proteger el negocio?",
}

SLOT_FLOW: List[Dict[str, Any]] = [
    {
        "key": "product_interest",
        "required": True,
        "wa_required": False,
        "question": (
            "¡Genial! Me encanta ayudar con esto 😊 ¿Qué tipo de seguro te interesa?\n\n"
            "1) Salud 🩺\n"
            "2) Vida ❤️\n"
            "3) Hogar 🏠\n"
            "4) Mascotas 🐾\n"
            "5) Viaje ✈️\n"
            "6) Negocios 💼\n\n"
            "Dime el número o el tipo y seguimos."
        ),
    },
    {
        "key": "insurance_provider",
        "required": False,
        "wa_required": True,  # Solo en WhatsApp
        "question": (
            "Para darte el mejor precio, ¿tienes preferencia por alguna compañía? "
            "Trabajamos mucho con Adeslas y Asisa, que ahora mismo tienen ofertas geniales. "
            "¿Alguna te llama más la atención?"
        ),
    },
    {
        "key": "province",
        "required": True,
        "wa_required": True,
        "question": (
            "¿En qué provincia o ciudad de España vivís? 📍\n"
            "Si tienes el código postal (5 dígitos), mejor todavía."
        ),
    },
    {
        "key": "num_people",
        "required": True,
        "wa_required": True,
        "question": (
            "¿Para cuántas personas sería el seguro? 👥\n"
            "Ej: solo yo, mi pareja y yo, somos 3, para mi familia."
        ),
    },
    {
        "key": "ages",
        "required": True,
        "wa_required": True,
        "question": (
            "¿Cuáles son las edades aproximadas? 🎂\n"
            "Si son varias personas, sepáralas con comas — ej: 34, 32, 8."
        ),
    },
    {
        "key": "coverage_preferences",
        "required": True,
        "wa_required": False,
        "question": (
            "¿Alguna cobertura específica importante para ti? 🩺\n"
            "Ej: hospitalización, urgencias en el extranjero, reembolso...\n"
            "Si no tienes preferencia, escribe 'estándar'."
        ),
    },
    {
        "key": "has_preexisting",
        "required": True,
        "wa_required": False,
        "question": (
            "¿Hay alguna preexistencia o enfermedad previa importante entre las personas a asegurar? 🩺\n"
            "(sí / no)"
        ),
    },
    {
        "key": "preexisting_details",
        "required": False,
        "wa_required": False,
        "conditional": True,
        "question": (
            "¿Me dices cuál o cuáles? Una línea es suficiente 😊\n"
            "(ej: asma, diabetes, tiroides, hipertensión...)"
        ),
    },
    {
        "key": "budget",
        "required": True,
        "wa_required": False,
        "question": (
            "¿Tienes en mente algún presupuesto mensual aproximado? 💰\n"
            "Ej: hasta 50€, unos 100€, o 'no lo sé todavía'."
        ),
    },
    {
        "key": "name",
        "required": False,
        "wa_required": False,
        "question": "¿Cómo te llamas? (para personalizar la propuesta 😊)",
    },
    {
        "key": "phone_or_email",
        "required": False,
        "wa_required": False,
        "question": (
            "¿Me dejas un teléfono o email de contacto? (opcional)\n"
            "Si prefieres seguir solo por WhatsApp, escribe 'whatsapp'."
        ),
    },
    # --- MASCOTAS ---
    {"key": "pet_type", "required": True, "product": "mascotas", "question": "¿Es para un perro 🐶 o un gato 🐱?"},
    {"key": "pet_name", "required": True, "product": "mascotas", "question": "¿Cómo se llama tu compi?"},
    {"key": "pet_breed", "required": False, "product": "mascotas", "question": "¿De qué raza es? (Si es mestizo, pon 'cruce')"},
    {"key": "pet_age", "required": True, "product": "mascotas", "question": "¿Qué edad tiene?"},
    # --- VIAJE ---
    {"key": "travel_destination", "required": True, "product": "viaje", "question": "¿A dónde te vas de viaje? ✈️"},
    {"key": "travel_dates", "required": True, "product": "viaje", "question": "¿En qué fechas viajas? (O cuánto tiempo estarás)"},
    {"key": "num_travelers", "required": True, "product": "viaje", "question": "¿Cuántos viajeros sois?"},
    # --- NEGOCIOS ---
    {"key": "business_type", "required": True, "product": "negocios", "question": "¿A qué se dedica tu negocio? 💼"},
    {"key": "annual_revenue", "required": False, "product": "negocios", "question": "¿Cuál es vuestra facturación anual aproximada?"},
    {"key": "num_employees", "required": False, "product": "negocios", "question": "¿Cuántos empleados sois?"},
    # --- CIERRE / CITAS ---
    {
        "key": "appointment_requested",
        "required": False,
        "question": "¿Te gustaría agendar una breve llamada de 10 min para resolver dudas finales? 😊",
    },
]

_SLOT_INDEX: Dict[str, int] = {s["key"]: i for i, s in enumerate(SLOT_FLOW)}
_PREEXISTING_CONDITIONALS: frozenset = frozenset({"preexisting_details"})

def slot_applicable(slot_config: Any, slots: dict, source: str = "instagram_dm") -> bool:
    """True si el slot debe preguntarse ahora."""
    if isinstance(slot_config, str):
        key = slot_config
        # Buscar config completa si solo tenemos la key
        config = next((s for s in SLOT_FLOW if s["key"] == key), None)
    else:
        config = slot_config
        key = config["key"]

    if slots.get(key) is not None:
        return False
    
    # Restricción: insurance_provider solo en WhatsApp
    if key == "insurance_provider" and source not in _WA_SOURCES:
        return False

    if key in _PREEXISTING_CONDITIONALS:
        return slots.get("has_preexisting") is True
    
    # Restricciones por producto
    if config and "product" in config:
        curr_prod = slots.get("product_interest")
        return curr_prod == config["product"]
            
    # Citas solo para leads calientes (detectado en el flujo)
    if key == "appointment_requested":
        return slots.get("appointment_requested") is not None
        
    return True

def next_required_slot(slots: dict, source: str = "instagram_dm") -> Optional[str]:
    for slot_def in SLOT_FLOW:
        key = slot_def["key"]
        if not slot_applicable(slot_def, slots, source):
            continue
        if slots.get(key) is not None:
            continue
        if slot_def.get("required") or (slot_def.get("conditional") and slot_applicable(slot_def, slots, source)):
            return key
    return None

def next_any_slot(slots: dict, source: str = "instagram_dm") -> Optional[str]:
    for slot_def in SLOT_FLOW:
        key = slot_def["key"]
        if slot_applicable(slot_def, slots, source) and slots.get(key) is None:
            return key
    return None

def can_send_wa(slots: dict, source: str = "instagram_dm") -> bool:
    return all(
        slots.get(s["key"]) is not None
        for s in SLOT_FLOW
        if s.get("wa_required") and slot_applicable(s, slots, source)
    )

def flow_is_complete(slots: dict, source: str = "instagram_dm") -> bool:
    for slot_def in SLOT_FLOW:
        key = slot_def["key"]
        if not slot_applicable(slot_def, slots, source):
            continue
        if slot_def.get("required") and slots.get(key) is None:
            return False
    for slot_def in SLOT_FLOW:
        key = slot_def["key"]
        if slot_def.get("conditional") and slot_applicable(slot_def, slots, source) and slots.get(key) is None:
            return False
    return True

# ══════════════════════════════════════════════════════
# INTENT DETECTOR (insurance)
# ══════════════════════════════════════════════════════

_INSURANCE_ANCHOR_RE = re.compile(
    r"\b(seguros?|polizas?|aseguradora|cotiza(?:cion|r)|prima|"
    r"coberturas?|copago|cuadro medico|reembolso|hospitalizacion|"
    r"seguro de vida|seguro de salud|seguro medico|seguro hogar|"
    r"contratar un seguro|quiero un seguro|necesito un seguro|"
    r"busco un seguro|me interesa un seguro)\b",
    re.I,
)

_PRODUCT_WORD_RE   = re.compile(r"\b(salud|vida|hogar|mascota|viaje|negocio|empresa|dental|accidente|decesos|defensa|legal)\b", re.I)
_INSURANCE_WEAK_RE = re.compile(r"\b(seguro|poliza|cobertura|contratar|asegurar|cotiza|presupuesto|prima)\b", re.I)
_OUT_OF_SCOPE_RE   = re.compile(r"\b(auto|coche|moto|vehiculo|carro|automovil)\b", re.I)

def is_out_of_scope_product(text: str) -> bool:
    return bool(_OUT_OF_SCOPE_RE.search(nt(text)))

def is_insurance_intent(text: str, state: Optional[dict] = None) -> bool:
    t = nt(text)
    # Descartar productos fuera de alcance ANTES de evaluar intención
    if is_out_of_scope_product(t):
        return False
    if _INSURANCE_ANCHOR_RE.search(t): return True
    if _PRODUCT_WORD_RE.search(t) and _INSURANCE_WEAK_RE.search(t): return True
    if (
        state and state.get("mode") == "insurance"
        and state.get("step") == "product_interest"
        and re.fullmatch(r"\s*[123]\s*", t)
    ):
        return True
    return False

# ══════════════════════════════════════════════════════
# RAG: detect product question + answer
# ══════════════════════════════════════════════════════

_QWORD_RE = re.compile(r"^(que|qué|como|cómo|cual|cuál|cuanto|cuánto|incluye|cubre|hay|puedo|tengo|es)\b", re.I)
_PRODUCT_FAQ_KW = re.compile(
    r"\b(cobertura|cubre|incluye|carencia|copago|reembolso|cuadro medico|"
    r"hospitalizacion|urgencias|pruebas|especialista|dentista|"
    r"preexistencia|limite|tope|exclusion|periodo|precio|cuota)\b",
    re.I
)

def _looks_like_short_place_answer(text: str) -> bool:
    s = text.strip()
    if not (3 <= len(s) <= 45): return False
    if "?" in s: return False
    if re.search(r"\d", s): return False
    return len(s.split()) <= 4

def is_product_question(text: str, state: dict) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if state.get("step") == "province" and _looks_like_short_place_answer(t):
        return False
    if "?" in t:
        return True
    ntv = nt(t)
    if _QWORD_RE.search(ntv) and _PRODUCT_FAQ_KW.search(ntv):
        return True
    if _PRODUCT_FAQ_KW.search(ntv) and len(ntv.split()) >= 4:
        return True
    return False

def _embed_query_vec(question: str) -> Optional[str]:
    if embedder is None:
        return None
    vec = embedder.encode(question, normalize_embeddings=True)
    vec = vec.astype(float).tolist()
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"

def answer_with_rag(question: str, category: str = "salud", route: Optional[str] = None) -> Optional[str]:
    q_vec = _embed_query_vec(question)
    if not q_vec:
        return None

    where = "category = %s AND embedding IS NOT NULL"
    params: List[Any] = [category]

    if route:
        where += " AND route = %s"
        params.append(route)

    sql = (
        f"SELECT chunk_text, source_file, chunk_id, 1-(embedding <=> %s::vector) AS score "
        f"FROM kb_documents WHERE {where} "
        f"ORDER BY embedding <=> %s::vector LIMIT %s"
    )

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [q_vec] + params + [q_vec, KB_TOP_K])
            rows = cur.fetchall()

    if not rows:
        return None

    hits = [(r[0], r[1], r[2], float(r[3] or 0.0)) for r in rows]
    best_score = hits[0][3]
    if best_score < KB_SCORE_THRESHOLD:
        return None

    logger.info("RAG_HIT category=%s best_score=%.3f source=%s chunk=%s", category, best_score, hits[0][1], hits[0][2])

    context = "\n\n".join(h[0] for h in hits[:3])
    context = sanitize_brands(context)

    if _ai_client is None:
        trimmed = context.strip()
        trimmed = re.sub(r"\n{3,}", "\n\n", trimmed)
        return (trimmed[:800] + ("…" if len(trimmed) > 800 else ""))

    # Detectar idioma de la pregunta
    lang = detect_language(question)
    if lang == "en":
        system = get_system_prompt_en(extra_context=build_playbook_prompt(category))
        user = (
            f"Client question: {question}\n\n"
            f"Policy context (extracts):\n{context}\n\n"
            "Answer in English, max 6-8 lines. Use 3-4 bullets if appropriate."
        )
    else:
        system = get_system_prompt(extra_context=build_playbook_prompt(category))
        user = (
            f"Pregunta del cliente: {question}\n\n"
            f"Contexto de póliza (extractos):\n{context}\n\n"
            "Responde en español, máximo 6-8 líneas. Si procede, usa 3-4 bullets."
        )
    try:
        resp = _ai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=220,
            temperature=0.2,
            timeout=10,
        )
        out = resp.choices[0].message.content.strip()
        out = sanitize_brands(out)
        return out if out else None
    except Exception as e:
        logger.warning("RAG_LLM_ERROR err=%s", e)
        return None

# ══════════════════════════════════════════════════════
# SLOT EXTRACTORS
# ══════════════════════════════════════════════════════

_CP_MAP: dict = {
    1: "Álava", 2: "Albacete", 3: "Alicante", 4: "Almería", 5: "Ávila",
    6: "Badajoz", 7: "Baleares", 8: "Barcelona", 9: "Burgos", 10: "Cáceres",
    11: "Cádiz", 12: "Castellón", 13: "Ciudad Real", 14: "Córdoba", 15: "A Coruña",
    16: "Cuenca", 17: "Girona", 18: "Granada", 19: "Guadalajara", 20: "Gipuzkoa",
    21: "Huelva", 22: "Huesca", 23: "Jaén", 24: "León", 25: "Lleida",
    26: "La Rioja", 27: "Lugo", 28: "Madrid", 29: "Málaga", 30: "Murcia",
    31: "Navarra", 32: "Ourense", 33: "Asturias", 34: "Palencia", 35: "Las Palmas",
    36: "Pontevedra", 37: "Salamanca", 38: "Santa Cruz de Tenerife", 39: "Cantabria",
    40: "Segovia", 41: "Sevilla", 42: "Soria", 43: "Tarragona", 44: "Teruel",
    45: "Toledo", 46: "Valencia", 47: "Valladolid", 48: "Bizkaia",
    49: "Zamora", 50: "Zaragoza", 51: "Ceuta", 52: "Melilla",
}
_ALL_KNOWN_PROVINCES_NT: frozenset = frozenset(nt(v) for v in _CP_MAP.values())

def _is_spanish_cp(text: str) -> Optional[str]:
    m = re.search(r"\b(\d{5})\b", text or "")
    if not m: return None
    cp = m.group(1)
    p = int(cp[:2])
    if 1 <= p <= 52:
        return cp
    return None

def _cp_to_province(cp: str) -> str:
    return _CP_MAP.get(int(cp[:2]), "España")

def _llm_resolve_province(place: str) -> Optional[str]:
    place_norm = nt(place)
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT province FROM place_cache WHERE place_text=%s", (place_norm,))
            row = cur.fetchone()
    if row:
        return row[0]

    if _ai_client is None:
        return None

    prompt = (
        "Dado un lugar de España (ciudad/pueblo), responde SOLO JSON: "
        '{"province": "<provincia>"} o {"province": null}.\n'
        f"Lugar: {place[:80]}"
    )
    try:
        resp = _ai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=40,
            temperature=0,
            timeout=6,
        )
        raw = resp.choices[0].message.content.strip()
        parsed = json.loads(raw)
        province = parsed.get("province")
        if not province:
            return None
        if nt(province) not in _ALL_KNOWN_PROVINCES_NT:
            return None
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO place_cache(place_text, province, source) VALUES(%s,%s,%s) "
                    "ON CONFLICT (place_text) DO UPDATE SET province=EXCLUDED.province",
                    (place_norm, province, "llm"),
                )
            conn.commit()
        return province
    except Exception as e:
        logger.warning("LLM_PROVINCE_ERROR err=%s", e)
        return None

def _extract_province(text: str, state: Optional[dict] = None) -> Optional[str]:
    cp = _is_spanish_cp(text or "")
    if cp:
        return _cp_to_province(cp)

    if state and state.get("step") == "province":
        raw = re.sub(r"[^\w\s\-]", " ", (text or "")).strip()
        raw = re.sub(r"\s+", " ", raw)
        if 3 <= len(raw) <= 45 and not re.search(r"\d", raw):
            prov = _llm_resolve_province(raw)
            if prov:
                return prov
            return raw.title()

    return None

def _extract_num_people(text: str) -> Optional[int]:
    t = nt(text)
    m = re.search(r"\b(\d{1,2})\b", t)
    if m:
        v = int(m.group(1))
        if 1 <= v <= 10:
            return v
    if re.search(r"\b(solo yo|yo solo|para mi|nosotros uno|uno solo)\b", t): return 1
    if re.search(r"\b(mi pareja y yo|somos dos|dos personas|nosotros dos|mi mujer y yo|mi marido y yo)\b", t): return 2
    if re.search(r"\b(somos tres|tres personas|nosotros tres|mi pareja y mis hijos)\b", t): return 3
    if re.search(r"\b(somos cuatro|cuatro personas|nosotros cuatro)\b", t): return 4
    return None

_AGE_SIGNAL_RE = re.compile(r"\b(años|anos|edad|edades|tengo|tiene|mi hijo|mi hija|cumple)\b", re.I)

def _extract_ages(text: str, state: Optional[dict] = None) -> Optional[List[int]]:
    in_step = bool(state and state.get("step") == "ages")
    has_signal = bool(_AGE_SIGNAL_RE.search(text or ""))
    if not in_step and not has_signal:
        return None

    nums = re.findall(r"\b(\d{1,3})\b", text or "")
    ages = [int(x) for x in nums if 0 <= int(x) < 110]
    if not ages:
        return None

    if (not in_step) and len(ages) == 1 and ages[0] <= 10:
        nppl = _extract_num_people(text)
        if nppl is not None and nppl == ages[0]:
            return None
    return ages

def auto_detect_product(text: str) -> Optional[str]:
    """Detecta producto implícito aunque el cliente no lo nombre directamente."""
    t = nt(text)
    mapeos = [
        (r"\b(hijos|familia|niños|mis hijos|para todos|para la familia)\b", "salud"),
        (r"\b(hipoteca|si me pasa algo|dejar cubierta|fallecimiento|herencia)\b", "vida"),
        (r"\b(perro|gato|mascota|animal|gatito|perrito)\b", "mascotas"),
        (r"\b(viaje|vacaciones|vuelo|extranjero|viajar)\b", "viaje"),
        (r"\b(negocio|empresa|autónomo|autonomo|local|taller|oficina|pyme)\b", "negocios"),
        (r"\b(dientes|dentista|ortodoncia|boca|muela|muelas|blanqueamiento)\b", "dental"),
        (r"\b(nie|tie|visado|residencia|extranjero|tarjeta roja|arraigo)\b", "salud_extranjeros"),
    ]
    for pattern, producto in mapeos:
        if re.search(pattern, t):
            return producto
    return None


def detect_language(text: str) -> str:
    """Detecta si el texto está en inglés o español."""
    t = nt(text)
    english_signals = ["i", "need", "want", "health", "insurance", "how", "much", 
                       "cost", "visa", "nie", "residence", "spain", "coverage", 
                       "policy", "quote", "price"]
    count = sum(1 for word in english_signals if word in t)
    return "en" if count >= 2 else "es"


def propose_call_slots() -> list[str]:
    """Devuelve los próximos 3 huecos disponibles en horario laboral."""
    if not _HAS_GCAL:
        return []
    now = datetime.now()
    slots = []
    candidate = now + timedelta(hours=2)
    candidate = candidate.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    
    while len(slots) < 3:
        weekday = candidate.weekday()
        hour = candidate.hour
        if weekday < 5:
            if 9 <= hour < 14 or 16 <= hour < 19:
                dia = "Hoy" if candidate.date() == now.date() else "Mañana" if candidate.date() == (now + timedelta(days=1)).date() else candidate.strftime("%A")
                slots.append(f"{dia} a las {candidate.strftime('%H:%M')}")
        candidate += timedelta(hours=1)
    
    return slots[:3]


def create_calendar_event(slot: datetime, lead_name: str, lead_phone: str) -> bool:
    """Crea un evento en Google Calendar."""
    if not _HAS_GCAL:
        return False
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        
        import json
        creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=["https://www.googleapis.com/auth/calendar"]
        )
        service = build("calendar", "v3", credentials=creds)
        
        event = {
            "summary": f"Llamada {lead_name or 'Lead'} — Valentín Protección Integral",
            "description": f"Lead: {lead_phone}\nNombre: {lead_name}",
            "start": {"dateTime": slot.isoformat(), "timeZone": "Europe/Madrid"},
            "end": {"dateTime": (slot + timedelta(minutes=15)).isoformat(), "timeZone": "Europe/Madrid"},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]},
        }
        service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
        logger.info("GCAL_EVENT_CREATED lead=%s slot=%s", lead_phone, slot.isoformat())
        return True
    except Exception as e:
        logger.error("GCAL_ERROR lead=%s err=%s", lead_phone, e)
        return False


def _extract_product(text: str, state: Optional[dict] = None) -> Optional[str]:
    t = nt(text).strip()
    in_product_step = bool(state and state.get("mode") == "insurance" and state.get("step") == "product_interest")
    if in_product_step:
        if t in {"1", "primero", "el primero", "la primera"}: return "salud"
        if t in {"2", "segundo", "el segundo", "la segunda"}: return "vida"
        if t in {"3", "tercero", "el tercero", "la tercera"}: return "hogar"
        if t in {"4", "cuarto", "el cuarto", "la cuarta"}: return "mascotas"
        if t in {"5", "quinto", "el quinto", "la quinta"}: return "viaje"
        if t in {"6", "sexto", "el sexto", "la sexta"}: return "negocios"
        
    if re.search(r"\b(salud|medico|médico|seguro medico)\b", t): return "salud"
    if re.search(r"\b(vida)\b", t): return "vida"
    if re.search(r"\b(hogar)\b", t): return "hogar"
    if re.search(r"\b(mascota|perro|gato|canino|felino)\b", t): return "mascotas"
    if re.search(r"\b(viaje|turismo|vacaciones)\b", t): return "viaje"
    if re.search(r"\b(negocio|empresa|pyme|autonomo|autónomo|sociedad)\b", t): return "negocios"
    if re.search(r"\b(dental|boca|dientes)\b", t): return "dental"
    if re.search(r"\b(accidente|accidentes)\b", t): return "accidentes"
    if re.search(r"\b(decesos|fallecimiento|entierro)\b", t): return "decesos"
    if re.search(r"\b(legal|juridica|jurídica|abogado)\b", t): return "defensa_legal"
    return None

def _extract_coverage(text: str, state: Optional[dict] = None) -> Optional[str]:
    t = (text or "").strip()
    if len(t) < 3:
        return None
    tn = nt(t)
    # Rechazar afirmaciones cortas que no son coberturas
    _REJECT_COVERAGE = {"si", "sip", "ok", "aja", "va", "sep", "yes", "no", "nop", "nah"}
    if tn in _REJECT_COVERAGE:
        return None
    # Aceptar 'estándar' / 'normal' como cobertura por defecto
    if tn in {"estandar", "normal", "basico", "basica", "lo basico", "lo normal", "lo estandar"}:
        return "estándar"
    return t

def _extract_copay_preference(text: str) -> Optional[str]:
    t = nt(text)
    if "sin copago" in t or "cuota fija" in t: return "sin_copago"
    if "con copago" in t or ("copago" in t and "sin" not in t): return "con_copago"
    if re.search(r"\b(indiferente|me da igual|da igual)\b", t): return "indiferente"
    if t.strip() == "1": return "sin_copago"
    if t.strip() == "2": return "con_copago"
    if t.strip() == "3": return "indiferente"
    return None

def _extract_yes_no(text: str) -> Optional[bool]:
    t = nt(text)
    if re.search(r"\b(si|sí|claro|vale|ok)\b", t): return True
    if re.search(r"\b(no|nop|para nada)\b", t): return False
    return None

def _extract_has_preexisting(text: str) -> Optional[bool]:
    return _extract_yes_no(text)

def _extract_preexisting_details(text: str, state: Optional[dict] = None) -> Optional[str]:
    in_step = bool(state and state.get("step") == "preexisting_details")
    t = (text or "").strip()
    if len(t) < 3: return None
    if in_step: return t
    if re.search(r"\b(asma|diabetes|tiroides|hipertension|hipertensión|alerg)\b", nt(t)):
        return t
    return None

_BUDGET_SIG = re.compile(r"(€|eur|presupuesto|cuota|mensual|precio|importe|al mes|por mes)", re.I)

def _extract_budget(text: str, state: Optional[dict] = None) -> Optional[str]:
    in_step = bool(state and state.get("step") == "budget")
    if not in_step and not _BUDGET_SIG.search(text or ""):
        return None
    t = nt(text)
    if re.search(r"\b(no lo se|no sé|depende)\b", t): return "no_definido"
    m = re.search(r"\b(\d{2,4})\b", t)
    if m: return f"{m.group(1)}€/mes"
    return (text or "").strip() if in_step else None

def _extract_name(text: str, state: Optional[dict] = None) -> Optional[str]:
    t = (text or "").strip()
    # Detección con patrón explícito
    m = re.search(r"(?:soy|me llamo|mi nombre es)\s+([A-ZÁÉÍÓÚÑa-záéíóúñ]{2,20})", t, re.I)
    if m:
        return m.group(1).strip().title()
    # Si estamos en el step "name", aceptar una palabra capitalizada como nombre
    if state and state.get("step") == "name":
        words = t.split()
        if 1 <= len(words) <= 3 and all(len(w) >= 2 for w in words):
            return " ".join(w.title() for w in words)
    return None

def _extract_phone_or_email(text: str) -> Optional[str]:
    em = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text or "")
    if em: return em.group(0)
    ph = re.search(r"\b([679]\d{8}|\+34\s?[679]\d{8}|0034\s?[679]\d{8})\b", (text or "").replace(" ", ""))
    if ph: return ph.group(0)
    return None

def extract_slots_from_text(text: str, current_state: dict) -> dict:
    extracted: dict = {}
    slots = dict(current_state.get("slots", {}))
    extractors: dict[str, Any] = {
        "product_interest":     lambda t: _extract_product(t, current_state),
        "province":             lambda t: _extract_province(t, current_state),
        "num_people":           _extract_num_people,
        "ages":                 lambda t: _extract_ages(t, current_state),
        "coverage_preferences": lambda t: _extract_coverage(t, current_state),
        "copay_preference":     _extract_copay_preference,
        "has_preexisting":      _extract_has_preexisting,
        "preexisting_details":  lambda t: _extract_preexisting_details(t, current_state),
        "budget":               lambda t: _extract_budget(t, current_state),
        "name":                 lambda t: _extract_name(t, current_state),
        "phone_or_email":       _extract_phone_or_email,
        # Mascotas
        "pet_type":             lambda t: "perro" if "perro" in nt(t) or "🐶" in t else ("gato" if "gato" in nt(t) or "🐱" in t else None),
        "pet_name":             lambda t: t.strip().title() if current_state.get("step") == "pet_name" and len(t.strip()) > 1 else None,
        "pet_breed":            lambda t: re.search(r"\braza\s+de\s+(.+)", t, re.I).group(1) if re.search(r"\braza\s+de\s+(.+)", t, re.I) else None,
        "pet_age":              lambda t: re.search(r"(\d+)\s*(años|meses)", t, re.I).group(0) if re.search(r"(\d+)\s*(años|meses)", t, re.I) else None,
        "insurance_provider":   lambda t: "adeslas" if re.search(r"\badeslas\b", t, re.I) else ("asisa" if re.search(r"\basisa\b", t, re.I) else None),
        # Viaje
        "travel_destination":   lambda t: t.strip() if current_state.get("step") == "travel_destination" and len(t.strip()) > 3 else None,
        "travel_dates":         lambda t: t.strip() if current_state.get("step") == "travel_dates" and len(t.strip()) > 3 else None,
        "num_travelers":        lambda t: (re.search(r"(\d+)", t).group(1) if re.search(r"(\d+)", t) else None),
        # Negocios
        "business_type":        lambda t: t.strip() if current_state.get("step") == "business_type" and len(t.strip()) > 3 else None,
        "appointment_requested": lambda t: _extract_yes_no(t),
    }
    for slot_key, extractor in extractors.items():
        if slots.get(slot_key) is not None:
            continue
        if not slot_applicable(slot_key, slots):
            continue
        val = extractor(text)
        if val is not None:
            extracted[slot_key] = val
            slots = {**slots, **extracted}
    return extracted

def is_valid_answer_for_step(step: str, text: str, state: dict) -> bool:
    if step == "product_interest":      return _extract_product(text, state) is not None
    if step == "province":              return _extract_province(text, state) is not None
    if step == "num_people":            return _extract_num_people(text) is not None
    if step == "ages":                  return _extract_ages(text, state) is not None
    if step == "coverage_preferences":  return _extract_coverage(text, state) is not None
    if step == "copay_preference":      return _extract_copay_preference(text) is not None
    if step == "has_preexisting":       return _extract_has_preexisting(text) is not None
    if step == "preexisting_details":   return _extract_preexisting_details(text, state) is not None
    if step == "budget":                return _extract_budget(text, state) is not None
    if step == "name":                  return _extract_name(text, state) is not None
    
    # New slots validation
    extracted = extract_slots_from_text(text, state)
    if step in extracted: return True
    
    return True

# ══════════════════════════════════════════════════════
# LEAD SCORING (UPGRADE)
# ══════════════════════════════════════════════════════

_URGENCY_RE = re.compile(
    r"\b(urgente|lo necesito ya|rapido|pronto|cuanto antes|inmediato|"
    r"me operan|me caso|nos mudamos|se me acaba|vence|caduca)\b", re.I
)

def lead_score(slots: dict, last_user_text: str = "", purchase_intent: bool = False) -> int:
    score = 0
    # datos duros
    if slots.get("province"): score += 1
    if slots.get("num_people"): score += 1
    if slots.get("ages"): score += 2
    if slots.get("copay_preference"): score += 1
    if slots.get("has_preexisting") is not None: score += 1
    if slots.get("budget"): score += 1
    if slots.get("name"): score += 1

    # señales de intención
    t = nt(last_user_text or "")
    if re.search(r"\b(precio|cuota|cuanto|cuánto|presupuesto)\b", t): score += 1
    if purchase_intent: score += 3
    if re.search(r"\b(whatsapp|wasap|wsp|enlace)\b", t): score += 2
    # Urgencia
    if _URGENCY_RE.search(last_user_text or ""): score += 3
    return score

# ══════════════════════════════════════════════════════
# WA: link prefilled SIN mostrar el texto al usuario
# ══════════════════════════════════════════════════════

def _build_wa_prefill(slots: dict) -> str:
    def v(k): return str(slots[k]) if slots.get(k) is not None else "?"
    copay_label = {
        "sin_copago":  "sin copago",
        "con_copago":  "con copago",
        "indiferente": "indiferente",
    }.get(slots.get("copay_preference") or "", str(slots.get("copay_preference") or "?"))

    preex = "?"
    if slots.get("has_preexisting") is True:
        preex = "Sí"
        if slots.get("preexisting_details"):
            preex += f" — {slots['preexisting_details']}"
    elif slots.get("has_preexisting") is False:
        preex = "No"

    lines = [
        "Hola, vengo desde Instagram 👋",
        f"Interés: {v('product_interest')}",
        f"Provincia: {v('province')}",
        f"Personas: {v('num_people')}",
        f"Edades: {v('ages')}",
        f"Coberturas: {v('coverage_preferences')}",
        f"Copago: {copay_label}",
        f"Preexistencias: {preex}",
        f"Presupuesto: {v('budget')}",
    ]
    if slots.get("name"): lines.append(f"Nombre: {slots['name']}")
    if slots.get("phone_or_email"): lines.append(f"Contacto: {slots['phone_or_email']}")
    lines += ["", "¿Me enviáis la propuesta? Gracias 🙂"]
    return "\n".join(lines)

def wa_cooldown_active(state: dict) -> bool:
    import datetime
    wa_sent_at = state.get("wa_sent_at")
    if wa_sent_at is None: return False
    if isinstance(wa_sent_at, datetime.datetime):
        now = datetime.datetime.now(tz=wa_sent_at.tzinfo)
        return (now - wa_sent_at).total_seconds() / 3600 < WA_COOLDOWN_HOURS
    return False

def _wa_message(lead_id: str, slots: dict, channel: str, state: dict, reason: str = "wa_link_sent") -> str:
    summary = _build_wa_prefill(slots)
    base_url = f"https://wa.me/{DEFAULT_WA_PHONE_E164}"
    wa_link = f"{base_url}?text={quote(summary, safe='')}"

    msg = (
        "Perfecto ✅ Te paso el enlace de WhatsApp.\n"
        "Al abrirlo, el mensaje ya va preparado. Solo tienes que enviarlo 🙂\n\n"
        f"{wa_link}"
    )

    state["wa_sent_at"] = datetime.now()
    log_event(lead_id, channel, "out", msg, intent=reason)
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE conversation_state SET wa_sent_at=now() WHERE lead_id=%s", (lead_id,))
        conn.commit()
    return msg

_WA_EXPLICIT_RE = re.compile(r"\b(whatsapp|wasap|wsp|wa\b|link de whatsapp|enlace de whatsapp|manda el link)\b", re.I)
_PURCHASE_INTENT_RE = re.compile(r"\b(contratar|lo quiero|enviame la propuesta|envíame la propuesta|manda propuesta|como lo contrato)\b", re.I)
_RESET_RE = re.compile(r"\b(empezar de nuevo|reiniciar|reset|de cero)\b", re.I)

def is_explicit_wa_request(text: str) -> bool:
    return bool(_WA_EXPLICIT_RE.search(nt(text)))

def is_purchase_intent(text: str) -> bool:
    return bool(_PURCHASE_INTENT_RE.search(nt(text)))

def is_reset_request(text: str) -> bool:
    return bool(_RESET_RE.search(nt(text)))

def is_greeting_only(text: str) -> bool:
    t = nt(text)
    if not t: return True
    # Rosa responde de forma más humana
    GREET = {"hola","buenas","buenas tardes","buenas noches","buenos dias","hey","holi","hello","ey","hi","saludos",
             "hola rosa","buenas rosa","hey rosa","hi rosa","holi rosa"}
    if t in GREET: return True
    words = t.split()
    return len(words) <= 4 and any(w in GREET for w in words) and not re.search(r"\b(seguro|salud|vida|hogar|precio|poliza|contratar|presupuesto)\b", t)

def _question_for_slot(slot_key: str, product: Optional[str] = None, ab_version: str = "A") -> str:
    """Devuelve la pregunta para un slot, adaptada al producto y versión A/B."""
    # Variación A/B simple para el primer contacto
    if slot_key == "product_interest" and ab_version == "B":
        return (
            "¡Hola! Soy Rosa y me encantaría ayudarte a proteger lo que más quieres ❤️ "
            "¿Qué seguro te interesa mirar hoy?\n\n"
            "1) Salud 🩺\n"
            "2) Vida ❤️\n"
            "3) Hogar 🏠"
        )

    # Preguntas específicas por producto
    if product == "vida" and slot_key in _VIDA_QUESTIONS:
        return _VIDA_QUESTIONS[slot_key]
    if product == "hogar" and slot_key in _HOGAR_QUESTIONS:
        return _HOGAR_QUESTIONS[slot_key]
    if product == "mascotas" and slot_key in _MASCOTAS_QUESTIONS:
        return _MASCOTAS_QUESTIONS[slot_key]
    if product == "viaje" and slot_key in _VIAJE_QUESTIONS:
        return _VIAJE_QUESTIONS[slot_key]
    if product == "negocios" and slot_key in _NEGOCIOS_QUESTIONS:
        return _NEGOCIOS_QUESTIONS[slot_key]
        
    # Pregunta genérica
    slot_def = next((s for s in SLOT_FLOW if s["key"] == slot_key), None)
    if not slot_def:
        return f"¿Me confirmas el dato de {slot_key}?"
    return slot_def["question"]

# ══════════════════════════════════════════════════════
# META SEND
# ══════════════════════════════════════════════════════

_META_PERM_SUBCODES: frozenset = frozenset({2534048, 200})
_META_PERM_MSGS: tuple = (
    "does not have access to advanced access",
    "instagram_manage_messages",
    "recipient is not in the allowed",
    "no tiene acceso avanzado",
    "user is not an admin",
)

def _is_meta_permission_error(error_body: str) -> bool:
    try:
        parsed = json.loads(error_body)
        err = parsed.get("error", {})
        subcode = err.get("error_subcode", 0)
        msg = (err.get("message", "") + err.get("error_user_msg", "")).lower()
        if subcode in _META_PERM_SUBCODES: return True
        if any(s in msg for s in _META_PERM_MSGS): return True
    except Exception:
        pass
    return any(s in error_body.lower() for s in _META_PERM_MSGS)

def _meta_sender_is_blocked(sender_id: str) -> bool:
    ts = _META_BLOCKED.get(sender_id)
    if ts is None: return False
    if time.time() - ts > META_BLOCKED_TTL_S:
        del _META_BLOCKED[sender_id]
        return False
    return True

def _meta_block_sender(sender_id: str) -> None:
    _META_BLOCKED[sender_id] = time.time()
    logger.warning("META_SENDER_BLOCKED sender_id=%s ttl=%ss", sender_id, META_BLOCKED_TTL_S)

def _meta_send_ig_dm(recipient_id: str, message: str) -> bool:
    if _meta_sender_is_blocked(recipient_id):
        return False
    token = META_ACCESS_TOKEN or META_PAGE_ACCESS_TOKEN
    if not token or not recipient_id:
        return False
    endpoint = META_IG_SENDER_ENDPOINT or "https://graph.facebook.com/v19.0/me/messages"
    url = f"{endpoint}?access_token={token}"
    data = json.dumps({"recipient": {"id": recipient_id}, "message": {"text": message[:1800]}}).encode("utf-8")
    req = URequest(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=10):
            logger.info("META_SEND_OK to=%s", recipient_id)
            return True
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if e.code == 403 and _is_meta_permission_error(body):
            _meta_block_sender(recipient_id)
            logger.warning("META_SEND_403_PERM sender_id=%s", recipient_id)
            return False
        logger.error("META_SEND_HTTP_ERR %s: %s", e.code, body[:200])
        return False
    except Exception as e:
        logger.error("META_SEND_ERR %s", e)
        return False

def _meta_send_wa(recipient_id: str, message: str) -> bool:
    """Envía un mensaje por WhatsApp Cloud API."""
    if _meta_sender_is_blocked(recipient_id):
        return False
    token = META_WA_TOKEN or META_ACCESS_TOKEN
    phone_id = WA_PHONE_NUMBER_ID
    if not token or not recipient_id or not phone_id:
        logger.warning("WA_SEND_MISSING_CONFIG phone_id=%s token=%s", bool(phone_id), bool(token))
        return False
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages?access_token={token}"
    data = json.dumps({
        "messaging_product": "whatsapp",
        "to": recipient_id,
        "type": "text",
        "text": {"body": message[:1800]}
    }).encode("utf-8")
    req = URequest(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=10):
            logger.info("WA_SEND_OK to=%s", recipient_id)
            return True
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if e.code == 403 and _is_meta_permission_error(body):
            _meta_block_sender(recipient_id)
            logger.warning("WA_SEND_403_PERM sender_id=%s", recipient_id)
            return False
        logger.error("WA_SEND_HTTP_ERR %s: %s", e.code, body[:200])
        return False
    except Exception as e:
        logger.error("WA_SEND_ERR %s", e)
        return False


def send_with_lag_sync(recipient_id: str, message: str) -> None:
    """Envía con retardo artificial usando distribución triangular (pico en 7s) y extra para mensajes largos."""
    lag = random.triangular(LAG_MIN_S, LAG_MAX_S, 7.0)
    if len(message) > 200:
        lag += 2.0
    time.sleep(lag)
    _meta_send_ig_dm(recipient_id, message)


def _send_wa_with_lag(recipient_id: str, message: str) -> None:
    """Envía WhatsApp con el mismo retardo artificial que send_with_lag_sync."""
    lag = random.triangular(LAG_MIN_S, LAG_MAX_S, 7.0)
    if len(message) > 200:
        lag += 2.0
    time.sleep(lag)
    _meta_send_wa(recipient_id, message)


def _bot_is_silenced(lead_id: str) -> bool:
    """
    Restriction 1: El bot NO debe responder si:
    - derivado_a_humano = True
    - human_released = False
    - última actividad < 48h (sigue activo)
    """
    try:
        ls = load_lead_state(lead_id)
        if not ls.get("derivado_a_humano"):
            return False
        if ls.get("human_released"):
            return False
        ultimo = ls.get("ultimo_mensaje")
        if not ultimo:
            return False
        now = datetime.now()
        if hasattr(ultimo, 'tzinfo') and ultimo.tzinfo:
            now = datetime.now(tz=ultimo.tzinfo)
        horas = (now - ultimo).total_seconds() / 3600
        if horas >= 48:
            return False  # Inactividad > 48h → bot puede responder
        return True
    except Exception as e:
        logger.error("BOT_IS_SILENCED_ERR lead=%s err=%s", lead_id, e)
        return False


def verify_meta_signature(raw_body: bytes, sig256: str, sig1: str) -> bool:
    if not META_APP_SECRET or META_SIGNATURE_MODE in ("", "dev"):
        return True
    secret = META_APP_SECRET.encode("utf-8")
    if sig256 and "sha256=" in sig256:
        recv = sig256.split("sha256=", 1)[-1].strip()
        return hmac.compare_digest(hmac.new(secret, msg=raw_body, digestmod=hashlib.sha256).hexdigest(), recv)
    if sig1 and "sha1=" in sig1:
        recv = sig1.split("sha1=", 1)[-1].strip()
        return hmac.compare_digest(hmac.new(secret, msg=raw_body, digestmod=hashlib.sha1).hexdigest(), recv)
    return False

def extract_text_from_wa_image(media_id: str) -> Optional[str]:
    """Descarga una imagen de WhatsApp y extrae texto con Tesseract."""
    if not _HAS_TESSERACT or not _HAS_PIL:
        logger.warning("TESSERACT_NOT_AVAILABLE _HAS_TESSERACT=%s _HAS_PIL=%s", _HAS_TESSERACT, _HAS_PIL)
        return None
    try:
        token = META_WA_TOKEN or META_ACCESS_TOKEN
        if not token:
            logger.warning("WA_IMAGE_NO_TOKEN")
            return None
        url = f"https://graph.facebook.com/v19.0/{media_id}?access_token={token}"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            data = resp.json()
            img_url = data.get("url") or data.get("media", {}).get("url", "")
            if not img_url:
                logger.error("WA_IMAGE_NO_URL media_id=%s", media_id)
                return None
            resp = requests.get(f"{img_url}?access_token={token}", timeout=10)
            if resp.status_code != 200:
                logger.error("WA_IMAGE_DOWNLOAD_FAIL media_id=%s status=%s", media_id, resp.status_code)
                return None
        
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
        img = Image.open(BytesIO(resp.content))
        text = pytesseract.image_to_string(img, lang='spa+eng')
        result = text.strip()
        logger.info("OCR_SUCCESS media_id=%s len=%d", media_id, len(result))
        return result if result else None
    except Exception as e:
        logger.error("OCR_ERROR media_id=%s err=%s", media_id, e)
        return None


def send_followup_if_needed(lead_id: str) -> bool:
    """Envía un follow-up automático si han pasado más de FOLLOWUP_DELAY_HOURS."""
    try:
        ls = load_lead_state(lead_id)
        state = load_state(lead_id)
        
        # Restriction 1: No enviar follow-up si el bot está silenciado
        if _bot_is_silenced(lead_id):
            logger.info("FOLLOWUP_SKIP_SILENCED lead=%s", lead_id)
            return False
        
        if flow_is_complete(state.get("slots", {})):
            return False
        
        followup_attempts = state.get("followup_attempts", 0)
        if followup_attempts >= FOLLOWUP_MAX_ATTEMPTS:
            return False
        
        ultimo = ls.get("ultimo_mensaje")
        if not ultimo:
            return False
        now = datetime.now()
        if hasattr(ultimo, 'tzinfo') and ultimo.tzinfo:
            now = datetime.now(tz=ultimo.tzinfo)
        horas = (now - ultimo).total_seconds() / 3600
        if horas < FOLLOWUP_DELAY_HOURS:
            return False
        
        nombre = state.get("slots", {}).get("name") or ls.get("datos_recogidos", {}).get("nombre")
        if nombre:
            msg = f"Hola {nombre}, solo quería saber si pudimos ayudarte con lo del seguro. Si tienes alguna duda o quieres que sigamos, aquí estamos 😊"
        else:
            msg = "Hola, quedamos a medias antes. Si tienes dudas o quieres que sigamos con tu consulta, aquí estamos cuando quieras 😊"
        
        sender_id = state.get("ig_user_id", "")
        if sender_id:
            _meta_send_wa(sender_id, msg)
        
        state["followup_attempts"] = followup_attempts + 1
        state["wa_sent_at"] = datetime.now()
        save_state(lead_id, state)
        
        ls["notas"].append(f"Follow-up #{followup_attempts + 1} enviado")
        save_lead_state(lead_id, ls)
        
        logger.info("FOLLOWUP_SENT lead=%s attempt=%d/%d", lead_id, followup_attempts + 1, FOLLOWUP_MAX_ATTEMPTS)
        return True
    except Exception as e:
        logger.error("FOLLOWUP_ERROR lead=%s err=%s", lead_id, e)
        return False

def _queue_lead_for_salesforce(lead_id: str, company_tag: str = "default"):
    """Guarda el lead en una tabla de cola para Salesforce. Solución temporal sin AI."""
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                # Asegurar tabla (v0) con soporte multi-company
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS salesforce_sync_queue (
                        id SERIAL PRIMARY KEY,
                        lead_id TEXT UNIQUE,
                        company_tag TEXT DEFAULT 'default',
                        synced BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                # Intentar añadir columna si no existe (migración básica)
                try:
                    cur.execute("ALTER TABLE salesforce_sync_queue ADD COLUMN IF NOT EXISTS company_tag TEXT DEFAULT 'default'")
                except: pass

                cur.execute("""
                    INSERT INTO salesforce_sync_queue (lead_id, company_tag)
                    VALUES (%s, %s)
                    ON CONFLICT (lead_id) DO UPDATE SET company_tag = EXCLUDED.company_tag
                """, (lead_id, company_tag))
            conn.commit()
    except Exception as e:
        logger.warning("SF_QUEUE_ERROR lead=%s: %s", lead_id, e)

def _notify_n8n_followup(lead_id: str, step: str, sender_id: Optional[str], company_tag: str = "default") -> bool:
    """Envía un webhook a n8n para seguimiento, sin bloquear el flujo principal."""
    # Sincronización básica con Salesforce (v0)
    _queue_lead_for_salesforce(lead_id, company_tag)
    
    if not N8N_WEBHOOK_URL:
        return False
    
    def _fire():
        try:
            data = {
                "lead_id": lead_id,
                "sender_id": sender_id,
                "step": step,
                "timestamp": datetime.now().isoformat(),
                "event_type": "high_score_alert",
                "agent": AGENT_NAME,
                "company_tag": company_tag
            }
            requests.post(N8N_WEBHOOK_URL, json=data, timeout=N8N_TIMEOUT)
        except Exception as e:
            logger.warning("N8N_NOTIFY_FAILED lead=%s step=%s error=%s", lead_id, step, e)

    threading.Thread(target=_fire, daemon=True).start()
    return True

# ══════════════════════════════════════════════════════
# DEDUP
# ══════════════════════════════════════════════════════

def _dedup_key(lead_id: str, text: str) -> str:
    h = hashlib.md5((text or "").strip().lower().encode("utf-8")).hexdigest()[:12]
    return f"{lead_id}|{h}"

def _dedup_get(lead_id: str, text: str) -> Optional[str]:
    key = _dedup_key(lead_id, text)
    entry = _DEDUP_CACHE.get(key)
    if entry and (time.time() - entry[0]) < _DEDUP_TTL:
        return entry[1]
    return None

def _dedup_set(lead_id: str, text: str, reply: str) -> None:
    key = _dedup_key(lead_id, text)
    _DEDUP_CACHE[key] = (time.time(), reply)
    if len(_DEDUP_CACHE) > 5000:
        now = time.time()
        for k in [k for k, (ts, _) in _DEDUP_CACHE.items() if now - ts > _DEDUP_TTL * 2]:
            del _DEDUP_CACHE[k]

# ══════════════════════════════════════════════════════
# CORE: process_message con RAG interrupt + AUTO WA
# ══════════════════════════════════════════════════════
# SCORING & SENTIMENT
# ══════════════════════════════════════════════════════

_URGENCY_KEYWORDS = {
    "vence", "urgente", "mañana", "mañana mismo", "caduca", "ayer",
    "hospital", "operación", "proxima semana", "próxima semana",
    "renovación", "renovacion", "cuanto antes"
}

_SENTIMENT_POSITIVE = {"gracias", "genial", "perfecto", "me encanta", "estupendo", "top", "buenismo", "rosa", "vale", "bien"}
_SENTIMENT_NEGATIVE = {"no me gusta", "caro", "pesada", "esperado", "mal", "fatal", "horror", "tarda", "duda"}

def calculate_lead_score(lead_id: str, text: str, state: dict) -> Tuple[int, str, float]:
    """
    Calcula el interés del lead basado en comportamiento y texto.
    Retorna: (score_int, temp_tag, closure_prob)
    """
    score = 0
    t = nt(text)
    
    # 1. Analizar texto (Urgencia)
    if any(k in t for k in _URGENCY_KEYWORDS):
        score += 4
        
    # 2. Analizar sentimiento
    if any(p in t for p in _SENTIMENT_POSITIVE): score += 1
    if any(n in t for n in _SENTIMENT_NEGATIVE): score -= 1
    
    # 3. Profundidad del flujo
    steps_filled = len(state.get("slots", {}))
    score += min(steps_filled, 4) # Máximo 4 puntos por slots llenos
    
    # 4. Abandono potencial (drift)
    if state.get("non_insurance_turns", 0) > 0:
        score -= 2
        
    # Clasificación
    tag = "cold"
    if score >= 4: tag = "warm"
    if score >= 8: tag = "hot"
    
    # Probabilidad base (muy simplificada)
    prob = min(max(0.1, (score / 12.0)), 0.99)
    
    return score, tag, prob

def update_lead_scoring(lead_id: str, score: int, tag: str, prob: float):
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE leads SET lead_score = %s, temp_tag = %s, closure_prob = %s WHERE lead_id = %s",
                (score, tag, prob, lead_id)
            )
        conn.commit()


def score_intent(text: str, slots: dict, turn_count: int) -> str:
    """
    Clasifica la intención del lead como 'frio', 'templado' o 'caliente'.
    """
    t = nt(text)
    
    # CALIENTE
    caliente_palabras = ["contratar", "me interesa", "cuánto vale", "cuanto vale", 
                         "quiero empezar", "cómo lo hago", "como lo hago",
                         "necesito para ya", "urgente", "para esta semana"]
    if any(p in t for p in caliente_palabras):
        return "caliente"
    
    slots_rellenados = sum(1 for v in slots.values() if v is not None)
    if slots_rellenados >= 3:
        return "caliente"
    if turn_count > 4:
        return "caliente"
    
    # TEMPLADO
    if re.search(r"\b(precio|cuanto|cuánto|presupuesto|cuesta|cuestan)\b", t):
        return "templado"
    if 1 <= slots_rellenados <= 2:
        return "templado"
    productos_mencionados = ["salud", "vida", "dental", "mascotas", "hogar", "viaje", "negocios", "accidentes", "decesos"]
    if any(p in t for p in productos_mencionados):
        return "templado"
    
    # FRÍO
    return "frio"


def _is_rate_limited(sender_id: str) -> bool:
    """Anti-spam: 5+ mensajes en <10s → agrupar."""
    now = time.time()
    ts_list = _RATE_LIMIT.get(sender_id, [])
    ts_list = [t for t in ts_list if now - t < RATE_LIMIT_WINDOW_S]
    ts_list.append(now)
    _RATE_LIMIT[sender_id] = ts_list
    return len(ts_list) > RATE_LIMIT_MAX_MSGS

def _detect_returning_user(lead_id: str) -> Optional[str]:
    """Detecta si el usuario ya ha interactuado antes y devuelve un saludo personalizado."""
    try:
        with db_connect() as conn:
            order_col = _conversations_order_by_sql(conn)
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT text FROM conversations WHERE lead_id = %s AND direction = %s "
                    f"ORDER BY {order_col} DESC LIMIT 1",
                    (lead_id, "out"),
                )
                row = cur.fetchone()
                if row:
                    return "returning"
    except Exception:
        pass
    return None

# Storytelling / prueba social
_STORYTELLING_HINTS: List[str] = [
    "El otro día ayudé a una familia de Madrid con algo parecido y quedaron encantados 😊",
    "Justo esta semana tuve un caso similar, así que lo tengo muy fresquito 🙂",
    "He visto mucha gente con la misma duda, así que te lo cuento claro.",
    "Esto es más común de lo que crees, ¡me lo preguntan bastante!",
]

_STORYTELLING_HINTS_B: List[str] = [
    "¡Qué buena pregunta! Me encanta poder aclararte esto 😊",
    "Me apasiona ayudar a gente como tú a estar tranquila, te cuento todo.",
    "Eres de los que se fijan en los detalles, ¡eso es genial! Te explico.",
    "Me hace mucha ilusión guiarte en esto, es más sencillo de lo que parece.",
]

def _random_storytelling(ab_version: str = "A") -> str:
    if ab_version == "B":
        return random.choice(_STORYTELLING_HINTS_B)
    return random.choice(_STORYTELLING_HINTS)

def _is_new_conversation(lead_id: str) -> bool:
    """
    REGLA ABSOLUTA — Prioridad 0.
    True únicamente si:
      A) Cero mensajes outbound previos en conversations para este lead_id
      B) human_released = FALSE en lead_state
    """
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM conversations "
                    "WHERE lead_id = %s AND direction = 'out'",
                    (lead_id,),
                )
                count = cur.fetchone()[0]
                if count > 0:
                    return False
                cur.execute(
                    "SELECT human_released FROM lead_state WHERE lead_id = %s",
                    (lead_id,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return False
    except Exception as e:
        logger.warning("IS_NEW_CONVERSATION_ERR lead=%s err=%s", lead_id, e)
        return False
    return True


def _is_extranjeria_profile(text: str, slots: dict) -> bool:
    """
    Detecta si el lead tiene perfil de extranjería/visado.
    Busca keywords en el texto y en los slots actuales.
    """
    _KEYWORDS = {
        "visado", "visa", "nie", "tie", "residencia", "extranjero", "extranjera",
        "pasaporte", "passport", "consulado", "consulate", "student visa",
        "permiso de residencia", "tarjeta de residencia", "extranjeria", "extranjería",
    }
    text_lower = (text or "").lower()
    for kw in _KEYWORDS:
        if kw in text_lower:
            return True
    for v in slots.values():
        if isinstance(v, str):
            v_lower = v.lower()
            for kw in _KEYWORDS:
                if kw in v_lower:
                    return True
    return False


def process_message(lead_id: str, text: str, sender_id: str, source: str = "ig") -> str:
    channel = channel_from_source(source)
    text = (text or "").strip()

    # ══ REGLA ABSOLUTA — PRIORIDAD 0 ══════════════════════
    # El agente SOLO interviene en conversaciones NUEVAS.
    # Comprobación ANTES de cualquier otra lógica.
    if not _is_new_conversation(lead_id):
        return ""

    # Anti-spam
    if sender_id and _is_rate_limited(sender_id):
        logger.info("RATE_LIMITED sender=%s", sender_id[:10])
        return "Dame un momento que leo todo lo que me mandas 😊"

    # Restriction 1: No responder si el bot está silenciado (derivado a humano sin release)
    if _bot_is_silenced(lead_id):
        logger.info("BOT_SILENCED lead=%s msg=%r", lead_id, text[:60])
        return "Gracias por tu mensaje. Un agente humano se pondrá en contacto contigo pronto 😊"

    state = load_state(lead_id)
    if not state["ig_user_id"] and sender_id:
        state["ig_user_id"] = sender_id
    state["channel"] = channel
    state["source"] = source

    # Lead state persistente
    init_lead_state(lead_id)
    ls = load_lead_state(lead_id)
    
    # Comprobar expiración por inactividad
    expired = check_lead_expiry(lead_id, ls)
    if expired:
        # Si expiró, reiniciamos para nueva conversación pero conservamos historial
        ls = dict(LEAD_STATE_DEFAULT)
        ls["notas"] = ["Nueva conversación tras expiración"]
        init_lead_state(lead_id)

    mode = state.get("mode", "idle")
    filled_slots = dict(state["slots"])

    filled_for_log = [k for k, v in filled_slots.items() if v is not None and k in LOG_SAFE_SLOTS]
    logger.info("PROCESS lead=%s mode=%s step=%s text=%r | filled=%s", lead_id, mode, state["step"], text[:80], filled_for_log)

    # ── WELCOME MESSAGE ────────────────────────────────────
    # Se envía UNA SOLA VEZ: cuando es el primer mensaje inbound del lead.
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM conversations WHERE lead_id = %s AND direction = 'in'",
                    (lead_id,),
                )
                count_in = cur.fetchone()[0]
    except Exception:
        count_in = 1  # safe default: si falla, asumimos primer mensaje
    if count_in == 1:
        lang = detect_language(text)
        welcome = WELCOME_MESSAGE_EN if lang == "en" else WELCOME_MESSAGE
        log_event(lead_id, channel, "out", welcome, intent="welcome")
        save_state(lead_id, state)
        return welcome

    # ── DETECCIÓN DE PERFIL EXTRANJERÍA ───────────────────
    if not filled_slots.get("perfil_extranjeria"):
        if _is_extranjeria_profile(text, filled_slots):
            filled_slots["perfil_extranjeria"] = True
            state["slots"] = filled_slots
            update_profile_from_slots(lead_id, {"perfil_extranjeria": True})
            ls_data = ls.get("datos_recogidos", {})
            ls_data["perfil_extranjeria"] = True
            ls["datos_recogidos"] = ls_data
            logger.info("EXTRANJERIA_DETECTED lead=%s", lead_id)

    # ── MAX_AGENT_TURNS ────────────────────────────────────
    _es_extranjeria = bool(filled_slots.get("perfil_extranjeria"))
    _max_turns = MAX_AGENT_TURNS_EXTRANJERIA if _es_extranjeria else MAX_AGENT_TURNS_GENERAL
    _agent_turns = ls.get("mensajes_intercambiados", 0)
    if _agent_turns >= _max_turns and ls.get("fase") not in ("listo_para_humano", "cerrado"):
        _msg_max = (
            "Para darte la mejor atención, prefiero que Rosa te llame directamente y te lo "
            "dejamos claro en 2 minutos. ¿Cuándo te viene bien? 🙏"
        )
        ls = advance_lead_phase(lead_id, ls, "listo_para_humano", f"Max turnos ({_max_turns}) alcanzados")
        ls["derivado_a_humano"] = True
        save_lead_state(lead_id, ls)
        _notify_human_handoff(lead_id, ls, text, sender_id)
        log_event(lead_id, channel, "out", _msg_max, intent="max_turns_handoff")
        save_state(lead_id, state)
        return _msg_max

    # Reset
    if is_reset_request(text):
        reset_state(lead_id)
        state = {"step": "product_interest", "slots": {}, "last_question": "", "ig_user_id": sender_id,
                 "channel": channel, "source": source, "wa_sent_at": None, "mode": "insurance", "non_insurance_turns": 0}
        first_q = _question_for_slot("product_interest")
        reply = f"Listo, empezamos de cero 🙂\n\n{first_q}"
        state["last_question"] = first_q
        log_event(lead_id, channel, "out", reply, intent="reset")
        save_state(lead_id, state)
        return reply

    insurance_signal = is_insurance_intent(text, state)

    # Productos fuera de alcance (auto, moto, coche)
    if is_out_of_scope_product(text):
        reply = (
            "Entiendo 😊 Yo me especializo en seguros de Salud 🩺, Vida ❤️ y Hogar 🏠. "
            "Si necesitas algo de esos tres, ¡aquí estoy para ayudarte!"
        )
        log_event(lead_id, channel, "out", reply, intent="out_of_scope_product")
        save_state(lead_id, state)
        return reply

    if mode == "idle":
        if not insurance_signal:
            # Detect returning user
            returning = _detect_returning_user(lead_id)
            if is_greeting_only(text):
                if returning:
                    reply = "¡Hola de nuevo! 😊 Soy Rosa. Me alegro de verte por aquí otra vez. ¿Retomamos lo del seguro o buscas algo nuevo? Llevo Salud 🩺, Vida ❤️ u Hogar 🏠."
                else:
                    reply = "¡Hola! 😊 Soy Rosa. ¿En qué puedo ayudarte hoy? Llevo temas de Salud 🩺, Vida ❤️ u Hogar 🏠."
            else:
                reply = "Te leo 😊 ¿A qué te refieres exactamente? Llevo temas de seguros de salud, vida y hogar."
            log_event(lead_id, channel, "out", reply, intent="idle_smalltalk")
            save_state(lead_id, state)
            return reply
        state["mode"] = "insurance"
        state["non_insurance_turns"] = 0
        mode = "insurance"

    # Cooldown post WA
    if wa_cooldown_active(state):
        wa_link = f"https://wa.me/{DEFAULT_WA_PHONE_E164}"
        reply = f"¡Te sigo leyendo! 😊 Para que no se pierda nada, mejor seguimos por el enlace de WhatsApp que te mandé arriba: {wa_link}"
        log_event(lead_id, channel, "out", reply, intent="post_wa_redirect")
        save_state(lead_id, state)
        return reply

    # Handle greetings / smalltalk
    if is_greeting_only(text) and mode == "insurance" and state.get("step") == "product_interest":
         reply = f"¡Hola de nuevo! 😊 Soy Rosa. Como te decía, ¿en qué puedo ayudarte hoy? Llevo temas de Salud 🩺, Vida ❤️ u Hogar 🏠."
         log_event(lead_id, channel, "out", reply, intent="greeting_human")
         save_state(lead_id, state)
         return reply

    # RAG interrupt
    category = filled_slots.get("product_interest") or "salud"
    if is_product_question(text, state):
        rag = answer_with_rag(text, category=category)
        if rag:
            nxt = next_required_slot(filled_slots) or next_any_slot(filled_slots)
            if nxt:
                follow = _question_for_slot(nxt)
                reply = f"{rag}\n\n---\n\n{follow}"
                state["step"] = nxt
                state["last_question"] = follow
            else:
                reply = rag
            log_event(lead_id, channel, "out", reply, intent="rag_answer")
            save_state(lead_id, state)
            return reply

    # Primera pregunta
    if not state.get("last_question"):
        q = _question_for_slot("product_interest", ab_version=state.get("ab_version", "A"))
        reply = f"¡Perfecto! Soy {AGENT_NAME} 😊\n\n{q}"
        state["step"] = "product_interest"
        state["last_question"] = q
        log_event(lead_id, channel, "out", reply, intent="start_flow")
        save_state(lead_id, state)
        return reply

    # Calcular variables necesarias antes de los bloques que las usan
    explicit_wa = is_explicit_wa_request(text)
    purchase = is_purchase_intent(text)
    nxt = next_required_slot(filled_slots) or next_any_slot(filled_slots)

    # HANDLE "completed" re-entry
    if state.get("step") == "completed":
        if explicit_wa or purchase:
            reply = _wa_message(lead_id, filled_slots, channel, state, reason="post_complete_wa")
            save_state(lead_id, state)
            return reply
        reply = (
            "Ya habíamos terminado con los datos 😊 ¿Te mando la propuesta por WhatsApp?\n"
            "Escribe 'whatsapp' y te paso el enlace, o 'empezar de nuevo' si quieres cambiar algo."
        )
        log_event(lead_id, channel, "out", reply, intent="post_complete_prompt")
        save_state(lead_id, state)
        return reply

    new_slots = extract_slots_from_text(text, state)
    if new_slots:
        filled_slots.update(new_slots)
        state["slots"] = filled_slots
        state["non_insurance_turns"] = 0
        update_profile_from_slots(lead_id, new_slots)
    else:
        current_step = state.get("step", "product_interest")
        valid_for_step = is_valid_answer_for_step(current_step, text, state)
        if not insurance_signal and not valid_for_step:
            turns = state.get("non_insurance_turns", 0) + 1
            state["non_insurance_turns"] = turns
            if turns >= NON_INS_TURNS_THRESHOLD:
                state["mode"] = "idle"
                state["non_insurance_turns"] = 0
                reply = "Sin problema 😊 Cuando quieras retomar lo del seguro, escribe 'seguro' y seguimos."
                log_event(lead_id, channel, "out", reply, intent="drift_back_to_idle")
                save_state(lead_id, state)
                return reply

    # Detectar el producto actual para preguntas personalizadas
    current_product = filled_slots.get("product_interest")

    # WhatsApp explícito
    if explicit_wa:
        if can_send_wa(filled_slots):
            reply = _wa_message(lead_id, filled_slots, channel, state, reason="wa_link_sent")
            save_state(lead_id, state)
            return reply
        else:
            # Pedir mínimos en bullets
            missing = []
            if not filled_slots.get("province"): missing.append("• Tu provincia o código postal")
            if not filled_slots.get("num_people"): missing.append("• Para cuántas personas sería")
            if not filled_slots.get("ages"): missing.append("• Las edades de cada uno")
            
            reply = (
                "¡Claro! Te paso el link encantada 😊 Solo necesito antes estos 3 detallitos mínimos "
                "para que mi compañera tenga algo que enviarte:\n\n"
                + "\n".join(missing) + "\n\n"
                "¿Me los dices un segundo?"
            )
            state["step"] = (next_required_slot(filled_slots) or "province")
            log_event(lead_id, channel, "out", reply, intent="wa_request_missing_data")
            save_state(lead_id, state)
            return reply

    # Compra explícita y flujo completo
    if purchase and flow_is_complete(filled_slots):
        reply = _wa_message(lead_id, filled_slots, channel, state, reason="purchase_handoff")
        state["step"] = "completed"
        save_state(lead_id, state)
        return reply

    # UPGRADE: Auto-WA cuando score alto y ya tenemos gates mínimos
    if AUTO_WA_ENABLED and not wa_cooldown_active(state):
        score = lead_score(filled_slots, last_user_text=text, purchase_intent=purchase)
        logger.info("LEAD_SCORE lead=%s score=%s threshold=%s", lead_id, score, AUTO_WA_SCORE_THRESHOLD)

        gates_ok = can_send_wa(filled_slots) if AUTO_WA_MIN_GATES else True
        if score >= AUTO_WA_SCORE_THRESHOLD and gates_ok:
            pre = "Genial. Para cerrar más rápido y enviarte la propuesta, lo hacemos por WhatsApp 🙂\n\n"
            wa_msg = _wa_message(lead_id, filled_slots, channel, state, reason="auto_wa_high_score")
            reply = pre + wa_msg
            save_state(lead_id, state)
            return reply

    # Siguiente slot requerido
    if nxt is None:
        state["step"] = "completed"
        reply = (
            "¡Perfecto, ya tengo todo lo necesario! 🎉 Con estos datos mi compañera podrá prepararte varias opciones "
            "para que tú mismo las compares y elijas la que más tranquilo te deje.\n\n"
            "¿Te envío el enlace de WhatsApp para que te lo pase en cuanto lo tenga listo?"
        )
        log_event(lead_id, channel, "out", reply, intent="flow_complete")
        save_state(lead_id, state)
        return reply

    q = _question_for_slot(nxt, product=current_product, ab_version=state.get("ab_version", "A"))

    # Micro-cierres con storytelling (sin prometer precios)
    _MICRO_CIERRES: dict = {
        "province": [
            "Perfecto, así lo ajusto bien. ",
            "Genial, con eso afino mucho la búsqueda. ",
        ],
        "num_people": [
            "Perfecto, así lo ajusto bien. ",
            "Entendido. Según si sois más o menos, las opciones cambian bastante. ",
        ],
        "ages": [
            "Perfecto, así lo ajusto bien. ",
            "Esto es clave, porque la edad influye mucho en el precio. ",
        ],
        "budget": [
            "Entendido. Con esto te preparo una propuesta muy alineada. ",
            "Anotado. Así descarto opciones que no te encajen de entrada. ",
        ],
        "has_preexisting": [
            "Esto es importante para que no haya sorpresas después. ",
        ],
        "coverage_preferences": [
            f"{_random_storytelling()} ",
            "Genial, así sé qué priorizar. ",
        ],
    }
    # Micro-cierres con storytelling dinámico
    def _get_micro(nxt_slot: str, product_name: Optional[str], slots_data: dict) -> str:
        base_options = _MICRO_CIERRES.get(nxt_slot, [""])
        
        if product_name == "mascotas":
            name = slots_data.get("pet_name", "tu peque")
            if nxt_slot == "pet_breed": return f"¡Qué nombre más bonito tiene {name}! 😊 ¿De qué raza es? "
            if nxt_slot == "pet_age": return f"Perfecto, anotado lo de {name}. ¿Qué edad tiene? "
            return "Me encantan los animales, seguimos... 🐶 "
            
        if product_name == "viaje":
            dest = slots_data.get("travel_destination", "ese viaje")
            if nxt_slot == "travel_dates": return f"¡Teredme envidia! {dest} suena increíble ✈️ ¿En qué fechas vas? "
            return "¡Qué ganas de viajar me están entrando! 😊 "

        return random.choice(base_options)

    micro = _get_micro(nxt, current_product, filled_slots)

    state["step"] = nxt
    state["last_question"] = q
    msg = micro + q
    log_event(lead_id, channel, "out", msg, intent=f"flow_question:{nxt}")

    # --- Lead Scoring Update ---
    score, tag, prob = calculate_lead_score(lead_id, text, state)
    update_lead_scoring(lead_id, score, tag, prob)
    
    # 🔥 Alerta al equipo si score >= umbral
    try:
        from backend.notifier import send_whatsapp_alert
        slots = state.get("slots", {})
        send_whatsapp_alert({
            "lead_id": lead_id,
            "score": score,
            "name": slots.get("name", ""),
            "product_interest": slots.get("product_interest", ""),
            "last_text": text,
            "sender_id": sender_id,
        })
    except Exception as e:
        logger.warning("ALERT_ERROR lead=%s err=%s", lead_id, e)
    
    # 🚨 Lógica de Citas (Appointment Setter)
    # Si es Gold Opportunity y aún no hemos preguntado por la cita
    if score >= 8 and not filled_slots.get("appointment_requested"):
        # Solo lo ofrecemos si ya tenemos lo básico (product, province)
        if filled_slots.get("product_interest") and (filled_slots.get("province") or filled_slots.get("phone_or_email")):
            prod = current_product or "tu seguro"
            if current_product == "vida":
                action_phrase = "hables 5 minutillos con Sebastián directamente"
                expert_phrase = "Él es el experto y te asesorará mejor que nadie"
            else:
                action_phrase = "hables 5 minutillos conmigo (Rosa) directamente"
                expert_phrase = "Soy la experta en esto y te asesoraré mejor que nadie"

            appointment_q = (
                f"He estado dándole vueltas a lo que me cuentas y, para que no te quede ninguna duda técnica sobre {prod}, "
                f"creo que lo más honesto es que {action_phrase}. "
                f"{expert_phrase}, sin compromiso. ¿Estarías abierto a una llamada corta?"
            )
            msg = f"{msg}\n\n{appointment_q}"
            state["step"] = "appointment_requested"
    
    # 🗓️ Google Calendar: si el cliente aceptó la llamada, proponer slots
    if state.get("step") == "appointment_requested" and filled_slots.get("appointment_requested") is True:
        # Si ya propusimos slots y el cliente eligió uno
        if state.get("calendar_slots_proposed"):
            choice = text.strip()
            slots_proposed = state.get("calendar_slots_proposed", [])
            slot_idx = None
            if choice in ["1", "2", "3"]:
                slot_idx = int(choice) - 1
            else:
                # Intentar matchear por texto
                for i, s in enumerate(slots_proposed):
                    if s.lower() in choice.lower() or choice.lower() in s.lower():
                        slot_idx = i
                        break
            
            if slot_idx is not None and 0 <= slot_idx < len(slots_proposed):
                # Parsear el slot elegido
                slot_text = slots_proposed[slot_idx]
                logger.info("CALENDAR_SLOT_CHOSEN lead=%s slot=%s", lead_id[:12], slot_text)
                
                # Convertir texto a datetime
                now = datetime.now()
                dia = now.date()
                if "Mañana" in slot_text:
                    dia = now.date() + timedelta(days=1)
                hora_match = re.search(r"(\d{1,2}):(\d{2})", slot_text)
                if hora_match:
                    hora = int(hora_match.group(1))
                    minuto = int(hora_match.group(2))
                    slot_dt = datetime(dia.year, dia.month, dia.day, hora, minuto)
                    
                    nombre = filled_slots.get("name", "")
                    producto = current_product or "seguro"
                    
                    # Crear evento en Google Calendar
                    created = create_calendar_event(slot_dt, nombre, sender_id)
                    
                    if created:
                        confirm_msg = f"Perfecto, Rosa te llama el {slot_text}. ¡Hasta entonces! 🙏"
                        logger.info("CALENDAR_EVENT_CREATED lead=%s slot=%s", lead_id[:12], slot_text)
                    else:
                        confirm_msg = "Te apunto para llamarte. Rosa se pone en contacto contigo pronto. 603 44 87 65 🙏"
                    
                    # Notificar al equipo
                    try:
                        from backend.notifier import send_whatsapp_alert
                        send_whatsapp_alert({
                            "lead_id": lead_id,
                            "score": score,
                            "name": nombre,
                            "product_interest": producto,
                            "last_text": f"Llamada agendada: {slot_text}",
                            "sender_id": sender_id,
                        })
                    except Exception:
                        pass
                    
                    msg = f"{msg}\n\n{confirm_msg}"
                    state["step"] = "call_scheduled"
            else:
                # No entendió la elección, repetir opciones
                slots_list = "\n".join(f"{i+1}) {s}" for i, s in enumerate(slots_proposed))
                msg = f"{msg}\n\nNo entendí bien. Estas son las opciones:\n{slots_list}\n\n¿Cuál te viene mejor? (responde 1, 2 o 3)"
        else:
            # Proponer slots por primera vez
            slots_proposed = propose_call_slots()
            if slots_proposed:
                state["calendar_slots_proposed"] = slots_proposed
                slots_list = "\n".join(f"{i+1}) {s}" for i, s in enumerate(slots_proposed))
                msg = f"{msg}\n\nPerfecto. Te propongo estas opciones:\n{slots_list}\n\n¿Cuál te viene mejor?"
                logger.info("CALENDAR_SLOTS_PROPOSED lead=%s slots=%s", lead_id[:12], slots_proposed)
            else:
                # Calendar no disponible, fallback
                msg = f"{msg}\n\nTe apunto para llamarte. Rosa se pone en contacto contigo pronto. 603 44 87 65 🙏"
                state["step"] = "call_scheduled"

    # Alerta "Oportunidad de Oro" (n8n notification)
    if score >= AUTO_WA_SCORE_THRESHOLD and mode == "insurance":
        # Estrategia de separación: Adeslas -> Comp A, Asisa -> Comp B
        # Si no hay proveedor decidido, usamos una por defecto o esperamos
        provider = filled_slots.get("insurance_provider")
        if provider:
            company_tag = "adeslas_sf" if provider == "adeslas" else "asisa_sf"
            _notify_n8n_followup(lead_id, state.get("step", ""), sender_id, company_tag=company_tag)
        else:
            # Si no ha elegido pero el score es alto, avisamos pero marcamos como 'undecided'
            _notify_n8n_followup(lead_id, state.get("step", ""), sender_id, company_tag="undecided")
        if random.random() < 0.2:
            msg = f"Veo que tienes las cosas muy claras y eso nos ayuda mucho a asesorarte mejor 😊 {msg}"

    # Actualizar lead_state persistente
    update_lead_state_from_message(lead_id, text, sender_id, state, ls)
    
    save_state(lead_id, state)
    return msg

def handle_incoming(sender_id: str, text: str) -> str:
    text = (text or "").strip()
    lead_id = lead_id_from_ig_user(sender_id)
    ensure_lead_row(lead_id, source_channel="instagram_dm", category="salud")
    ensure_profile_row(lead_id)
    init_lead_state(lead_id)
    log_event(lead_id, "ig", "in", text, intent="ig_in")
    return process_message(lead_id, text, sender_id=sender_id, source="instagram_dm")

# ══════════════════════════════════════════════════════
# Pydantic models
# ══════════════════════════════════════════════════════

class AgentRespondReq(BaseModel):
    lead_id:    str
    text:       str
    source:     str = "web"
    sender_id:  Optional[str] = None
    ig_user_id: Optional[str] = None
    raw:        Optional[Any] = None

class AgentRespondResp(BaseModel):
    ok:            bool
    lead_id:       str
    reply_text:    str
    intent:        Optional[str] = None
    needs_handoff: bool = False
    sent_via_meta: bool = False

class RagQuery(BaseModel):
    question: str
    category: str = "salud"
    route:    Optional[str] = None
    top_k:    int = 5

# ══════════════════════════════════════════════════════
# PRODUCT PLAYBOOKS (cargados desde product_playbooks.json)
# ══════════════════════════════════════════════════════

_PLAYBOOKS_PATH = os.path.join(os.path.dirname(__file__), "product_playbooks.json")
_PLAYBOOKS: Dict[str, dict] = {}  # producto -> playbook
_PLAYBOOKS_LOADED = False


def _load_playbooks() -> None:
    """Carga los playbooks desde product_playbooks.json al iniciar el servidor."""
    global _PLAYBOOKS, _PLAYBOOKS_LOADED
    try:
        if not os.path.exists(_PLAYBOOKS_PATH):
            logger.warning("PLAYBOOKS_NO_FILE %s — se usará prompt base", _PLAYBOOKS_PATH)
            _PLAYBOOKS_LOADED = False
            return
        with open(_PLAYBOOKS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        productos = data.get("productos", [])
        for pb in productos:
            prod = pb.get("producto")
            if prod:
                _PLAYBOOKS[prod] = pb
        _PLAYBOOKS_LOADED = True
        logger.info("PLAYBOOKS_LOADED %d productos desde %s", len(_PLAYBOOKS), _PLAYBOOKS_PATH)
    except Exception as e:
        logger.error("PLAYBOOKS_LOAD_ERROR %s", e)
        _PLAYBOOKS_LOADED = False


def get_playbook(producto: str) -> Optional[dict]:
    """Devuelve el playbook para un producto, o None si no existe."""
    return _PLAYBOOKS.get(producto)


def build_playbook_prompt(producto: str) -> str:
    """
    Construye un bloque de texto con las reglas del playbook para inyectar
    en el prompt del agente. Si no hay playbook, devuelve un prompt genérico.
    """
    pb = get_playbook(producto)
    if not pb:
        return ""
    
    lines = []
    lines.append("=== INSTRUCCIONES COMERCIALES ===")
    
    resumen = pb.get("resumen_comercial", "")
    if resumen:
        lines.append(f"Resumen del producto: {resumen}")
    
    preguntas = pb.get("preguntas_iniciales", [])
    if preguntas:
        lines.append("Preguntas recomendadas para el cliente:")
        for p in preguntas:
            lines.append(f"  - {p}")
    
    datos = pb.get("datos_minimos", [])
    if datos:
        lines.append("Datos mínimos necesarios:")
        for d in datos:
            lines.append(f"  - {d}")
    
    objeciones = pb.get("objeciones_frecuentes", [])
    if objeciones:
        lines.append("Objeciones frecuentes del cliente:")
        for o in objeciones:
            lines.append(f"  - {o}")
    
    limites = pb.get("limites", [])
    if limites:
        lines.append("LÍMITES — lo que NO debes prometer:")
        for l in limites:
            lines.append(f"  - {l}")
    
    derivar = pb.get("cuando_derivar_humano", [])
    if derivar:
        lines.append("Cuándo derivar a un agente humano:")
        for d in derivar:
            lines.append(f"  - {d}")
    
    # --- SECCIÓN A: argumentos_clave ---
    argumentos = pb.get("argumentos_clave", [])
    if argumentos:
        lines.append("")
        lines.append("## Argumentos comerciales")
        lines.append("Úsalos cuando el cliente pregunte por qué contratar con nosotros o pida que le convenzas:")
        for a in argumentos:
            lines.append(f"- {a}")
    
    # --- SECCIÓN B: respuestas_objeciones ---
    respuestas_obj = pb.get("respuestas_objeciones", [])
    if respuestas_obj:
        lines.append("")
        lines.append("## Cómo responder objeciones")
        for o in respuestas_obj:
            lines.append(f'Si dicen "{o["objecion"]}": {o["respuesta"]}')
    
    lines.append("=== FIN INSTRUCCIONES ===")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════

@app.get("/health")
async def health_check():
    """
    Healthcheck para Oracle Load Balancer y Docker.
    Verifica: servidor activo + conexión DB + embedder listo.
    Responde siempre en < 2 segundos.
    """
    checks = {}
    overall = "ok"

    # 1. DB check
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {str(e)[:60]}"
        overall = "degraded"

    # 2. Embedder check
    checks["embedder"] = "ok" if embedder is not None else "unavailable"

    # 3. AI client check
    checks["ai_client"] = "ok" if _ai_client is not None else "unavailable"

    # 4. KB check (contar chunks)
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM kb_documents")
                kb_count = cur.fetchone()[0]
        checks["kb_chunks"] = kb_count
    except Exception:
        checks["kb_chunks"] = 0

    status_code = 200 if overall == "ok" else 206

    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall,
            "agent": AGENT_NAME,
            "version": "2.0.0",
            "checks": checks,
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.get("/")
async def root():
    """Endpoint raíz — confirma que el agente está online."""
    return {
        "agent": AGENT_NAME,
        "status": "online",
        "docs": "/docs",
        "health": "/health"
    }

# -------- Playbooks debug endpoint --------

@app.get("/playbooks")
def playbooks_list(producto: Optional[str] = None):
    """Devuelve los playbooks cargados. Si se especifica producto, solo ese."""
    if not _PLAYBOOKS_LOADED:
        return {"ok": False, "error": "Playbooks no cargados", "playbooks_loaded": False}
    if producto:
        pb = get_playbook(producto)
        if not pb:
            return {"ok": False, "error": f"Producto '{producto}' no encontrado"}
        return {"ok": True, "playbook": pb}
    return {
        "ok": True,
        "playbooks_loaded": True,
        "total": len(_PLAYBOOKS),
        "productos": list(_PLAYBOOKS.keys()),
        "playbooks": list(_PLAYBOOKS.values()),
    }

# -------- Follow-up / rescate de leads --------

@app.post("/api/followup/pending")
def followup_pending():
    """Devuelve leads que abandonaron el flujo y pueden ser rescatados."""
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cs.lead_id, cs.step, cs.slots, cs.ig_user_id, cs.updated_at "
                    "FROM conversation_state cs "
                    "WHERE cs.mode = 'insurance' "
                    "  AND cs.step NOT IN ('completed') "
                    "  AND cs.updated_at < now() - interval %s "
                    "ORDER BY cs.updated_at ASC LIMIT 50",
                    (f"{FOLLOWUP_DELAY_HOURS} hours",),
                )
                rows = cur.fetchall()
    except Exception as e:
        logger.error("FOLLOWUP_QUERY_ERROR %s", e)
        return {"ok": False, "error": str(e)}

    leads = []
    for r in rows:
        slots = _safe_json(r[2]) or {}
        leads.append({
            "lead_id": str(r[0]),
            "step": r[1],
            "slots_filled": [k for k, v in slots.items() if v is not None],
            "ig_user_id": r[3] or "",
            "last_activity": str(r[4]),
        })
    return {"ok": True, "pending": leads}

@app.post("/api/followup/send")
def followup_send(lead_id: str, background_tasks: BackgroundTasks):
    """Envía un mensaje de seguimiento a un lead que abandonó el flujo."""
    state = load_state(lead_id)
    if state.get("step") == "completed":
        return {"ok": False, "reason": "already_completed"}
    sender_id = state.get("ig_user_id", "")
    if not sender_id:
        return {"ok": False, "reason": "no_ig_user_id"}

    slots = state.get("slots", {})
    nxt = next_required_slot(slots)
    name = slots.get("name", "")
    greeting = f"¡Hola{', ' + name if name else ''}! " if name else "¡Hola! "

    if nxt:
        followup_msg = (
            f"{greeting}Soy Rosa 😊 Me quedé con ganas de seguir ayudándote con lo del seguro. "
            f"Solo me faltaba un detallito para avanzar:\n\n"
            f"{_question_for_slot(nxt)}\n\n"
            f"¿Seguimos? Sin compromiso, estoy aquí para lo que necesites 🙂"
        )
    else:
        followup_msg = (
            f"{greeting}Soy Rosa 😊 Vi que ya habíamos avanzado bastante con lo del seguro. "
            f"¿Te mando la propuesta por WhatsApp? Escribe 'whatsapp' y listo 🙂"
        )

    log_event(lead_id, "ig", "out", followup_msg, intent="followup_rescue")
    if not _meta_sender_is_blocked(sender_id):
        background_tasks.add_task(send_with_lag_sync, sender_id, followup_msg)
    
    # Notificar a n8n
    _notify_n8n_followup(lead_id, state.get("step", "unknown"), sender_id)

    return {"ok": True, "message_sent": followup_msg}

# -------- Lead export (CRM) --------

@app.get("/api/leads/export")
def leads_export(limit: int = 100):
    """Exporta leads con sus datos para integración CRM."""
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT l.lead_id, l.source_channel, l.category, l.created_at, l.last_activity_at, "
                "  p.province, p.num_insured, p.ages, p.copay_preference, p.has_preexisting, p.preexisting_details, "
                "  cs.step, cs.mode, cs.ig_user_id "
                "FROM leads l "
                "LEFT JOIN lead_profile p ON l.lead_id = p.lead_id "
                "LEFT JOIN conversation_state cs ON l.lead_id = cs.lead_id "
                "ORDER BY l.last_activity_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    result = []
    for r in rows:
        result.append({
            "lead_id": str(r[0]), "source": r[1], "category": r[2],
            "created_at": str(r[3]), "last_activity": str(r[4]),
            "province": r[5], "num_insured": r[6],
            "ages": _safe_json(r[7]), "copay": r[8],
            "preexisting": r[9], "preexisting_details": r[10],
            "flow_step": r[11], "mode": r[12], "ig_user_id": r[13] or "",
        })
    return {"ok": True, "count": len(result), "leads": result}

@app.get("/api/analytics/dashboard")
def analytics_dashboard():
    """Estadísticas de conversión y abandono por paso."""
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                # Total leads
                cur.execute("SELECT COUNT(*) FROM leads")
                total_leads = cur.fetchone()[0]

                # Completed vs Pending
                cur.execute("SELECT step, COUNT(*) FROM conversation_state GROUP BY step")
                steps_counts = dict(cur.fetchall())

                # Conversion rate (simplificado)
                completed = steps_counts.get("completed", 0)
                conversion_rate = (completed / total_leads * 100) if total_leads > 0 else 0

                # Lead Temperature Stats
                cur.execute("SELECT temp_tag, COUNT(*) FROM leads GROUP BY temp_tag")
                temp_stats = dict(cur.fetchall())
                
                cur.execute("SELECT AVG(lead_score) FROM leads")
                avg_score = float(cur.fetchone()[0] or 0)

                # Drop-off funnel
                funnel = []
                for s_def in SLOT_FLOW:
                    key = s_def["key"]
                    count = steps_counts.get(key, 0)
                    funnel.append({"step": key, "count": count})
                
                # Top channels
                cur.execute("SELECT source_channel, COUNT(*) FROM leads GROUP BY source_channel")
                channels = dict(cur.fetchall())

        return {
            "ok": True,
            "total_leads": total_leads,
            "completed": completed,
            "conversion_rate": f"{conversion_rate:.1f}%",
            "avg_lead_score": round(float(avg_score), 1),
            "temperature_stats": temp_stats,
            "funnel": funnel,
            "channels": channels
        }
    except Exception as e:
        logger.error("ANALYTICS_ERROR: %s", e)
        return {"ok": False, "error": str(e)}

@app.post("/api/agent/respond", response_model=AgentRespondResp)
async def api_agent_respond(req: AgentRespondReq, background_tasks: BackgroundTasks):
    if not (req.lead_id or "").strip():
        raise HTTPException(400, {"error": "lead_id es obligatorio"})
    if not (req.text or "").strip():
        raise HTTPException(400, {"error": "text es obligatorio"})

    text      = req.text.strip()
    source    = normalize_source_channel(req.source)
    lead_id   = normalize_lead_id(req.lead_id.strip(), req.ig_user_id)
    sender_id = req.ig_user_id or req.sender_id or ""
    channel   = channel_from_source(source)

    cached = _dedup_get(lead_id, text)
    if cached:
        return AgentRespondResp(ok=True, lead_id=lead_id, reply_text=cached)

    ensure_lead_row(lead_id, source_channel=source, category="salud")
    ensure_profile_row(lead_id)
    log_event(lead_id, channel, "in", text, intent="api_in")

    # Restriction 1: No responder si el bot está silenciado
    if _bot_is_silenced(lead_id):
        logger.info("API_BOT_SILENCED lead=%s msg=%r", lead_id, text[:60])
        return AgentRespondResp(
            ok=True,
            lead_id=lead_id,
            reply_text="Gracias por tu mensaje. Un agente humano se pondrá en contacto contigo pronto 😊",
            intent="bot_silenced",
            needs_handoff=True,
            sent_via_meta=False,
        )

    try:
        agent_reply = process_message(lead_id, text, sender_id=sender_id, source=source)
    except Exception as e:
        logger.error("PROCESS_MESSAGE_ERROR lead=%s err=%s", lead_id, e, exc_info=True)
        agent_reply = "Disculpa, ha habido un problema técnico 😊 ¿Me lo puedes repetir?"

    current_state  = load_state(lead_id)
    needs_handoff  = current_state.get("step") == "completed"
    _dedup_set(lead_id, text, agent_reply)

    sent_via_meta = False
    if META_ACCESS_TOKEN and sender_id and not _meta_sender_is_blocked(sender_id):
        background_tasks.add_task(send_with_lag_sync, sender_id, agent_reply)
        sent_via_meta = True

    return AgentRespondResp(
        ok=True,
        lead_id=lead_id,
        reply_text=agent_reply,
        intent=f"flow_question:{current_state.get('step')}",
        needs_handoff=needs_handoff,
        sent_via_meta=sent_via_meta,
    )

@app.post("/api/agent/voice")
async def api_agent_voice(
    request: FastAPIRequest,
    background_tasks: BackgroundTasks,
):
    """
    Recibe un audio de WhatsApp, lo transcribe con Whisper (OpenAI-compatible)
    y procesa el texto transcrito como un mensaje normal.
    """
    import tempfile

    WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")

    try:
        form = await request.form()
        audio_file = form.get("audio")
        lead_id_raw = form.get("lead_id", "")
        sender_id = form.get("sender_id", "")
        source = form.get("source", "whatsapp")

        if not audio_file:
            return {"ok": False, "error": "No se recibió archivo de audio"}

        logger.info("AUDIO_RECIBIDO lead=%s sender=%s size=%s", lead_id_raw[:12], sender_id[:10], getattr(audio_file, "size", 0))

        # Guardar temporalmente el audio
        content = await audio_file.read()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        # Transcribir con Whisper (OpenAI-compatible)
        transcribed = None
        if _ai_client is not None:
            try:
                with open(tmp_path, "rb") as f:
                    transcript = _ai_client.audio.transcriptions.create(
                        model=WHISPER_MODEL,
                        file=f,
                        language="es",
                    )
                transcribed = transcript.text.strip()
                logger.info("TRANSCRIPCION lead=%s texto=%r", lead_id_raw[:12], transcribed[:100])
            except Exception as e:
                logger.error("WHISPER_ERROR lead=%s err=%s", lead_id_raw[:12], e)
        else:
            logger.warning("WHISPER_NO_CLIENT lead=%s (no hay _ai_client)", lead_id_raw[:12])

        # Limpiar archivo temporal
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        if not transcribed:
            return {
                "ok": True,
                "lead_id": lead_id_raw,
                "reply_text": "No he podido escuchar bien el audio. ¿Puedes escribirme lo que necesitas? 🙏",
                "transcribed": None,
            }

        # Procesar como mensaje de texto normal
        source_norm = normalize_source_channel(source)
        lead_id = normalize_lead_id(lead_id_raw.strip(), sender_id)
        channel = channel_from_source(source_norm)

        ensure_lead_row(lead_id, source_channel=source_norm, category="salud")
        ensure_profile_row(lead_id)
        log_event(lead_id, channel, "in", transcribed, intent="voice_in")

        if _bot_is_silenced(lead_id):
            return {
                "ok": True,
                "lead_id": lead_id,
                "reply_text": "Gracias por tu mensaje. Un agente humano se pondrá en contacto contigo pronto 😊",
                "transcribed": transcribed,
            }

        try:
            agent_reply = process_message(lead_id, transcribed, sender_id=sender_id, source=source_norm)
        except Exception as e:
            logger.error("VOICE_PROCESS_ERROR lead=%s err=%s", lead_id, e, exc_info=True)
            agent_reply = "Disculpa, ha habido un problema técnico 😊 ¿Me lo puedes repetir?"

        _dedup_set(lead_id, transcribed, agent_reply)

        if META_ACCESS_TOKEN and sender_id and not _meta_sender_is_blocked(sender_id):
            background_tasks.add_task(send_with_lag_sync, sender_id, agent_reply)

        logger.info("VOICE_RESPONSE lead=%s reply=%r", lead_id, agent_reply[:80])

        return {
            "ok": True,
            "lead_id": lead_id,
            "reply_text": agent_reply,
            "transcribed": transcribed,
        }

    except Exception as e:
        logger.error("VOICE_ENDPOINT_ERROR err=%s", e, exc_info=True)
        return {
            "ok": True,
            "lead_id": lead_id_raw if 'lead_id_raw' in dir() else "",
            "reply_text": "No he podido escuchar bien el audio. ¿Puedes escribirme lo que necesitas? 🙏",
            "transcribed": None,
        }


@app.get("/meta/webhook")
async def meta_webhook_verify(request: FastAPIRequest):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == META_VERIFY_TOKEN:
        logger.info("META_WEBHOOK_VERIFIED")
        return PlainTextResponse(content=p.get("hub.challenge", ""))
    raise HTTPException(403, "Verification failed")

@app.post("/meta/webhook")
async def meta_webhook_receive(request: FastAPIRequest, background_tasks: BackgroundTasks):
    raw = await request.body()
    sig256 = request.headers.get("X-Hub-Signature-256", "")
    sig1 = request.headers.get("X-Hub-Signature", "")
    if not verify_meta_signature(raw, sig256, sig1):
        raise HTTPException(403, "Bad signature")
    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    logger.info("META_INCOMING %s", json.dumps(payload, ensure_ascii=False)[:400])

    def _extract_messages(payload: dict) -> List[Tuple[str, str]]:
        results: List[Tuple[str, str]] = []
        for entry in payload.get("entry", []):
            page_id = str(entry.get("id", ""))
            
            # 1.entry.messaging (DMs)
            for ev in (entry.get("messaging") or []):
                if ev.get("read") or ev.get("delivery"):
                    logger.debug("META_IGNORE read/delivery")
                    continue
                msg_obj = ev.get("message") or {}
                if msg_obj.get("is_echo"):
                    logger.debug("META_IGNORE is_echo")
                    continue
                sid = str((ev.get("sender") or {}).get("id", ""))
                if not sid or sid == page_id:
                    continue
                t = (msg_obj.get("text") or "").strip()
                if t:
                    results.append((sid, t))

            # 2.entry.changes (webhook general)
            for ch in (entry.get("changes") or []):
                if ch.get("field") == "messages":
                    val = ch.get("value") or {}
                    for m in (val.get("messages") or []):
                        sid = str((m.get("from") or {}).get("id", ""))
                        t = ((m.get("text") or {}).get("body") or "").strip()
                        if sid and t and sid != page_id:
                            results.append((sid, t))
        return results

    messages = _extract_messages(payload)
    for sender_id, text in messages:
        lead_id = lead_id_from_ig_user(sender_id)

        # Restriction 1: No responder si el bot está silenciado
        if _bot_is_silenced(lead_id):
            logger.info("META_BOT_SILENCED lead=%s msg=%r", lead_id, text[:60])
            continue

        cached = _dedup_get(lead_id, text)
        if cached:
            if not _meta_sender_is_blocked(sender_id):
                background_tasks.add_task(send_with_lag_sync, sender_id, cached)
            continue

        reply = handle_incoming(sender_id, text)
        _dedup_set(lead_id, text, reply)
        if not _meta_sender_is_blocked(sender_id):
            background_tasks.add_task(send_with_lag_sync, sender_id, reply)

    return {"ok": True, "processed": len(messages)}


# ══════════════════════════════════════════════════════
# WHATSAPP CLOUD API ENDPOINTS
# ══════════════════════════════════════════════════════

@app.get("/wa/inbound")
async def wa_webhook_verify(request: FastAPIRequest):
    """Verificación del webhook de WhatsApp Cloud API (Meta)."""
    p = request.query_params
    mode = p.get("hub.mode")
    token = p.get("hub.verify_token")
    challenge = p.get("hub.challenge")
    if mode == "subscribe" and token == META_VERIFY_TOKEN and challenge:
        logger.info("WA_WEBHOOK_VERIFIED")
        return PlainTextResponse(content=challenge)
    raise HTTPException(403, "Verification failed")


@app.post("/wa/inbound")
async def wa_webhook_receive(request: FastAPIRequest, background_tasks: BackgroundTasks):
    """Recibe mensajes entrantes de WhatsApp Cloud API."""
    raw = await request.body()
    sig256 = request.headers.get("X-Hub-Signature-256", "")
    sig1 = request.headers.get("X-Hub-Signature", "")
    if not verify_meta_signature(raw, sig256, sig1):
        raise HTTPException(403, "Bad signature")
    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    logger.info("WA_INCOMING %s", json.dumps(payload, ensure_ascii=False)[:400])

    def _extract_wa_messages(payload: dict) -> List[Tuple[str, str, Optional[str]]]:
        """
        Extrae mensajes de WhatsApp Cloud API.
        Formato: entry[].changes[].value.messages[0]
        Devuelve: [(sender_id, text, contact_name), ...]
        """
        results: List[Tuple[str, str, Optional[str]]] = []
        for entry in payload.get("entry", []):
            for ch in (entry.get("changes") or []):
                if ch.get("field") != "messages":
                    continue
                value = ch.get("value", {})
                # Obtener nombre del contacto si está disponible
                contacts = value.get("contacts", [])
                contact_name = None
                if contacts:
                    profile = contacts[0].get("profile", {})
                    contact_name = profile.get("name")
                
                for msg in (value.get("messages") or []):
                    msg_type = msg.get("type", "")
                    msg_from = str(msg.get("from", "")).strip()
                    
                    if not msg_from:
                        continue
                    
                    text_body = ""
                    if msg_type == "text":
                        text_body = (msg.get("text", {}) or {}).get("body", "").strip()
                    elif msg_type == "interactive":
                        # Botón reply
                        btn_reply = msg.get("interactive", {}).get("button_reply", {})
                        text_body = btn_reply.get("title", "").strip()
                    
                    if msg_from and text_body:
                        results.append((msg_from, text_body, contact_name))
        return results

    messages = _extract_wa_messages(payload)
    for sender_id, text, contact_name in messages:
        lead_id = lead_id_from_ig_user(sender_id)

        # Restriction 1: No responder si el bot está silenciado
        if _bot_is_silenced(lead_id):
            logger.info("WA_BOT_SILENCED lead=%s msg=%r", lead_id, text[:60])
            continue

        cached = _dedup_get(lead_id, text)
        if cached:
            if not _meta_sender_is_blocked(sender_id):
                background_tasks.add_task(_send_wa_with_lag, sender_id, cached)
            continue

        reply = handle_incoming(sender_id, text)
        _dedup_set(lead_id, text, reply)
        if not _meta_sender_is_blocked(sender_id):
            background_tasks.add_task(_send_wa_with_lag, sender_id, reply)

    return {"ok": True, "processed": len(messages)}

# -------- RAG debug endpoint --------

# -------- Lead State debug endpoint --------

@app.get("/api/lead/{lead_id}/state")
def lead_state_get(lead_id: str):
    """Devuelve el lead_state completo para depuración."""
    try:
        ls = load_lead_state(lead_id)
        return {"ok": True, "lead_id": lead_id, "lead_state": ls}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/lead/{lead_id}/release-bot")
def lead_release_bot(lead_id: str):
    """
    Libera al bot para que pueda responder de nuevo a un lead que había sido
    derivado a humano. Establece human_released = True.
    """
    try:
        ls = load_lead_state(lead_id)
        if not ls.get("derivado_a_humano"):
            return {"ok": False, "error": "El lead no está derivado a humano"}
        ls["human_released"] = True
        ls["notas"].append(f"Bot liberado manualmente para responder")
        save_lead_state(lead_id, ls)
        logger.info("BOT_RELEASED lead=%s", lead_id)
        return {"ok": True, "lead_id": lead_id, "human_released": True}
    except Exception as e:
        logger.error("BOT_RELEASE_ERR lead=%s err=%s", lead_id, e)
        return {"ok": False, "error": str(e)}


# -------- Dashboard: listar leads del día --------

@app.get("/api/insights")
def insights_list(aplicado: Optional[bool] = None, token: str = ""):
    """Devuelve insights del evaluador. Protegido con KB_ADMIN_TOKEN."""
    if KB_ADMIN_TOKEN and token != KB_ADMIN_TOKEN:
        raise HTTPException(403, "Invalid token")
    try:
        from backend.agent_evaluator import get_insights
        resultados = get_insights(aplicado=aplicado)
        return {"ok": True, "total": len(resultados), "insights": resultados}
    except Exception as e:
        logger.error("INSIGHTS_LIST_ERR %s", e)
        return {"ok": False, "error": str(e)}


@app.post("/api/insights/{insight_id}/apply")
def insights_apply(insight_id: int, token: str = ""):
    """Marca un insight como aplicado. Protegido con KB_ADMIN_TOKEN."""
    if KB_ADMIN_TOKEN and token != KB_ADMIN_TOKEN:
        raise HTTPException(403, "Invalid token")
    try:
        from backend.agent_evaluator import apply_insight
        ok = apply_insight(insight_id)
        return {"ok": ok, "insight_id": insight_id}
    except Exception as e:
        logger.error("INSIGHTS_APPLY_ERR %s", e)
        return {"ok": False, "error": str(e)}


@app.get("/api/leads")
def leads_list(token: str = ""):
    """Devuelve los leads del día con su lead_state. Protegido con KB_ADMIN_TOKEN."""
    if KB_ADMIN_TOKEN and token != KB_ADMIN_TOKEN:
        raise HTTPException(403, "Invalid token")
    
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    l.lead_id::text,
                    l.last_activity_at,
                    ls.fase,
                    ls.producto_detectado,
                    ls.datos_recogidos,
                    ls.mensajes_intercambiados,
                    ls.ultimo_mensaje,
                    ls.derivado_a_humano,
                    ls.notas,
                    cs.ig_user_id,
                    cs.slots->>'name' as contact_name
                FROM leads l
                LEFT JOIN lead_state ls ON ls.lead_id = l.lead_id
                LEFT JOIN conversation_state cs ON cs.lead_id = l.lead_id
                WHERE l.last_activity_at >= %s
                ORDER BY l.last_activity_at DESC
                LIMIT 100
            """, (today_start,))
            rows = cur.fetchall()
    
    leads = []
    for row in rows:
        lead_id, last_activity, fase, producto, datos_raw, msgs, ultimo, derivado, notas_raw, ig_uid, contact_name = row
        
        datos = {}
        if datos_raw:
            try:
                if isinstance(datos_raw, str):
                    datos = json.loads(datos_raw)
                else:
                    datos = datos_raw
            except:
                datos = {}
        
        sender_id = ig_uid or ""
        
        leads.append({
            "lead_id": lead_id,
            "fase": fase or "nuevo",
            "producto_detectado": producto,
            "datos_recogidos": datos,
            "mensajes_intercambiados": msgs or 0,
            "ultimo_mensaje": ultimo.isoformat() if ultimo else None,
            "last_activity_at": last_activity.isoformat() if last_activity else None,
            "derivado_a_humano": bool(derivado),
            "sender_id": sender_id,
            "contact_name": contact_name,
        })
    
    return {"ok": True, "total": len(leads), "leads": leads}


# -------- Dashboard HTML --------

import os as _os
_DASHBOARD_PATH = _os.path.join(_os.path.dirname(__file__), "..", "dashboard", "index.html")


@app.get("/dashboard")
def dashboard(token: str = ""):
    """Sirve el panel de leads estático."""
    if KB_ADMIN_TOKEN and token != KB_ADMIN_TOKEN:
        raise HTTPException(403, "Invalid token")
    try:
        with open(_DASHBOARD_PATH, "r", encoding="utf-8") as f:
            html = f.read()
        return HTMLResponse(content=html)
    except FileNotFoundError:
        raise HTTPException(404, "Dashboard not found")


@app.post("/rag/ask")
def rag_ask(q: RagQuery):
    out = answer_with_rag(q.question, category=q.category, route=q.route)
    return {"answer": out}

# -------- KB endpoints --------

@app.get("/kb/stats")
def kb_stats():
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT category, COUNT(*) FROM kb_documents GROUP BY category ORDER BY COUNT(*) DESC")
            counts = cur.fetchall()
            cur.execute("SELECT source_file, created_at FROM kb_documents ORDER BY created_at DESC LIMIT 10")
            last_files = cur.fetchall()
    return {
        "counts": [{"category": c[0], "chunks": int(c[1])} for c in counts],
        "last": [{"source_file": r[0], "created_at": str(r[1])} for r in last_files],
    }

@app.get("/kb/search")
def kb_search(q: str, category: str = "salud", top_k: int = 5):
    if embedder is None:
        raise HTTPException(500, "Embedder no disponible.")
    q_vec = _embed_query_vec(q)
    if not q_vec:
        raise HTTPException(500, "No se pudo crear embedding.")
    sql = (
        "SELECT source_file, chunk_id, 1-(embedding <=> %s::vector) AS score, chunk_text "
        "FROM kb_documents WHERE category=%s AND embedding IS NOT NULL "
        "ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (q_vec, category, q_vec, top_k))
            rows = cur.fetchall()
    return {
        "hits": [
            {"source_file": r[0], "chunk_id": r[1], "score": float(r[2] or 0), "text": r[3]}
            for r in rows
        ]
    }

@app.get("/admin")
def admin_dashboard(request: FastAPIRequest):
    """Panel de administración interno con métricas."""
    token = request.headers.get("X-Admin-Token", "")
    if KB_ADMIN_TOKEN and token != KB_ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Acceso denegado")
    
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT fase, COUNT(*) FROM lead_state GROUP BY fase ORDER BY fase")
                fases = cur.fetchall()
                
                cur.execute("SELECT temp_tag, COUNT(*) FROM leads GROUP BY temp_tag")
                scores = cur.fetchall()
                
                cur.execute("SELECT AVG(mensajes_intercambiados) FROM lead_state WHERE fase = 'listo_para_humano'")
                avg_msgs = cur.fetchone()[0] or 0
                
                cur.execute("""
                    SELECT ls.lead_id, ls.fase, ls.producto_detectado, 
                           ls.mensajes_intercambiados, ls.ultimo_mensaje
                    FROM lead_state ls
                    ORDER BY ls.updated_at DESC LIMIT 10
                """)
                ultimos = cur.fetchall()
                
                try:
                    cur.execute("SELECT AVG(score), MIN(score), MAX(score), COUNT(*) FROM conversation_scores")
                    eval_stats = cur.fetchone()
                except Exception:
                    eval_stats = (0, 0, 0, 0)
                
                try:
                    cur.execute("""
                        SELECT cs.ab_version, COUNT(*) as leads,
                               SUM(CASE WHEN ls.fase='listo_para_humano' THEN 1 ELSE 0 END) as convertidos
                        FROM conversation_state cs
                        JOIN lead_state ls ON cs.lead_id = ls.lead_id
                        GROUP BY cs.ab_version
                    """)
                    ab_data = cur.fetchall()
                except Exception:
                    ab_data = []
    except Exception as e:
        return HTMLResponse(f"<h1>Error</h1><p>{e}</p>", status_code=500)
    
    rows_fases = "".join(f"<tr><td>{r[0]}</td><td>{r[1]}</td></tr>" for r in fases)
    rows_scores = "".join(f"<tr><td>{r[0]}</td><td>{r[1]}</td></tr>" for r in scores)
    rows_ultimos = "".join(
        f"<tr><td>{str(r[0])[:8]}...</td><td>{r[1]}</td><td>{r[2] or '-'}</td><td>{r[3]}</td><td>{str(r[4] or '')[:30]}</td></tr>"
        for r in ultimos
    )
    rows_ab = "".join(
        f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{round(r[2]/r[1]*100, 1) if r[1] > 0 else 0}%</td></tr>"
        for r in ab_data
    )
    
    html = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><title>Admin — Valentín Protección Integral</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #fff; color: #333; }}
h1 {{ color: #1a3a5c; border-bottom: 2px solid #c8a96e; padding-bottom: 10px; }}
h2 {{ color: #1a3a5c; margin-top: 30px; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0 20px; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #1a3a5c; color: white; }}
tr:nth-child(even) {{ background: #f8f9fa; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }}
.stat-card {{ background: #f0f2f5; border-radius: 8px; padding: 15px; text-align: center; }}
.stat-card .num {{ font-size: 2em; font-weight: bold; color: #1a3a5c; }}
.stat-card .label {{ font-size: 0.9em; color: #666; }}
</style>
</head>
<body>
<h1>📊 Panel de Administración</h1>
<p>Valentín Protección Integral — Agentes de Seguros Vinculados</p>
<div class="stats">
<div class="stat-card"><div class="num">{avg_msgs:.1f}</div><div class="label">Mensajes promedio hasta cierre</div></div>
<div class="stat-card"><div class="num">{eval_stats[3]}</div><div class="label">Conversaciones evaluadas</div></div>
<div class="stat-card"><div class="num">{eval_stats[0] or 0:.1f}</div><div class="label">Score promedio evaluador</div></div>
</div>
<h2>📋 Leads por Fase</h2>
<table><tr><th>Fase</th><th>Count</th></tr>{rows_fases}</table>
<h2>🌡️ Score Distribution</h2>
<table><tr><th>Tag</th><th>Count</th></tr>{rows_scores}</table>
<h2>🧪 A/B Testing</h2>
<table><tr><th>Versión</th><th>Leads</th><th>Convertidos</th><th>Tasa %</th></tr>{rows_ab}</table>
<h2>🕐 Últimos 10 Leads</h2>
<table><tr><th>Lead ID</th><th>Fase</th><th>Producto</th><th>Mensajes</th><th>Último mensaje</th></tr>{rows_ultimos}</table>
<h2>📈 Evaluador</h2>
<table><tr><th>Métrica</th><th>Valor</th></tr>
<tr><td>Score promedio</td><td>{eval_stats[0] or 0:.2f}</td></tr>
<tr><td>Score mínimo</td><td>{eval_stats[1] or 0}</td></tr>
<tr><td>Score máximo</td><td>{eval_stats[2] or 0}</td></tr>
<tr><td>Total evaluaciones</td><td>{eval_stats[3]}</td></tr>
</table>
</body></html>"""
    
    return HTMLResponse(html)


@app.get("/admin/evaluations")
def admin_evaluations(request: FastAPIRequest):
    """Devuelve las últimas 50 evaluaciones. Protegido con KB_ADMIN_TOKEN."""
    token = request.headers.get("X-Admin-Token", "")
    if KB_ADMIN_TOKEN and token != KB_ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Acceso denegado")
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT lead_id, score, turnos_total, turnos_hasta_producto, 
                           turnos_hasta_datos, fase_final, evaluated_at
                    FROM conversation_scores
                    ORDER BY evaluated_at DESC LIMIT 50
                """)
                rows = cur.fetchall()
        return {
            "ok": True,
            "evaluations": [
                {
                    "lead_id": str(r[0]),
                    "score": r[1],
                    "turnos_total": r[2],
                    "turnos_hasta_producto": r[3],
                    "turnos_hasta_datos": r[4],
                    "fase_final": r[5],
                    "evaluated_at": str(r[6]),
                }
                for r in rows
            ]
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/internal/run-followups")
def run_followups(request: FastAPIRequest):
    """Recorre todos los leads con flujo incompleto y aplica follow-up."""
    token = request.headers.get("X-Admin-Token", "")
    if KB_ADMIN_TOKEN and token != KB_ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Acceso denegado")
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT cs.lead_id FROM conversation_state cs
                    JOIN lead_state ls ON cs.lead_id = ls.lead_id
                    WHERE cs.mode = 'insurance'
                    AND cs.step NOT IN ('completed')
                    AND ls.fase NOT IN ('cerrado', 'listo_para_humano')
                """)
                leads = cur.fetchall()
        sent = 0
        for (lead_id,) in leads:
            if send_followup_if_needed(lead_id):
                sent += 1
        return {"ok": True, "processed": len(leads), "sent": sent}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/kb/sync-from-cloud")
async def kb_sync_from_cloud(request: FastAPIRequest, background_tasks: BackgroundTasks):
    """
    Descarga PDFs desde OCI Object Storage → ./data/ → ingesta en pgvector.
    Protegido con X-Admin-Token.
    Ejecución en background para no timeout.
    """
    if KB_ADMIN_TOKEN:
        token = request.headers.get("X-Admin-Token", "")
        if token != KB_ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail="Forbidden")

    async def _run():
        from backend.oci_storage import sync_bucket_to_local
        from backend.kb_ingest import ingest_directory
        logger.info("KB_SYNC_CLOUD iniciando...")
        sync_result = sync_bucket_to_local(local_data_dir=KB_DATA_DIR)
        logger.info("KB_SYNC_CLOUD descarga: %s", sync_result)
        ingest_result = ingest_directory(
            data_dir=KB_DATA_DIR,
            embedder=embedder,
            db_dsn=DB_DSN
        )
        total_chunks = sum(r.get("chunks_inserted", 0) for r in ingest_result)
        logger.info("KB_SYNC_CLOUD ingesta: %d chunks nuevos", total_chunks)

    background_tasks.add_task(_run)
    return {"ok": True, "message": "Sync iniciado en background — revisa /kb/stats en 2-3 minutos"}


@app.post("/kb/upload-to-cloud")
async def kb_upload_to_cloud(request: FastAPIRequest):
    """
    Sube todos los PDFs locales de ./data/ al bucket OCI.
    Para usar desde el servidor cuando quieras sincronizar al revés.
    Protegido con X-Admin-Token.
    """
    if KB_ADMIN_TOKEN:
        token = request.headers.get("X-Admin-Token", "")
        if token != KB_ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail="Forbidden")

    from backend.oci_storage import sync_local_to_bucket
    result = sync_local_to_bucket(local_data_dir=KB_DATA_DIR)
    return {"ok": True, "result": result}


@app.get("/kb/cloud-status")
async def kb_cloud_status():
    """
    Lista los PDFs disponibles en el bucket OCI.
    Sin autenticación — solo metadata, no contenido.
    """
    from backend.oci_storage import list_bucket_objects
    objects = list_bucket_objects()
    by_category: dict = {}
    for obj in objects:
        if not obj["name"].endswith(".pdf"):
            continue
        parts = obj["name"].split("/")
        cat = parts[0] if len(parts) > 1 else "raiz"
        by_category.setdefault(cat, []).append(obj["name"].split("/")[-1])

    return {
        "bucket": os.getenv("OCI_BUCKET_NAME", "agente-rosa-kb"),
        "total_pdfs": len([o for o in objects if o["name"].endswith(".pdf")]),
        "by_category": {k: len(v) for k, v in by_category.items()},
        "files": by_category,
    }


@app.post("/kb/ingest")
async def kb_ingest(request: FastAPIRequest, background_tasks: BackgroundTasks):
    """
    Ingesta un PDF o todo el directorio ./data/ en la KB.
    Body JSON opcional: {"file": "nombre_archivo.pdf", "category": "salud"}
    Si no se pasa file, procesa todo ./data/
    Requiere header: X-Admin-Token = KB_ADMIN_TOKEN (si está configurado)
    """
    # Auth check
    if KB_ADMIN_TOKEN:
        token = request.headers.get("X-Admin-Token", "")
        if token != KB_ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail="Forbidden")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    from backend.kb_ingest import ingest_pdf, ingest_directory

    file_name = body.get("file")
    category = body.get("category", "salud")

    if file_name:
        filepath = os.path.join(KB_DATA_DIR, file_name)
        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail=f"Archivo no encontrado: {file_name}")
        result = ingest_pdf(filepath, category=category, embedder=embedder, db_dsn=DB_DSN)
        return {"ok": True, "results": [result]}
    else:
        results = ingest_directory(data_dir=KB_DATA_DIR, embedder=embedder, db_dsn=DB_DSN)
        return {"ok": True, "results": results}
