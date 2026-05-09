"""
oci_storage.py
Gestión de PDFs en OCI Object Storage.
Permite subir/descargar PDFs desde cualquier portátil sin SSH.
Requiere: oci (pip install oci) — opcional, con fallback si no está instalado.
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("rosa")

# Config desde variables de entorno
OCI_NAMESPACE       = os.getenv("OCI_NAMESPACE", "")
OCI_BUCKET_NAME     = os.getenv("OCI_BUCKET_NAME", "agente-rosa-kb")
OCI_REGION          = os.getenv("OCI_REGION", "eu-madrid-1")
OCI_CONFIG_FILE     = os.getenv("OCI_CONFIG_FILE", "~/.oci/config")
OCI_CONFIG_PROFILE  = os.getenv("OCI_CONFIG_PROFILE", "DEFAULT")
OCI_USE_INSTANCE_PRINCIPAL = os.getenv("OCI_USE_INSTANCE_PRINCIPAL", "1") not in {"0", "false", "no"}

_HAS_OCI = False
_oci_client = None

def _get_oci_client():
    """Inicializa cliente OCI (Instance Principal en Oracle VM, config file en local)."""
    global _HAS_OCI, _oci_client
    if _oci_client is not None:
        return _oci_client
    try:
        import oci
        _HAS_OCI = True
        if OCI_USE_INSTANCE_PRINCIPAL:
            try:
                signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
                _oci_client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
                logger.info("OCI client listo (Instance Principal)")
                return _oci_client
            except Exception as e:
                logger.warning("OCI Instance Principal failed, usando config file: %s", e)
        config = oci.config.from_file(OCI_CONFIG_FILE, OCI_CONFIG_PROFILE)
        _oci_client = oci.object_storage.ObjectStorageClient(config)
        logger.info("OCI client listo (config file, region=%s)", config.get("region"))
        return _oci_client
    except ImportError:
        logger.warning("OCI SDK no instalado — Object Storage no disponible")
        return None
    except Exception as e:
        logger.warning("OCI client init failed: %s", e)
        return None


def list_bucket_objects(prefix: str = "") -> list[dict]:
    """
    Lista objetos en el bucket OCI.
    Retorna lista de dicts con: name, size, time_modified.
    """
    client = _get_oci_client()
    if not client or not OCI_NAMESPACE:
        return []
    try:
        import oci
        response = client.list_objects(
            OCI_NAMESPACE, OCI_BUCKET_NAME,
            prefix=prefix,
            fields="name,size,timeModified"
        )
        return [
            {
                "name": obj.name,
                "size": obj.size,
                "modified": str(obj.time_modified),
            }
            for obj in response.data.objects
        ]
    except Exception as e:
        logger.error("OCI list_objects error: %s", e)
        return []


def download_pdf(object_name: str, local_path: str) -> bool:
    """
    Descarga un PDF del bucket OCI a local_path.
    Retorna True si éxito, False si error.
    """
    client = _get_oci_client()
    if not client or not OCI_NAMESPACE:
        return False
    try:
        import oci
        response = client.get_object(OCI_NAMESPACE, OCI_BUCKET_NAME, object_name)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "wb") as f:
            for chunk in response.data.raw.stream(1024 * 1024, decode_content=False):
                f.write(chunk)
        logger.info("OCI download OK: %s → %s", object_name, local_path)
        return True
    except Exception as e:
        logger.error("OCI download error %s: %s", object_name, e)
        return False


def upload_pdf(local_path: str, object_name: str) -> bool:
    """
    Sube un PDF local al bucket OCI.
    object_name debe incluir la subcarpeta: 'salud/poliza.pdf'
    """
    client = _get_oci_client()
    if not client or not OCI_NAMESPACE:
        logger.warning("OCI no configurado — upload omitido")
        return False
    try:
        import oci
        with open(local_path, "rb") as f:
            client.put_object(
                OCI_NAMESPACE, OCI_BUCKET_NAME,
                object_name, f,
                content_type="application/pdf"
            )
        logger.info("OCI upload OK: %s → %s/%s", local_path, OCI_BUCKET_NAME, object_name)
        return True
    except Exception as e:
        logger.error("OCI upload error %s: %s", local_path, e)
        return False


def sync_bucket_to_local(local_data_dir: str = "./data") -> dict:
    """
    Descarga todos los PDFs del bucket OCI a la estructura local data/.
    Solo descarga los que no existen o han cambiado.
    Retorna: {"downloaded": int, "skipped": int, "errors": int}
    """
    objects = list_bucket_objects()
    if not objects:
        logger.warning("OCI sync: bucket vacío o sin acceso")
        return {"downloaded": 0, "skipped": 0, "errors": 0}

    downloaded = skipped = errors = 0
    for obj in objects:
        if not obj["name"].endswith(".pdf"):
            continue
        local_path = os.path.join(local_data_dir, obj["name"])
        # Saltar si ya existe y tiene el mismo tamaño
        if os.path.exists(local_path) and os.path.getsize(local_path) == obj.get("size", -1):
            skipped += 1
            continue
        if download_pdf(obj["name"], local_path):
            downloaded += 1
        else:
            errors += 1

    logger.info("OCI sync: downloaded=%d skipped=%d errors=%d", downloaded, skipped, errors)
    return {"downloaded": downloaded, "skipped": skipped, "errors": errors}


def sync_local_to_bucket(local_data_dir: str = "./data") -> dict:
    """
    Sube todos los PDFs locales al bucket OCI.
    Mantiene la estructura de subcarpetas: data/salud/x.pdf → salud/x.pdf
    Solo sube los que no existen en el bucket.
    """
    existing = {obj["name"] for obj in list_bucket_objects()}
    uploaded = skipped = errors = 0

    for pdf_path in Path(local_data_dir).rglob("*.pdf"):
        rel = pdf_path.relative_to(local_data_dir)
        object_name = str(rel).replace("\\", "/")
        if object_name in existing:
            skipped += 1
            continue
        if upload_pdf(str(pdf_path), object_name):
            uploaded += 1
        else:
            errors += 1

    logger.info("OCI upload: uploaded=%d skipped=%d errors=%d", uploaded, skipped, errors)
    return {"uploaded": uploaded, "skipped": skipped, "errors": errors}
