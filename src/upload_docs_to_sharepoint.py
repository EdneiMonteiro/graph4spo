"""
upload_docs_to_sharepoint.py

Script auxiliar: faz upload dos documentos .txt da pasta docs/ para a
biblioteca doc4index no SharePoint Online via Microsoft Graph API.

Execute APÓS o script infra/04_grant_sharepoint.ps1.

Uso:
    python src/upload_docs_to_sharepoint.py
"""

import logging
import os
import sys
from pathlib import Path

import requests
from azure.identity import CertificateCredential
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from keyvault_client import build_kv_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).parent.parent / "docs"


def get_graph_token(sp_credential: CertificateCredential) -> str:
    return sp_credential.get_token("https://graph.microsoft.com/.default").token


def get_site_id(token: str, site_url: str) -> str:
    url = site_url.rstrip("/")
    host = url.split("://")[-1].split("/")[0]
    graph_url = f"https://graph.microsoft.com/v1.0/sites/{host}:/?$select=id"
    resp = requests.get(graph_url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()
    return resp.json()["id"]


def find_drive_id(token: str, site_id: str, library_name: str) -> str:
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives?$select=id,name",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    drives = resp.json().get("value", [])
    drive  = next((d for d in drives if d["name"] == library_name), None)
    if not drive:
        raise ValueError(f"Biblioteca '{library_name}' não encontrada.")
    return drive["id"]


def upload_file(token: str, drive_id: str, file_path: Path) -> None:
    """Faz upload de um arquivo para a raiz da biblioteca via Graph API (upload simples)."""
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}"
        f"/root:/{file_path.name}:/content"
    )
    content = file_path.read_bytes()
    resp = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
        data=content,
        timeout=60,
    )
    resp.raise_for_status()
    log.info("  ✅ %s → SharePoint", file_path.name)


def main() -> None:
    load_dotenv()

    tenant_id    = os.environ["SHAREPOINT_TENANT_ID"]  # tenant do SharePoint (07gc0)
    client_id    = os.environ["AZURE_CLIENT_ID"]
    kv_cert_name = os.environ["KV_CERT_NAME"]
    spo_site_url = os.environ["SHAREPOINT_SITE_URL"]
    library_name = os.environ["SHAREPOINT_LIBRARY_NAME"]

    log.info("Conectando ao Azure Key Vault...")
    kv         = build_kv_client()
    cert_bytes = kv.get_sp_cert_bytes(kv_cert_name)

    log.info("Autenticando no Microsoft Graph...")
    sp_cred = CertificateCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        certificate_data=cert_bytes,
    )
    token = get_graph_token(sp_cred)

    log.info("Localizando site SharePoint: %s", spo_site_url)
    site_id  = get_site_id(token, spo_site_url)
    drive_id = find_drive_id(token, site_id, library_name)

    txt_files = sorted(DOCS_DIR.glob("*.txt"))
    log.info("Fazendo upload de %d arquivo(s) para '%s'...", len(txt_files), library_name)

    for f in txt_files:
        upload_file(token, drive_id, f)

    log.info("✅ Upload concluído! %d arquivo(s) enviados.", len(txt_files))
    log.info("   Execute python src/index_documents.py para indexar.")


if __name__ == "__main__":
    main()
