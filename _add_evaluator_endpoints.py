#!/usr/bin/env python3
"""
Añade a backend/app.py:
1. Endpoints GET /insights y POST /insights/{id}/apply
2. APScheduler para resumen semanal (lunes 9:00)
3. Llamada a evaluate_conversation cuando un lead se cierra/deriva
"""

APP_PY = 'backend/app.py'

with open(APP_PY, 'r', encoding='utf-8') as f:
    content = f.read()

# ============================================================
# 1. AÑADIR ENDPOINTS DE INSIGHTS
# ============================================================

# Buscar el endpoint de dashboard leads
old_dashboard_leads = '''@app.get("/api/leads")
def leads_list'''

new_insights_endpoints = '''@app.get("/api/insights")
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
def leads_list'''

if old_dashboard_leads in content:
    content = content.replace(old_dashboard_leads, new_insights_endpoints)
    print("[OK] Endpoints GET /api/insights y POST /api/insights/{id}/apply añadidos")
else:
    print("[WARN] No se encontró leads_list")


# ============================================================
# 2. AÑADIR APSCHEDULER AL STARTUP
# ============================================================

old_startup = '''@app.on_event("startup")
async def on_startup():
    try:
        bootstrap_schema()
    except Exception as e:
        logger.error("BOOTSTRAP_ERROR %s", e)
    # Cargar playbooks al iniciar (no bloquea si falla)
    _load_playbooks()'''

new_startup = '''@app.on_event("startup")
async def on_startup():
    try:
        bootstrap_schema()
    except Exception as e:
        logger.error("BOOTSTRAP_ERROR %s", e)
    # Cargar playbooks al iniciar (no bloquea si falla)
    _load_playbooks()
    
    # Iniciar APScheduler para resumen semanal (lunes 9:00)
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from backend.agent_evaluator import generate_weekly_summary
        
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(
            generate_weekly_summary,
            trigger='cron',
            day_of_week='mon',
            hour=9,
            minute=0,
            id='weekly_summary',
            replace_existing=True,
        )
        _scheduler.start()
        logger.info("SCHEDULER_STARTED weekly_summary: lunes 9:00")
    except Exception as e:
        logger.warning("SCHEDULER_NOT_STARTED %s", e)'''

if old_startup in content:
    content = content.replace(old_startup, new_startup)
    print("[OK] APScheduler añadido al startup (resumen semanal lunes 9:00)")
else:
    print("[WARN] No se encontró on_startup")


# ============================================================
# 3. AÑADIR EVALUACIÓN AL CIERRE/DERIVACIÓN
# ============================================================

# Añadir llamada a evaluate_conversation cuando se avanza a listo_para_humano o cerrado
# Buscar en advance_lead_phase o en update_lead_state_from_message
old_advance_cerrado = '''    if any(p in text_lower for p in ["no me interesa", "no gracias", "no quiero", "déjalo", "no, gracias"]):
        ls = advance_lead_phase(lead_id, ls, "cerrado", f"Lead desistió: {text[:50]}")'''

new_advance_cerrado = '''    if any(p in text_lower for p in ["no me interesa", "no gracias", "no quiero", "déjalo", "no, gracias"]):
        ls = advance_lead_phase(lead_id, ls, "cerrado", f"Lead desistió: {text[:50]}")
        # Evaluar conversación al cerrar
        try:
            from backend.agent_evaluator import evaluate_conversation
            evaluate_conversation(lead_id)
        except Exception as e:
            logger.warning("EVALUATE_ERR lead=%s %s", lead_id, e)'''

if old_advance_cerrado in content:
    content = content.replace(old_advance_cerrado, new_advance_cerrado)
    print("[OK] evaluate_conversation añadido al cerrar lead")
else:
    print("[WARN] No se encontró advance_lead_phase cerrado")

# También añadir evaluación cuando se deriva a humano
old_advance_humano = '''    if ls["fase"] == "datos_minimos":
        if _check_datos_minimos_completos(ls) and not ls["derivado_a_humano"]:
            ls = advance_lead_phase(lead_id, ls, "listo_para_humano", "Datos mínimos completos")
            ls["derivado_a_humano"] = True
            _notify_human_handoff(lead_id, ls, text, sender_id)'''

new_advance_humano = '''    if ls["fase"] == "datos_minimos":
        if _check_datos_minimos_completos(ls) and not ls["derivado_a_humano"]:
            ls = advance_lead_phase(lead_id, ls, "listo_para_humano", "Datos mínimos completos")
            ls["derivado_a_humano"] = True
            _notify_human_handoff(lead_id, ls, text, sender_id)
            # Evaluar conversación al derivar
            try:
                from backend.agent_evaluator import evaluate_conversation
                evaluate_conversation(lead_id)
            except Exception as e:
                logger.warning("EVALUATE_ERR lead=%s %s", lead_id, e)'''

if old_advance_humano in content:
    content = content.replace(old_advance_humano, new_advance_humano)
    print("[OK] evaluate_conversation añadido al derivar a humano")
else:
    print("[WARN] No se encontró advance_lead_phase humano")


# ============================================================
# GUARDAR
# ============================================================

with open(APP_PY, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\n[OK] {APP_PY} actualizado")
print("Cambios:")
print("  1. GET /api/insights?token=XXX - lista insights")
print("  2. POST /api/insights/{id}/apply?token=XXX - aplicar insight")
print("  3. APScheduler en startup (resumen semanal lunes 9:00)")
print("  4. evaluate_conversation() al cerrar o derivar lead")
