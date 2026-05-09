"""
reset_dev_db.py
Borra y recrea el schema completo en entorno de desarrollo.
NUNCA ejecutar en producción.
"""
import os
import sys

ENV = os.getenv("APP_ENV", "development")
if ENV == "production":
    print("❌ PROHIBIDO en producción. Saliendo.")
    sys.exit(1)

print("⚠️  Este script borra TODAS las tablas. Escribe 'CONFIRMAR' para continuar:")
confirm = input().strip()
if confirm != "CONFIRMAR":
    print("Cancelado.")
    sys.exit(0)

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from backend.app import db_connect, bootstrap_schema

TABLES = [
    "conversations", "conversation_state",
    "lead_profile", "lead_state", "leads",
    "kb_documents", "place_cache",
]

with db_connect() as conn:
    with conn.cursor() as cur:
        for table in TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            print(f"  ✓ DROP {table}")
    conn.commit()

print("\nRecreando schema...")
bootstrap_schema()
print("✅ Schema recreado limpio.")
