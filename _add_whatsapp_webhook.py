#!/usr/bin/env python3
"""
Añade soporte para WhatsApp Cloud API a backend/app.py y al workflow n8n.

Cambios:
1. app.py: Endpoints GET/POST /wa/inbound + función de envío WhatsApp
2. n8n workflow: Code node actualizado para parsear WhatsApp Cloud API
3. .env.example: Nuevas variables WA_PHONE_NUMBER_ID y META_WA_TOKEN
"""

import json
import os

# ============================================================
# 1. MODIFICAR app.py
# ============================================================

APP_PY = 'backend/app.py'

with open(APP_PY, 'r', encoding='utf-8') as f:
    content = f.read()

# --- 1a. Añadir WA_PHONE_NUMBER_ID y META_WA_TOKEN a la sección CONFIG ---
# Buscar la línea de WA_TOKEN y añadir después
old_config = 'WA_TOKEN                = os.getenv("WA_TOKEN", "")'
new_config = '''WA_TOKEN                = os.getenv("WA_TOKEN", "")
WA_PHONE_NUMBER_ID      = os.getenv("WA_PHONE_NUMBER_ID", "")
META_WA_TOKEN           = os.getenv("META_WA_TOKEN", "")'''

if old_config in content:
    content = content.replace(old_config, new_config)
    print("[OK] Añadidas WA_PHONE_NUMBER_ID y META_WA_TOKEN a CONFIG")
else:
    print("[WARN] No se encontró WA_TOKEN en CONFIG")

# --- 1b. Añadir función _meta_send_wa después de _meta_send_ig_dm ---
old_send_ig = '''def send_with_lag_sync(recipient_id: str, message: str) -> None:
    time.sleep(random.uniform(LAG_MIN_S, LAG_MAX_S))
    _meta_send_ig_dm(recipient_id, message)'''

new_send_wa = '''def _meta_send_wa(recipient_id: str, message: str) -> bool:
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
    time.sleep(random.uniform(LAG_MIN_S, LAG_MAX_S))
    _meta_send_ig_dm(recipient_id, message)'''

if old_send_ig in content:
    content = content.replace(old_send_ig, new_send_wa)
    print("[OK] Añadida función _meta_send_wa")
else:
    print("[WARN] No se encontró send_with_lag_sync")

# --- 1c. Añadir endpoints GET/POST /wa/inbound después de /meta/webhook ---
old_meta_post = '''@app.post("/meta/webhook")
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
        cached = _dedup_get(lead_id, text)
        if cached:
            if not _meta_sender_is_blocked(sender_id):
                background_tasks.add_task(send_with_lag_sync, sender_id, cached)
            continue

        reply = handle_incoming(sender_id, text)
        _dedup_set(lead_id, text, reply)
        if not _meta_sender_is_blocked(sender_id):
            background_tasks.add_task(send_with_lag_sync, sender_id, reply)

    return {"ok": True, "processed": len(messages)}'''

new_wa_endpoints = '''@app.post("/meta/webhook")
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
        cached = _dedup_get(lead_id, text)
        if cached:
            if not _meta_sender_is_blocked(sender_id):
                background_tasks.add_task(_meta_send_wa, sender_id, cached)
            continue

        reply = handle_incoming(sender_id, text)
        _dedup_set(lead_id, text, reply)
        if not _meta_sender_is_blocked(sender_id):
            background_tasks.add_task(_meta_send_wa, sender_id, reply)

    return {"ok": True, "processed": len(messages)}'''

if old_meta_post in content:
    content = content.replace(old_meta_post, new_wa_endpoints)
    print("[OK] Añadidos endpoints GET/POST /wa/inbound")
else:
    print("[WARN] No se encontró meta_webhook_receive - buscando alternativa...")
    # Fallback: buscar solo el final de la función
    if 'return {"ok": True, "processed": len(messages)}' in content:
        print("[INFO] Endpoint /meta/webhook ya podría estar modificado")

with open(APP_PY, 'w', encoding='utf-8') as f:
    f.write(content)
print(f"[OK] {APP_PY} actualizado")


# ============================================================
# 2. MODIFICAR WORKFLOW n8n (Code node + Webhook)
# ============================================================

WF_V2 = 'recovered_workflows/agente_workflows_v2.json'

with open(WF_V2, 'r', encoding='utf-8') as f:
    wf = json.load(f)

# --- 2a. Actualizar el Code node para parsear WhatsApp Cloud API ---
new_code_js = """// n8n Code node
// Normaliza payloads (Instagram DM + WhatsApp Cloud API) y evita loops.

const body = $json.body ?? $json;
const out = [];

function emit({ lead_id, sender_id, text, source, ig_user_id, contact_name, raw }) {
  const id = String(lead_id ?? sender_id ?? "").trim();
  const t = String(text ?? "").trim();
  if (!id || !t) return;

  out.push({
    json: {
      lead_id: id,
      text: t,
      source: source ?? "instagram_dm",
      ig_user_id: ig_user_id ?? null,
      contact_name: contact_name ?? null,
      raw: raw ?? body,
    },
  });
}

/** 1) Payload simple tipo:
 * { lead_id, text, source } o { sender_id, text }
 */
if ((body.lead_id || body.sender_id) && body.text) {
  emit({
    lead_id: body.lead_id,
    sender_id: body.sender_id,
    text: body.text,
    source: body.source,
    ig_user_id: body.ig_user_id,
    raw: body,
  });
  return out;
}

/** 2) Formato Instagram DM: entry.messaging[] */
for (const entry of body.entry || []) {
  for (const ev of entry.messaging || []) {
    if (ev.read || ev.delivery) continue;
    const msg = ev.message || {};
    if (msg.is_echo || ev.is_echo) continue;

    emit({
      sender_id: ev.sender?.id,
      text: msg.text,
      source: "instagram_dm",
      ig_user_id: ev.sender?.id ?? null,
      raw: ev,
    });
  }

  /** 3) Formato WhatsApp Cloud API: entry.changes[].value.messages[] */
  for (const ch of entry.changes || []) {
    if (ch.field !== "messages") continue;
    const v = ch.value || {};
    
    // Obtener nombre del contacto si está disponible
    const contacts = v.contacts || [];
    const contactName = contacts.length > 0 ? (contacts[0].profile?.name || null) : null;
    
    // Instagram: v.messages[] con from.id
    // WhatsApp: v.messages[] con from (string E.164)
    for (const m of v.messages || []) {
      if (m.is_echo) continue;
      
      const msgFrom = m.from?.id || m.from || null;
      let msgText = "";
      
      if (m.text?.body) {
        msgText = m.text.body;
      } else if (m.type === "interactive" && m.interactive?.button_reply?.title) {
        msgText = m.interactive.button_reply.title;
      }
      
      if (msgFrom && msgText) {
        // Detectar si es WhatsApp (formato E.164) o Instagram (numeric ID)
        const isWhatsApp = /^\\d{10,15}$/.test(String(msgFrom).replace(/[^0-9]/g, ""));
        emit({
          sender_id: msgFrom,
          text: msgText,
          source: isWhatsApp ? "whatsapp" : "instagram_dm",
          ig_user_id: isWhatsApp ? null : msgFrom,
          contact_name: isWhatsApp ? contactName : null,
          raw: m,
        });
      }
    }
    
    // Instagram legacy: v.sender?.id, v.message?.text
    if (v.sender?.id && v.message?.text && !v.message?.is_echo) {
      emit({
        sender_id: v.sender.id,
        text: v.message.text,
        source: "instagram_dm",
        ig_user_id: v.sender.id,
        raw: ch,
      });
    }
  }
}

return out;"""

for node in wf['nodes']:
    if node.get('type') == 'n8n-nodes-base.code' and 'Code' in node.get('name', ''):
        node['parameters']['jsCode'] = new_code_js
        print(f"[OK] Code node '{node['name']}' actualizado con parser WhatsApp Cloud API")
        break

# --- 2b. Añadir segundo Webhook para WhatsApp (path: wa-inbound) ---
# Clonar el webhook existente y cambiar path
webhook_node = None
for node in wf['nodes']:
    if node.get('type') == 'n8n-nodes-base.webhook':
        webhook_node = node
        break

if webhook_node:
    wa_webhook = json.loads(json.dumps(webhook_node))  # deep copy
    wa_webhook['parameters']['path'] = 'wa-inbound'
    wa_webhook['id'] = 'wa_' + webhook_node['id']
    wa_webhook['name'] = 'Webhook WhatsApp'
    wa_webhook['webhookId'] = 'wa_' + (webhook_node.get('webhookId', ''))
    # Position: below the existing webhook
    wa_webhook['position'] = [webhook_node['position'][0], webhook_node['position'][1] + 200]
    wf['nodes'].append(wa_webhook)
    print(f"[OK] Webhook WhatsApp añadido (path: wa-inbound)")

# --- 2c. Actualizar HTTP Request para enviar source correcto ---
for node in wf['nodes']:
    if node.get('type') == 'n8n-nodes-base.httpRequest':
        params = node.get('parameters', {})
        json_body = params.get('jsonBody', '')
        if '"source": "instagram_dm"' in json_body:
            # Update to pass source dynamically
            new_body = json_body.replace(
                '"source": "instagram_dm"',
                '"source": "={{$json.source}}"'
            )
            params['jsonBody'] = new_body
            print(f"[OK] HTTP Request '{node['name']}' actualizado: source dinámico")
            break

with open(WF_V2, 'w', encoding='utf-8') as f:
    json.dump(wf, f, ensure_ascii=False, indent=2)
print(f"[OK] {WF_V2} actualizado")


# ============================================================
# 3. ACTUALIZAR .env.example
# ============================================================

ENV_EXAMPLE = '.env.example'

with open(ENV_EXAMPLE, 'r', encoding='utf-8') as f:
    env_content = f.read()

wa_vars = """
# ====== WhatsApp Cloud API ======
WA_PHONE_NUMBER_ID=  # ID del número de teléfono en Meta Business
META_WA_TOKEN=       # Token de acceso de WhatsApp Cloud API
"""

if 'WA_PHONE_NUMBER_ID' not in env_content:
    # Añadir después de la sección Meta
    if '# ====== Meta / Instagram ======' in env_content:
        env_content = env_content.replace(
            '# ====== Meta / Instagram ======',
            '# ====== WhatsApp Cloud API ======\nWA_PHONE_NUMBER_ID=\nMETA_WA_TOKEN=\n\n# ====== Meta / Instagram ======'
        )
        print("[OK] .env.example actualizado con WA_PHONE_NUMBER_ID y META_WA_TOKEN")
    else:
        env_content += wa_vars
        print("[OK] .env.example actualizado (append)")

with open(ENV_EXAMPLE, 'w', encoding='utf-8') as f:
    f.write(env_content)

print("\n=== TODOS LOS CAMBIOS COMPLETADOS ===")
print("1. app.py: Endpoints GET/POST /wa/inbound + _meta_send_wa")
print("2. n8n workflow: Code node + Webhook WhatsApp + source dinámico")
print("3. .env.example: WA_PHONE_NUMBER_ID y META_WA_TOKEN")
