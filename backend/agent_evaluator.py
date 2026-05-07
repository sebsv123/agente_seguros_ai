#!/usr/bin/env python3
"""
agent_evaluator.py — Feedback loop automático para mejora del agente.

Evalúa cada conversación cerrada/derivada, detecta patrones de fallo,
genera insights y produce un resumen semanal.

Uso:
    from agent_evaluator import evaluate_conversation, generate_weekly_summary

    # Evaluar al cerrar/derivar un lead
    evaluate_conversation(lead_id)

    # Generar resumen semanal (cron: lunes 9:00)
    generate_weekly_summary()
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("evaluator")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [evaluator] %(message)s",
)

# ── Config ────────────────────────────────────────────
DB_DSN = os.getenv("DB_DSN", "postgresql://agente:agente_pw@localhost:5433/agente_ai")
DEFAULT_WA_PHONE = os.getenv("DEFAULT_WA_PHONE_E164", "34603448765")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8000/dashboard?token=EAWDSK9DSF88GJAS33RG")

# Umbrales para detección de patrones
DERIVACION_MIN = 0.40  # 40%
ABANDONO_MAX_PREGUNTA = 3  # más de 3 abandonos en misma pregunta = alerta
TIEMPO_RESPUESTA_MAX = 300  # 5 minutos en segundos

# ── DB ────────────────────────────────────────────────
def _get_db():
    import psycopg
    return psycopg.connect(DB_DSN)


def _bootstrap_insights_table() -> None:
    """Crea la tabla agent_insights si no existe."""
    ddl = """
    CREATE TABLE IF NOT EXISTS agent_insights (
        id          BIGSERIAL PRIMARY KEY,
        producto    VARCHAR(50) NOT NULL,
        tipo        VARCHAR(30) NOT NULL,
        descripcion TEXT NOT NULL,
        sugerencia  TEXT,
        score       DECIMAL(3,2) DEFAULT 0.00,
        aplicado    BOOLEAN DEFAULT FALSE,
        fecha       TIMESTAMPTZ DEFAULT now(),
        fecha_aplicado TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS idx_insights_producto ON agent_insights(producto);
    CREATE INDEX IF NOT EXISTS idx_insights_aplicado ON agent_insights(aplicado);
    """
    try:
        with _get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()
    except Exception as e:
        logger.error("BOOTSTRAP_INSIGHTS_ERR %s", e)


# ── SCORING automático ────────────────────────────────

def score_conversation(lead_id: str) -> Tuple[int, Dict[str, Any]]:
    """
    Calcula una puntuación 0-100 para una conversación.
    Devuelve (score, detalles).
    """
    score = 0
    detalles: Dict[str, Any] = {"lead_id": lead_id, "rubricas": {}}

    with _get_db() as conn:
        with conn.cursor() as cur:
            # 1. Cargar lead_state
            cur.execute(
                "SELECT fase, producto_detectado, datos_recogidos, mensajes_intercambiados, "
                "derivado_a_humano, ultimo_mensaje FROM lead_state WHERE lead_id = %s",
                (lead_id,),
            )
            ls_row = cur.fetchone()
            if not ls_row:
                return 0, {"error": "No lead_state found"}

            fase, producto, datos_raw, msgs, derivado, ultimo = ls_row
            datos = datos_raw if isinstance(datos_raw, dict) else (json.loads(datos_raw) if datos_raw else {})

            # 2. Cargar conversaciones para medir tiempos
            cur.execute(
                "SELECT direction, created_at FROM conversations "
                "WHERE lead_id = %s ORDER BY created_at ASC",
                (lead_id,),
            )
            convs = cur.fetchall()

    # --- Rúbrica 1: datos_recogidos completos (+25) ---
    campos_requeridos = ["nombre", "edad", "codigo_postal"]
    completados = sum(1 for c in campos_requeridos if datos.get(c))
    pct_datos = completados / len(campos_requeridos)
    score_datos = int(25 * pct_datos)
    score += score_datos
    detalles["rubricas"]["datos_completos"] = {"puntos": score_datos, "completados": f"{completados}/{len(campos_requeridos)}"}

    # --- Rúbrica 2: lead derivado a humano (+25) ---
    if derivado:
        score += 25
        detalles["rubricas"]["derivado"] = {"puntos": 25, "valor": True}
    else:
        detalles["rubricas"]["derivado"] = {"puntos": 0, "valor": False}

    # --- Rúbrica 3: mensajes hasta derivación < 8 (+20) ---
    if msgs and msgs < 8:
        score += 20
        detalles["rubricas"]["mensajes_eficientes"] = {"puntos": 20, "mensajes": msgs}
    elif msgs:
        # Puntuación parcial: a más mensajes, menos puntos
        pts = max(0, 20 - (msgs - 8) * 2)
        score += pts
        detalles["rubricas"]["mensajes_eficientes"] = {"puntos": pts, "mensajes": msgs}
    else:
        detalles["rubricas"]["mensajes_eficientes"] = {"puntos": 0, "mensajes": 0}

    # --- Rúbrica 4: sin mensajes de confusión (+15) ---
    confusion_patterns = [
        r"\b(no entiendo|no comprendo|repite|otra vez|confundido|no sé qué decir)\b",
        r"\b(error|mal|incorrecto|no es eso)\b",
        r"\b(ayuda|help|no funciona)\b",
    ]
    confusion_detected = False
    for direction, created_at in convs:
        if direction == "in":
            # Revisar el texto de cada mensaje entrante
            cur = _get_db().cursor()
            cur.execute(
                "SELECT text FROM conversations WHERE lead_id = %s AND direction = 'in' AND created_at = %s",
                (lead_id, created_at),
            )
            text_row = cur.fetchone()
            if text_row:
                text = text_row[0].lower()
                for pat in confusion_patterns:
                    if re.search(pat, text):
                        confusion_detected = True
                        break
            cur.close()

    if not confusion_detected:
        score += 15
        detalles["rubricas"]["sin_confusion"] = {"puntos": 15, "valor": True}
    else:
        detalles["rubricas"]["sin_confusion"] = {"puntos": 0, "valor": False}

    # --- Rúbrica 5: lead respondió en < 2 min (+15) ---
    tiempos_respuesta = []
    last_out_time = None
    for direction, created_at in convs:
        if direction == "out":
            last_out_time = created_at
        elif direction == "in" and last_out_time:
            delta = (created_at - last_out_time).total_seconds()
            tiempos_respuesta.append(delta)

    if tiempos_respuesta:
        media = sum(tiempos_respuesta) / len(tiempos_respuesta)
        if media < 120:  # 2 minutos
            score += 15
            detalles["rubricas"]["respuesta_rapida"] = {"puntos": 15, "media_seg": round(media, 1)}
        elif media < 300:
            score += 8
            detalles["rubricas"]["respuesta_rapida"] = {"puntos": 8, "media_seg": round(media, 1)}
        else:
            detalles["rubricas"]["respuesta_rapida"] = {"puntos": 0, "media_seg": round(media, 1)}
    else:
        detalles["rubricas"]["respuesta_rapida"] = {"puntos": 0, "media_seg": None}

    detalles["score_total"] = score
    detalles["producto"] = producto
    detalles["fase"] = fase
    return score, detalles


# ── DETECCIÓN de patrones de fallo ────────────────────

def detectar_patrones_fallo(producto: str) -> List[Dict[str, Any]]:
    """
    Analiza las últimas 10 conversaciones del mismo producto
    y detecta patrones de fallo.
    """
    alertas: List[Dict[str, Any]] = []

    with _get_db() as conn:
        with conn.cursor() as cur:
            # Últimas 10 conversaciones del producto
            cur.execute("""
                SELECT ls.lead_id, ls.fase, ls.mensajes_intercambiados,
                       ls.derivado_a_humano, ls.datos_recogidos
                FROM lead_state ls
                JOIN leads l ON l.lead_id = ls.lead_id
                WHERE ls.producto_detectado = %s
                ORDER BY ls.updated_at DESC
                LIMIT 10
            """, (producto,))
            conversaciones = cur.fetchall()

    if not conversaciones:
        return alertas

    total = len(conversaciones)
    derivados = sum(1 for c in conversaciones if c[3])
    tasa_derivacion = derivados / total if total > 0 else 0

    # --- Patrón 1: Tasa de derivación < 40% ---
    if tasa_derivacion < DERIVACION_MIN:
        alertas.append({
            "tipo": "baja_derivacion",
            "producto": producto,
            "descripcion": f"Tasa de derivación del {tasa_derivacion:.0%} en últimas {total} conversaciones",
            "sugerencia": "Revisar playbook: puede faltar información clave o las preguntas no son efectivas",
            "score": round(tasa_derivacion, 2),
        })

    # --- Patrón 2: Abandonos en misma pregunta ---
    # Analizar en qué paso abandonaron los leads no derivados
    with _get_db() as conn:
        with conn.cursor() as cur:
            for c in conversaciones:
                lead_id, fase, msgs, derivado, _ = c
                if not derivado and fase != "cerrado":
                    # Buscar el último step conocido
                    cur.execute(
                        "SELECT step FROM conversation_state WHERE lead_id = %s",
                        (lead_id,),
                    )
                    step_row = cur.fetchone()
                    if step_row:
                        step = step_row[0]
                        alertas.append({
                            "tipo": "abandono_en_paso",
                            "producto": producto,
                            "descripcion": f"Lead abandonó en paso '{step}' tras {msgs} mensajes",
                            "sugerencia": f"Revisar la pregunta asociada al paso '{step}' — puede ser confusa o demasiado pronto",
                            "score": 0.3,
                        })

    # --- Patrón 3: Tiempo medio de respuesta > 5 min ---
    with _get_db() as conn:
        with conn.cursor() as cur:
            tiempos = []
            for c in conversaciones:
                lead_id = c[0]
                cur.execute(
                    "SELECT direction, created_at FROM conversations "
                    "WHERE lead_id = %s ORDER BY created_at ASC",
                    (lead_id,),
                )
                convs = cur.fetchall()
                last_out = None
                for direction, created_at in convs:
                    if direction == "out":
                        last_out = created_at
                    elif direction == "in" and last_out:
                        delta = (created_at - last_out).total_seconds()
                        tiempos.append(delta)

    if tiempos:
        media = sum(tiempos) / len(tiempos)
        if media > TIEMPO_RESPUESTA_MAX:
            alertas.append({
                "tipo": "friccion_alta",
                "producto": producto,
                "descripcion": f"Tiempo medio de respuesta del lead: {media/60:.1f} min (umbral: 5 min)",
                "sugerencia": "Marcar producto como 'fricción alta' — leads dudan o necesitan más información antes de responder",
                "score": round(max(0, 1 - media / 600), 2),
            })

    return alertas


# ── GENERACIÓN de insights ────────────────────────────

def generar_insight(producto: str, alerta: Dict[str, Any]) -> Optional[int]:
    """
    Guarda un insight en la tabla agent_insights.
    Si ya existe uno similar (mismo producto, tipo y descripción), no lo duplica.
    Devuelve el ID del insight o None.
    """
    _bootstrap_insights_table()

    with _get_db() as conn:
        with conn.cursor() as cur:
            # Evitar duplicados
            cur.execute(
                "SELECT id FROM agent_insights WHERE producto = %s AND tipo = %s AND descripcion = %s",
                (producto, alerta["tipo"], alerta["descripcion"]),
            )
            if cur.fetchone():
                return None

            cur.execute(
                "INSERT INTO agent_insights (producto, tipo, descripcion, sugerencia, score) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (producto, alerta["tipo"], alerta["descripcion"],
                 alerta.get("sugerencia", ""), alerta.get("score", 0)),
            )
            insight_id = cur.fetchone()[0]
        conn.commit()

    logger.info("INSIGHT_GENERATED id=%s producto=%s tipo=%s", insight_id, producto, alerta["tipo"])
    return insight_id


# ── EVALUACIÓN completa de una conversación ──────────

def evaluate_conversation(lead_id: str) -> Dict[str, Any]:
    """
    Evalúa una conversación completa: scoring + detección de patrones + insights.
    Se llama automáticamente cuando un lead se cierra o se deriva.
    """
    logger.info("EVALUATING lead=%s", lead_id)

    # 1. Scoring
    score, detalles = score_conversation(lead_id)
    logger.info("SCORE lead=%s score=%d/100", lead_id, score)

    # 2. Detectar patrones si hay producto
    producto = detalles.get("producto")
    if producto:
        alertas = detectar_patrones_fallo(producto)
        for alerta in alertas:
            generar_insight(producto, alerta)
            logger.warning("PATRON_DETECTED producto=%s tipo=%s", producto, alerta["tipo"])

    # 3. Guardar score en leads
    try:
        with _get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE leads SET lead_score = %s WHERE lead_id = %s",
                    (score, lead_id),
                )
            conn.commit()
    except Exception as e:
        logger.error("SCORE_SAVE_ERR lead=%s %s", lead_id, e)

    return {"lead_id": lead_id, "score": score, "detalles": detalles}


# ── ENDPOINTS para insights ──────────────────────────

def get_insights(aplicado: Optional[bool] = False) -> List[Dict[str, Any]]:
    """Devuelve insights filtrados por estado 'aplicado'."""
    _bootstrap_insights_table()
    resultados = []

    with _get_db() as conn:
        with conn.cursor() as cur:
            if aplicado is not None:
                cur.execute(
                    "SELECT id, producto, tipo, descripcion, sugerencia, score, aplicado, fecha, fecha_aplicado "
                    "FROM agent_insights WHERE aplicado = %s ORDER BY score DESC, fecha DESC",
                    (aplicado,),
                )
            else:
                cur.execute(
                    "SELECT id, producto, tipo, descripcion, sugerencia, score, aplicado, fecha, fecha_aplicado "
                    "FROM agent_insights ORDER BY score DESC, fecha DESC"
                )
            for row in cur.fetchall():
                resultados.append({
                    "id": row[0],
                    "producto": row[1],
                    "tipo": row[2],
                    "descripcion": row[3],
                    "sugerencia": row[4],
                    "score": float(row[5]) if row[5] else 0,
                    "aplicado": bool(row[6]),
                    "fecha": row[7].isoformat() if row[7] else None,
                    "fecha_aplicado": row[8].isoformat() if row[8] else None,
                })

    return resultados


def apply_insight(insight_id: int) -> bool:
    """
    Marca un insight como aplicado.
    En el futuro, aquí se podría actualizar automáticamente el playbook JSON.
    """
    _bootstrap_insights_table()
    try:
        with _get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE agent_insights SET aplicado = TRUE, fecha_aplicado = now() WHERE id = %s",
                    (insight_id,),
                )
            conn.commit()
        logger.info("INSIGHT_APPLIED id=%s", insight_id)
        return True
    except Exception as e:
        logger.error("INSIGHT_APPLY_ERR id=%s %s", insight_id, e)
        return False


# ── RESUMEN SEMANAL ──────────────────────────────────

def generate_weekly_summary() -> Dict[str, Any]:
    """
    Genera un resumen de la última semana y lo envía por WhatsApp.
    Se ejecuta cada lunes a las 9:00 vía APScheduler.
    """
    desde = datetime.now() - timedelta(days=7)

    with _get_db() as conn:
        with conn.cursor() as cur:
            # Total leads procesados
            cur.execute(
                "SELECT COUNT(*) FROM lead_state WHERE updated_at >= %s",
                (desde,),
            )
            total_leads = cur.fetchone()[0] or 0

            # Tasa de derivación
            cur.execute(
                "SELECT COUNT(*) FROM lead_state WHERE updated_at >= %s AND derivado_a_humano = TRUE",
                (desde,),
            )
            derivados = cur.fetchone()[0] or 0
            tasa_derivacion = (derivados / total_leads * 100) if total_leads > 0 else 0

            # Producto más activo
            cur.execute("""
                SELECT producto_detectado, COUNT(*) as cnt
                FROM lead_state
                WHERE updated_at >= %s AND producto_detectado IS NOT NULL
                GROUP BY producto_detectado
                ORDER BY cnt DESC
                LIMIT 1
            """, (desde,))
            prod_row = cur.fetchone()
            producto_mas_activo = prod_row[0] if prod_row else "N/A"

    # Mejor insight de la semana
    _bootstrap_insights_table()
    mejor_insight = "Ninguno"
    with _get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT descripcion FROM agent_insights "
                "WHERE fecha >= %s ORDER BY score DESC LIMIT 1",
                (desde,),
            )
            insight_row = cur.fetchone()
            if insight_row:
                mejor_insight = insight_row[0][:100]

    # Construir mensaje
    mensaje = (
        f"\U0001f4ca Resumen semanal del agente:\n"
        f"- {total_leads} leads procesados\n"
        f"- {tasa_derivacion:.0f}% tasa de derivación\n"
        f"- Producto más activo: {producto_mas_activo}\n"
        f"- Insight de la semana: {mejor_insight}\n"
        f"- Panel completo: {DASHBOARD_URL}"
    )

    # Enviar WhatsApp
    try:
        # Intentar usar _meta_send_wa si está disponible en el contexto
        from backend.app import _meta_send_wa
        _meta_send_wa(DEFAULT_WA_PHONE, mensaje)
        logger.info("WEEKLY_SUMMARY_SENT to=%s", DEFAULT_WA_PHONE)
    except ImportError:
        logger.warning("WEEKLY_SUMMARY_NOT_SENT (app._meta_send_wa no disponible)")
        logger.info("WEEKLY_SUMMARY_CONTENT: %s", mensaje)

    return {
        "total_leads": total_leads,
        "derivados": derivados,
        "tasa_derivacion": round(tasa_derivacion, 1),
        "producto_mas_activo": producto_mas_activo,
        "mejor_insight": mejor_insight,
        "mensaje_enviado": mensaje,
    }


# ── CLI ──────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Agent Evaluator — Feedback loop automático")
    parser.add_argument("--evaluate", help="Evaluar una conversación por lead_id")
    parser.add_argument("--insights", action="store_true", help="Listar insights no aplicados")
    parser.add_argument("--apply", type=int, help="Aplicar insight por ID")
    parser.add_argument("--weekly", action="store_true", help="Generar resumen semanal")
    parser.add_argument("--score", help="Calcular score para un lead_id")
    args = parser.parse_args()

    if args.evaluate:
        result = evaluate_conversation(args.evaluate)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.score:
        score, detalles = score_conversation(args.score)
        print(f"Score: {score}/100")
        print(json.dumps(detalles, ensure_ascii=False, indent=2, default=str))

    elif args.insights:
        insights = get_insights(aplicado=False)
        print(json.dumps(insights, ensure_ascii=False, indent=2, default=str))

    elif args.apply:
        ok = apply_insight(args.apply)
        print(f"Insight {args.apply} aplicado: {ok}")

    elif args.weekly:
        result = generate_weekly_summary()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
