#!/usr/bin/env python3
"""
check_connection.py — Verifica conexión a PostgreSQL/pgvector.

Uso:
    # En Windows (conectando a Linux):
    python backend/rag/check_connection.py --db-dsn "postgresql://agente_ingest:ingest_pw_2026@LINUX_IP:5432/agente_ai"

    # En Linux (local):
    python backend/rag/check_connection.py

    # Solo verificar conexión:
    python backend/rag/check_connection.py --ping
"""

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Orden de prioridad: INGEST_DB_DSN > DB_DSN > default local
DEFAULT_DSN = (
    os.getenv("INGEST_DB_DSN") or
    os.getenv("DB_DSN") or
    "postgresql://agente:agente_pw@localhost:5433/agente_ai"
)


def check_ping(dsn: str) -> bool:
    """Verifica que PostgreSQL responde."""
    try:
        import psycopg
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return True
    except Exception as e:
        print(f"  ❌ Connection failed: {e}")
        return False


def check_pgvector(dsn: str) -> bool:
    """Verifica que la extensión pgvector está instalada."""
    try:
        import psycopg
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT extname, extversion FROM pg_extension WHERE extname = 'vector'")
                row = cur.fetchone()
                if row:
                    print(f"  ✅ pgvector {row[1]} installed")
                    return True
                else:
                    print("  ❌ pgvector NOT installed")
                    return False
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False


def check_table(dsn: str) -> bool:
    """Verifica que la tabla document_chunks existe y tiene datos."""
    try:
        import psycopg
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM document_chunks")
                count = cur.fetchone()[0]
                print(f"  ✅ document_chunks table: {count} rows")

                if count > 0:
                    cur.execute("SELECT COUNT(DISTINCT category) FROM document_chunks")
                    cats = cur.fetchone()[0]
                    cur.execute("""
                        SELECT category, COUNT(*) as cnt
                        FROM document_chunks GROUP BY category ORDER BY cnt DESC
                    """)
                    print(f"  📊 Categories ({cats}):")
                    for cat, cnt in cur.fetchall():
                        print(f"      - {cat}: {cnt} chunks")
                return True
    except Exception as e:
        print(f"  ❌ Table check failed: {e}")
        return False


def check_permissions(dsn: str) -> bool:
    """Verifica permisos del usuario actual."""
    try:
        import psycopg
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_user")
                user = cur.fetchone()[0]
                print(f"  👤 Connected as: {user}")

                # Probar INSERT (rollback después)
                cur.execute("BEGIN")
                cur.execute(
                    "INSERT INTO document_chunks (document_id, source_file, category, chunk_id, chunk_text, chunk_order) "
                    "VALUES ('test', 'test.txt', 'test', 'test__0000__00000000', 'test', 0)"
                )
                cur.execute("ROLLBACK")
                print(f"  ✅ INSERT permission: OK")
                return True
    except Exception as e:
        print(f"  ❌ Permission check failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Check PostgreSQL/pgvector connection")
    parser.add_argument("--db-dsn", default=DEFAULT_DSN, help="PostgreSQL DSN")
    parser.add_argument("--ping", action="store_true", help="Only ping")
    parser.add_argument("--full", action="store_true", help="Full diagnostic")
    args = parser.parse_args()

    dsn = args.db_dsn
    print(f"\n🔌 Checking connection to PostgreSQL...")
    print(f"   DSN: {dsn.replace(dsn.split('@')[0] if '@' in dsn else '', '***@') if '@' in dsn else dsn}")
    print()

    if args.ping:
        ok = check_ping(dsn)
        print(f"\n{'✅ OK' if ok else '❌ FAILED'}")
        return 0 if ok else 1

    # Full check
    checks = [
        ("Ping PostgreSQL", check_ping(dsn)),
    ]

    if checks[0][1]:
        checks.append(("pgvector extension", check_pgvector(dsn)))
        checks.append(("document_chunks table", check_table(dsn)))
        checks.append(("User permissions", check_permissions(dsn)))

    print()
    all_ok = all(c[1] for c in checks)
    if all_ok:
        print("✅ ALL CHECKS PASSED — System ready")
    else:
        print("❌ SOME CHECKS FAILED — Review above")
        for name, ok in checks:
            print(f"   {'✅' if ok else '❌'} {name}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
