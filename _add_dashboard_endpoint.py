#!/usr/bin/env python3
"""
Añade a backend/app.py:
1. GET /leads endpoint (protegido con KB_ADMIN_TOKEN)
2. Sirve dashboard/index.html en /dashboard
"""

APP_PY = 'backend/app.py'

with open(APP_PY, 'r', encoding='utf-8') as f:
    content = f.read()

# ============================================================
# 1. AÑADIR ENDPOINT GET /leads
# ============================================================

# Buscar el último endpoint antes de /rag/ask
old_rag = '''@app.post("/rag/ask")
def rag_ask(q: RagQuery):'''

new_leads_endpoint = '''# -------- Dashboard: listar leads del día --------

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
def rag_ask(q: RagQuery):'''

if old_rag in content:
    content = content.replace(old_rag, new_leads_endpoint)
    print("[OK] Endpoints GET /api/leads y GET /dashboard añadidos")
else:
    print("[WARN] No se encontró rag_ask endpoint")


# ============================================================
# 2. AÑADIR HTMLResponse A LOS IMPORTS
# ============================================================

old_imports = '''from fastapi import BackgroundTasks, FastAPI, HTTPException, Request as FastAPIRequest
from fastapi.responses import PlainTextResponse'''

new_imports = '''from fastapi import BackgroundTasks, FastAPI, HTTPException, Request as FastAPIRequest
from fastapi.responses import HTMLResponse, PlainTextResponse'''

if old_imports in content:
    content = content.replace(old_imports, new_imports)
    print("[OK] HTMLResponse añadido a imports")
else:
    print("[WARN] No se encontró import de PlainTextResponse")


# ============================================================
# GUARDAR
# ============================================================

with open(APP_PY, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\n[OK] {APP_PY} actualizado")
print("Cambios:")
print("  1. GET /api/leads?token=XXX - lista leads del día con lead_state")
print("  2. GET /dashboard?token=XXX - sirve dashboard/index.html")
print("  3. HTMLResponse añadido a imports")
