#!/usr/bin/env python3
"""
Añade sistema de estado persistente por lead (lead_state) a backend/app.py.

Cambios:
1. Nueva tabla lead_state en bootstrap_schema()
2. Funciones: init_lead_state, load_lead_state, save_lead_state, advance_lead_phase
3. Integración en process_message() para avanzar fases automáticamente
4. Aviso WhatsApp a Rosa/Sebastián cuando needs_handoff=true
"""

import re

APP_PY = 'backend/app.py'

with open(APP_PY, 'r', encoding='utf-8') as f:
    content = f.read()

# ============================================================
# 1. AÑADIR TABLA lead_state AL SCHEMA
# ============================================================

old_ddl_state = '''    ddl_state = """
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
    );"""'''

new_ddl_state = '''    ddl_state = """
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
    );"""'''

if old_ddl_state in content:
    content = content.replace(old_ddl_state, new_ddl_state)
    print("[OK] Tabla lead_state añadida al schema")
else:
    print("[WARN] No se encontró ddl_state")

# Añadir lead_state a la ejecución del bootstrap
old_bootstrap_exec = '''            for ddl in (
                ddl_ext_uuid, ddl_ext_vector,
                ddl_leads, ddl_profile, ddl_state,
                ddl_conversations,
                ddl_kb, ddl_place_cache,
                ddl_indexes,
            ):'''

new_bootstrap_exec = '''            for ddl in (
                ddl_ext_uuid, ddl_ext_vector,
                ddl_leads, ddl_profile, ddl_state, ddl_lead_state,
                ddl_conversations,
                ddl_kb, ddl_place_cache,
                ddl_indexes,
            ):'''

if old_bootstrap_exec in content:
    content = content.replace(old_bootstrap_exec, new_bootstrap_exec)
    print("[OK] lead_state incluido en bootstrap")
else:
    print("[WARN] No se encontró bootstrap_exec")


# ============================================================
# 2. AÑADIR FUNCIONES DE GESTIÓN DE lead_state
# ============================================================

# Insertar después de reset_state() y antes de update_profile_from_slots()
old_after_reset = '''def reset_state(lead_id: str) -> None:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM conversation_state WHERE lead_id = %s", (lead_id,))
        conn.commit()

def update_profile_from_slots'''

new_lead_state_funcs = '''def reset_state(lead_id: str) -> None:
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
    },
    "mensajes_intercambiados": 0,
    "ultimo_mensaje": None,
    "derivado_a_humano": False,
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
                "ultimo_mensaje, derivado_a_humano, notas "
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
            "notas": _safe_json(row[6]) or [],
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
                     mensajes_intercambiados, ultimo_mensaje, derivado_a_humano, notas, updated_at)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT (lead_id) DO UPDATE SET
                    fase = EXCLUDED.fase,
                    producto_detectado = EXCLUDED.producto_detectado,
                    datos_recogidos = EXCLUDED.datos_recogidos,
                    mensajes_intercambiados = EXCLUDED.mensajes_intercambiados,
                    ultimo_mensaje = EXCLUDED.ultimo_mensaje,
                    derivado_a_humano = EXCLUDED.derivado_a_humano,
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
    """
    Envía un WhatsApp a Rosa/Sebastián (34603448765) cuando un lead
    está listo para asesoría humana.
    """
    datos = ls.get("datos_recogidos", {})
    producto = ls.get("producto_detectado", "no detectado")
    
    mensaje = (
        f"\U0001f514 Lead listo para asesoría\n"
        f"Nombre: {datos.get('nombre', '?')}\n"
        f"Producto: {producto}\n"
        f"Datos: {json.dumps(datos, ensure_ascii=False)}\n"
        f"Último mensaje: {ultimo_texto[:100]}\n"
        f"Responder: wa.me/{sender_id}"
    )
    
    try:
        _meta_send_wa("34603448765", mensaje)
        logger.info("HANDOFF_NOTIFIED lead=%s to=34603448765", lead_id)
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
    
    # Si el lead dice que no le interesa
    text_lower = text.lower().strip()
    if any(p in text_lower for p in ["no me interesa", "no gracias", "no quiero", "déjalo", "no, gracias"]):
        ls = advance_lead_phase(lead_id, ls, "cerrado", f"Lead desistió: {text[:50]}")
    
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

def update_profile_from_slots'''

if old_after_reset in content:
    content = content.replace(old_after_reset, new_lead_state_funcs)
    print("[OK] Funciones lead_state añadidas (init, load, save, advance, notify)")
else:
    print("[WARN] No se encontró reset_state + update_profile_from_slots")


# ============================================================
# 3. INTEGRAR lead_state EN process_message()
# ============================================================

# Añadir init_lead_state y load_lead_state al inicio de process_message
old_process_start = '''def process_message(lead_id: str, text: str, sender_id: str, source: str = "ig") -> str:
    channel = channel_from_source(source)
    text = (text or "").strip()

    # Anti-spam
    if sender_id and _is_rate_limited(sender_id):
        logger.info("RATE_LIMITED sender=%s", sender_id[:10])
        return "Dame un momento que leo todo lo que me mandas 😊"

    state = load_state(lead_id)
    if not state["ig_user_id"] and sender_id:
        state["ig_user_id"] = sender_id
    state["channel"] = channel
    state["source"] = source'''

new_process_start = '''def process_message(lead_id: str, text: str, sender_id: str, source: str = "ig") -> str:
    channel = channel_from_source(source)
    text = (text or "").strip()

    # Anti-spam
    if sender_id and _is_rate_limited(sender_id):
        logger.info("RATE_LIMITED sender=%s", sender_id[:10])
        return "Dame un momento que leo todo lo que me mandas 😊"

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
        init_lead_state(lead_id)'''

if old_process_start in content:
    content = content.replace(old_process_start, new_process_start)
    print("[OK] lead_state integrado en process_message()")
else:
    print("[WARN] No se encontró process_message start")


# ============================================================
# 4. AÑADIR ACTUALIZACIÓN DE lead_state ANTES DEL RETURN
# ============================================================

# Buscar el último save_state antes del return final en process_message
# Añadir update_lead_state_from_message justo antes de save_state
old_save_state_final = '''    save_state(lead_id, state)
    return msg'''

new_save_state_final = '''    # Actualizar lead_state persistente
    update_lead_state_from_message(lead_id, text, sender_id, state, ls)
    
    save_state(lead_id, state)
    return msg'''

# Contar ocurrencias para reemplazar solo la última (la del return msg)
count = content.count(old_save_state_final)
if count >= 1:
    # Reemplazar la última ocurrencia (justo antes del return msg final)
    # Buscar desde el final
    last_idx = content.rfind(old_save_state_final)
    if last_idx != -1:
        content = content[:last_idx] + new_save_state_final + content[last_idx + len(old_save_state_final):]
        print(f"[OK] update_lead_state_from_message añadido antes del save_state final")
    else:
        print("[WARN] No se pudo encontrar save_state final")
else:
    print("[WARN] No se encontró save_state + return msg")


# ============================================================
# 5. AÑADIR ENDPOINT /api/lead/<id>/state PARA DEBUG
# ============================================================

# Buscar el último endpoint antes de RAG
old_rag_endpoint = '''@app.post("/rag/ask")
def rag_ask(q: RagQuery):'''

new_lead_state_endpoint = '''# -------- Lead State debug endpoint --------

@app.get("/api/lead/{lead_id}/state")
def lead_state_get(lead_id: str):
    """Devuelve el lead_state completo para depuración."""
    try:
        ls = load_lead_state(lead_id)
        return {"ok": True, "lead_id": lead_id, "lead_state": ls}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/rag/ask")
def rag_ask(q: RagQuery):'''

if old_rag_endpoint in content:
    content = content.replace(old_rag_endpoint, new_lead_state_endpoint)
    print("[OK] Endpoint GET /api/lead/{lead_id}/state añadido")
else:
    print("[WARN] No se encontró rag_ask endpoint")


# ============================================================
# 6. ACTUALIZAR handle_incoming PARA INCLUIR LEAD STATE
# ============================================================

# handle_incoming ya llama a ensure_lead_row y ensure_profile_row
# Añadir init_lead_state
old_handle_incoming = '''def handle_incoming(sender_id: str, text: str) -> str:
    text = (text or "").strip()
    lead_id = lead_id_from_ig_user(sender_id)
    ensure_lead_row(lead_id, source_channel="instagram_dm", category="salud")
    ensure_profile_row(lead_id)
    log_event(lead_id, "ig", "in", text, intent="ig_in")
    return process_message(lead_id, text, sender_id=sender_id, source="instagram_dm")'''

new_handle_incoming = '''def handle_incoming(sender_id: str, text: str) -> str:
    text = (text or "").strip()
    lead_id = lead_id_from_ig_user(sender_id)
    ensure_lead_row(lead_id, source_channel="instagram_dm", category="salud")
    ensure_profile_row(lead_id)
    init_lead_state(lead_id)
    log_event(lead_id, "ig", "in", text, intent="ig_in")
    return process_message(lead_id, text, sender_id=sender_id, source="instagram_dm")'''

if old_handle_incoming in content:
    content = content.replace(old_handle_incoming, new_handle_incoming)
    print("[OK] init_lead_state añadido a handle_incoming")
else:
    print("[WARN] No se encontró handle_incoming")


# ============================================================
# GUARDAR
# ============================================================

with open(APP_PY, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\n[OK] {APP_PY} actualizado con sistema lead_state")
print("Cambios realizados:")
print("  1. Nueva tabla lead_state en bootstrap_schema()")
print("  2. Funciones: init/load/save/advance/check/notify")
print("  3. Integración en process_message()")
print("  4. Endpoint GET /api/lead/{lead_id}/state")
print("  5. init_lead_state en handle_incoming()")
