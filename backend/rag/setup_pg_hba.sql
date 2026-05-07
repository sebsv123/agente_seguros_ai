-- ============================================================
-- setup_pg_hba.sql — Usuario de ingesta y permisos
-- Se ejecuta automáticamente al iniciar el contenedor db
--
-- La contraseña del usuario agente_ingest se define en .env
-- como POSTGRES_PASSWORD. Ambas contraseñas deben coincidir.
-- ============================================================

-- 1. Crear usuario de ingesta con la misma contraseña que agente
--    La contraseña se hereda de POSTGRES_PASSWORD del .env
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agente_ingest') THEN
        CREATE ROLE agente_ingest WITH LOGIN PASSWORD 'cambia_esto_en_produccion';
        RAISE NOTICE 'Usuario agente_ingest creado. CAMBIA LA CONTRASEÑA con: ALTER ROLE agente_ingest PASSWORD ''nueva_password'';';
    END IF;
END
$$;

-- 2. Permisos para usuario ingest (solo INSERT/SELECT/UPDATE en document_chunks)
GRANT USAGE ON SCHEMA public TO agente_ingest;
GRANT INSERT, SELECT, UPDATE ON document_chunks TO agente_ingest;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO agente_ingest;

-- 3. El usuario 'agente' (backend) solo necesita SELECT
GRANT SELECT ON document_chunks TO agente;

-- ============================================================
-- CONFIGURACIÓN MANUAL (ejecutar UNA VEZ después del primer inicio)
-- ============================================================
--
-- 1. Cambiar la contraseña del usuario agente_ingest:
--    docker compose exec db psql -U agente -c "ALTER ROLE agente_ingest PASSWORD '${POSTGRES_PASSWORD}';"
--
-- 2. Permitir conexiones remotas en pg_hba.conf:
--    docker compose exec db bash
--    echo "host all agente_ingest 0.0.0.0/0 md5" >> /var/lib/postgresql/data/pg_hba.conf
--    echo "host all agente_ingest ::/0 md5" >> /var/lib/postgresql/data/pg_hba.conf
--    psql -U agente -c "SELECT pg_reload_conf();"
--    exit
--
-- 3. Configurar firewall en Linux (solo IP de Windows):
--    sudo ufw allow from 192.168.1.X to any port 5432 proto tcp
--    donde 192.168.1.X es la IP privada del portátil Windows
-- ============================================================
