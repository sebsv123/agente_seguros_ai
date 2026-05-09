"""
notifier.py — Alertas automáticas al equipo cuando un lead alcanza score alto.

Envía un WhatsApp al número del equipo con el resumen del lead caliente.
Se ejecuta en background para no frenar la respuesta al cliente.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("notifier")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [notifier] %(message)s",
)

# ── Config ────────────────────────────────────────────
TEAM_WHATSAPP_NUMBER = os.getenv("TEAM_WHATSAPP_NUMBER", "34603448765")
ALERT_SCORE_THRESHOLD = int(os.getenv("ALERT_SCORE_THRESHOLD", "8"))

# Memoria de leads ya notificados (lead_id -> True)
_notified_leads: Dict[str, bool] = {}


def _build_alert_text(lead_data: Dict[str, Any]) -> str:
    """Construye el texto de la alerta para el equipo."""
    lead_id = lead_data.get("lead_id", "?")
    nombre = lead_data.get("name") or "no capturado"
    producto = lead_data.get("product_interest") or "no detectado"
    score = lead_data.get("score", 0)
    resumen = (lead_data.get("last_text") or "")[:150]
    sender_id = lead_data.get("sender_id", "")
    wa_link = f"wa.me/{sender_id}" if sender_id else "no disponible"

    lines = [
        f"🔥 Lead caliente — Score: {score}/10",
        "",
        f"👤 Nombre: {nombre}",
        f"📦 Producto: {producto}",
        f"💬 Último: \"{resumen}\"",
        f"🔗 {wa_link}",
        f"🆔 {lead_id[:12]}...",
    ]
    return "\n".join(lines)


def send_whatsapp_alert(lead_data: Dict[str, Any]) -> bool:
    """
    Envía una alerta por WhatsApp al equipo si el score supera el umbral
    y el lead no ha sido notificado antes.
    Se ejecuta en un hilo separado para no bloquear.
    """
    lead_id = lead_data.get("lead_id", "")
    score = lead_data.get("score", 0)

    if not lead_id:
        logger.warning("ALERT_SKIP no lead_id")
        return False

    # Evitar duplicados
    if _notified_leads.get(lead_id):
        logger.info("ALERT_SKIP lead=%s ya notificado", lead_id[:12])
        return False

    if score < ALERT_SCORE_THRESHOLD:
        return False

    # Marcar como notificado inmediatamente (evita race conditions)
    _notified_leads[lead_id] = True

    alert_text = _build_alert_text(lead_data)
    logger.info("LEAD_CALIENTE lead=%s score=%d — notificando equipo", lead_id[:12], score)

    def _send():
        try:
            # Intentar usar _meta_send_wa de app.py
            import sys
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
            from backend.app import _meta_send_wa
            ok = _meta_send_wa(TEAM_WHATSAPP_NUMBER, alert_text)
            if ok:
                logger.info("ALERT_SENT lead=%s to=%s", lead_id[:12], TEAM_WHATSAPP_NUMBER)
            else:
                logger.warning("ALERT_SEND_FAILED lead=%s", lead_id[:12])
        except Exception as e:
            logger.error("ALERT_SEND_ERROR lead=%s err=%s", lead_id[:12], e)

    threading.Thread(target=_send, daemon=True).start()
    return True


def reset_notified(lead_id: Optional[str] = None) -> None:
    """Limpia el cache de leads notificados (útil en tests o reinicios)."""
    if lead_id:
        _notified_leads.pop(lead_id, None)
    else:
        _notified_leads.clear()
