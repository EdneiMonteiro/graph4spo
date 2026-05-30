"""
index_documents.py

Pipeline de indexação: SharePoint Online → Azure AI Search

Fluxo:
  1. DefaultAzureCredential (az login) → Azure Key Vault
     - Recupera: openai-api-key, openai-endpoint, search-api-key, search-endpoint
     - Recupera: certificado PFX do Service Principal (sp4spo-cert)
  2. CertificateCredential (Service Principal) → Microsoft Graph API
     - Lista arquivos .txt na biblioteca doc4index do SharePoint
     - Baixa conteúdo de cada arquivo
  3. RecursiveCharacterTextSplitter → chunks (800 tokens, overlap 100)
  4. AzureOpenAIEmbeddings (text-embedding-3-small) → vetores 1536 dims
  5. AzureSearch (LangChain) → cria índice híbrido + carrega chunks

Pré-requisitos:
  - Arquivo .env configurado (gerado pelo script 01_azure_resources.ps1)
  - az login executado na sessão atual
  - Scripts de infra 01-04 executados com sucesso
  - Documentos .txt carregados na biblioteca doc4index do SharePoint
"""

import logging
import os
import sys
from typing import Generator

import requests
from azure.core.credentials import AzureKeyCredential
from azure.identity import CertificateCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import SearchFieldDataType, SimpleField, SearchableField, SearchField
from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import AzureSearch
from langchain_core.documents import Document
from langchain_openai import AzureOpenAIEmbeddings

# Adiciona src/ ao path para import do keyvault_client
sys.path.insert(0, os.path.dirname(__file__))
from keyvault_client import build_kv_client

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CHUNK_SIZE    = 800   # caracteres por chunk
CHUNK_OVERLAP = 100   # sobreposição entre chunks consecutivos
BATCH_SIZE    = 100   # documentos por batch de upload ao AI Search

# Separadores otimizados para prosa em português
PT_SEPARATORS = ["\n\n", "\n", ".", "!", "?", ";", ":", ",", " ", ""]


# ---------------------------------------------------------------------------
# Funções auxiliares — Microsoft Graph / SharePoint
# ---------------------------------------------------------------------------

def get_graph_token(sp_credential: CertificateCredential) -> str:
    """Obtém token de acesso para o Microsoft Graph usando o Service Principal."""
    token = sp_credential.get_token("https://graph.microsoft.com/.default")
    return token.token


def get_site_id(token: str, sharepoint_site_url: str) -> str:
    """
    Resolve o Site ID do SharePoint usando a Graph API.
    Suporta site raiz (https://tenant.sharepoint.com) e subsites.
    """
    url = sharepoint_site_url.rstrip("/")
    # Extrai host e path: https://07gc0.sharepoint.com → host=07gc0.sharepoint.com, path=/
    if "://" in url:
        _, rest = url.split("://", 1)
    else:
        rest = url

    parts = rest.split("/", 1)
    host  = parts[0]
    path  = "/" + parts[1] if len(parts) > 1 else "/"

    if path == "/":
        graph_url = f"https://graph.microsoft.com/v1.0/sites/{host}:/?$select=id,displayName"
    else:
        graph_url = f"https://graph.microsoft.com/v1.0/sites/{host}:{path}?$select=id,displayName"

    resp = requests.get(
        graph_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    log.info("  Site: %s (ID: %s)", data.get("displayName"), data["id"])
    return data["id"]


def find_drive_id(token: str, site_id: str, library_name: str) -> str:
    """Localiza o ID da drive (biblioteca de documentos) pelo nome da biblioteca."""
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives?$select=id,name",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    drives = resp.json().get("value", [])
    drive  = next((d for d in drives if d["name"] == library_name), None)
    if not drive:
        available = [d["name"] for d in drives]
        raise ValueError(
            f"Biblioteca '{library_name}' não encontrada. "
            f"Bibliotecas disponíveis: {available}"
        )
    return drive["id"]


def list_txt_files(token: str, drive_id: str) -> list[dict]:
    """
    Lista todos os arquivos .txt na raiz da biblioteca.
    Retorna lista de dicts com 'name' e '@microsoft.graph.downloadUrl'.
    """
    resp = requests.get(
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children"
        "?$select=id,name,file&$expand=listItem($expand=fields($select=Depto))",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json().get("value", [])
    txt_files = []
    for item in items:
        if "file" in item and item["name"].lower().endswith(".txt"):
            depto = (item.get("listItem") or {}).get("fields", {}).get("Depto", "") or ""
            item["depto"] = depto
            txt_files.append(item)
    return txt_files


def download_text(token: str, download_url: str) -> str:
    """Baixa o conteúdo de texto de um arquivo do SharePoint."""
    resp = requests.get(
        download_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    resp.raise_for_status()
    # Tenta UTF-8 com fallback para latin-1 (comum em documentos legados)
    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        return resp.content.decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# Pipeline de chunking
# ---------------------------------------------------------------------------

def chunk_files(
    files: list[dict],
    token: str,
    drive_id: str = "",
) -> list[Document]:
    """
    Baixa cada arquivo e aplica RecursiveCharacterTextSplitter.

    Parâmetros de chunking:
      chunk_size=800    — aprox. 600-650 tokens para texto em português
      chunk_overlap=100 — 12% de sobreposição para preservar contexto entre chunks
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=PT_SEPARATORS,
        length_function=len,
    )

    all_docs: list[Document] = []
    for file in files:
        log.info("  Baixando: %s", file["name"])
        download_url = (
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{file['id']}/content"
        )
        content = download_text(token, download_url)
        chunks  = splitter.create_documents(
            texts=[content],
            metadatas=[{"source": file["name"], "depto": file.get("depto", "")}],
        )
        log.info("    → %d chunk(s)", len(chunks))
        all_docs.extend(chunks)

    return all_docs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    # --- Variáveis de ambiente (não-secretas) ---
    tenant_id     = os.environ["SHAREPOINT_TENANT_ID"]  # tenant do SharePoint (07gc0)
    client_id     = os.environ.get("SP_CLIENT_ID") or os.environ["AZURE_CLIENT_ID"]
    kv_cert_name  = os.environ["KV_CERT_NAME"]
    spo_site_url  = os.environ["SHAREPOINT_SITE_URL"]
    library_name = os.environ["SHAREPOINT_LIBRARY_NAME"]
    index_name   = os.environ["AZURE_SEARCH_INDEX_NAME"]
    oai_api_ver  = os.environ["AZURE_OPENAI_API_VERSION"]
    embed_deploy = os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"]

    # --- Key Vault: recupera segredos e certificado ---
    log.info("Conectando ao Azure Key Vault...")
    kv = build_kv_client()

    oai_endpoint  = kv.get_secret("openai-endpoint")
    oai_key       = kv.get_secret("openai-api-key")
    srch_endpoint = kv.get_secret("search-endpoint")
    srch_key      = kv.get_secret("search-api-key")

    log.info("Recuperando certificado do Service Principal do Key Vault...")
    cert_bytes = kv.get_sp_cert_bytes(kv_cert_name)

    # --- Service Principal → Microsoft Graph ---
    log.info("Autenticando no Microsoft Graph via CertificateCredential...")
    # CertificateCredential autentica o SP no tenant do SharePoint (07gc0)
    # O cert foi recuperado do Key Vault do tenant Azure (5bda9e44-)
    sp_credential = CertificateCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        certificate_data=cert_bytes,
    )
    token = get_graph_token(sp_credential)

    # --- SharePoint: descobrir site e biblioteca ---
    log.info("Localizando site SharePoint: %s", spo_site_url)
    site_id  = get_site_id(token, spo_site_url)

    log.info("Localizando biblioteca '%s'...", library_name)
    drive_id = find_drive_id(token, site_id, library_name)

    # --- Listar arquivos ---
    log.info("Listando arquivos .txt na biblioteca...")
    files = list_txt_files(token, drive_id)
    log.info("  %d arquivo(s) encontrado(s).", len(files))

    if not files:
        log.warning(
            "Nenhum arquivo .txt encontrado na biblioteca '%s'.\n"
            "Faça upload dos documentos da pasta docs/ para:\n"
            "  %s/%s",
            library_name, spo_site_url, library_name,
        )
        return

    # --- Chunking ---
    log.info("Baixando e segmentando documentos (chunk=%d, overlap=%d)...",
             CHUNK_SIZE, CHUNK_OVERLAP)
    documents = chunk_files(files, token, drive_id)
    log.info("Total de chunks gerados: %d", len(documents))

    # --- Embeddings ---
    log.info("Inicializando Azure OpenAI Embeddings (deployment: %s)...", embed_deploy)
    embeddings = AzureOpenAIEmbeddings(
        azure_endpoint=oai_endpoint,
        api_key=oai_key,
        azure_deployment=embed_deploy,
        openai_api_version=oai_api_ver,
        chunk_size=16,  # número de textos por chamada à API de embedding
    )

    # --- Azure AI Search: recria o índice com campo 'depto' filterable ---
    log.info("Recriando índice '%s' com campo 'depto' filterable...", index_name)
    idx_client = SearchIndexClient(srch_endpoint, AzureKeyCredential(srch_key))
    try:
        idx_client.delete_index(index_name)
        log.info("  Índice anterior removido.")
    except Exception:
        log.info("  Índice não existia, criando novo.")

    # Schema completo: campos padrão do LangChain + campo 'depto' filterable
    index_fields = [
        SimpleField(
            name="id",
            type=SearchFieldDataType.String,
            key=True,
            filterable=True,
        ),
        SearchableField(
            name="content",
            type=SearchFieldDataType.String,
        ),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=1536,
            vector_search_profile_name="myHnswProfile",
        ),
        SearchableField(
            name="metadata",
            type=SearchFieldDataType.String,
        ),
        SimpleField(
            name="depto",
            type=SearchFieldDataType.String,
            filterable=True,
            retrievable=True,
        ),
    ]
    vector_store = AzureSearch(
        azure_search_endpoint=srch_endpoint,
        azure_search_key=srch_key,
        index_name=index_name,
        embedding_function=embeddings,
        search_type="hybrid",
        fields=index_fields,
    )

    # Upload em batches para respeitar limites do Free SKU (50 MB, 3 índices)
    total_batches = -(-len(documents) // BATCH_SIZE)  # ceil division
    log.info("Carregando %d chunks em %d batch(es) de até %d...",
             len(documents), total_batches, BATCH_SIZE)

    for i in range(0, len(documents), BATCH_SIZE):
        batch = documents[i : i + BATCH_SIZE]
        vector_store.add_documents(batch)
        log.info("  Batch %d/%d concluído.", i // BATCH_SIZE + 1, total_batches)

    log.info("✅ Indexação concluída! %d chunks no índice '%s'.", len(documents), index_name)
    log.info("   Execute python src/search_query.py para testar.")


if __name__ == "__main__":
    main()
