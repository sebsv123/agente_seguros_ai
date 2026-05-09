"""
notifier.py
Envía alertas de WhatsApp al equipo cuando un lead alcanza score >= umbral.
"""
import logging
import os
import threading

logger = logging.getLogger("rosa")

TEAM_WHATSAPP_NUMBER = os.getenv("TEAM_WHATSAPP_NUMBER", "34603448765")
ALERT_SCORE_THRESHOLD = int(os.getenv("ALERT_SCORE_THRESHOLD", "8"))

_ALREADY_NOTIFIED: set = set()  # Evita duplicados por lead_id

def send_whatsapp_alert(lead_data: dict) -> None:
    """
    Envía alerta al equipo si score >= umbral.
    Se ejecuta en background para no bloquear la respuesta al cliente.
    """
    score = lead_data.get("score", 0)
    lead_id = lead_data.get("lead_id", "")

    if score < ALERT_SCORE_THRESHOLD:
        return
    if lead_id in _ALREADY_NOTIFIED:
        return

    def _fire():
        try:
            from backend.app import _meta_send_wa, DEFAULT_WA_PHONE_E164
            nombre = lead_data.get("name") or "no indicado"
            producto = lead_data.get("product_interest") or "no detectado"
            sender_id = lead_data.get("sender_id") or ""
            ultimo_texto = lead_data.get("last_text") or ""

            mensaje = (
                f"🔥 LEAD CALIENTE — Score: {score}/10\n\n"
                f"👤 Nombre: {nombre}\n"
                f"📦 Producto: {producto}\n"
                f"💬 Último mensaje: \"{ultimo_texto[:100]}\"\n\n"
                f"👉 Responder: wa.me/{sender_id}"
            )
            _meta_send_wa(TEAM_WHATSAPP_NUMBER, mensaje)
            _ALREADY_NOTIFIED.add(lead_id)
            logger.info("ALERT_SENT lead=%s score=%s", lead_id[:12], score)
        except Exception as e:
            logger.warning("ALERT_FIRE_ERROR lead=%s err=%s", lead_id, e)

    threading.Thread(target=_fire, daemon=True).start()
