# ──────────────────────────────────────────────────────────────────────────────
# graph4spo Indexer — Azure Container Apps
#
# Autenticação:
#   Key Vault  → User-Assigned Managed Identity (env MI_CLIENT_ID)
#   Graph API  → CertificateCredential (cert .pfx recuperado do Key Vault)
#
# Build remoto (sem Docker local necessário):
#   az acr build --subscription <SUB_SHARED> --registry cr4shared \
#     --image graph4spo/indexer4spo:v2 --file Dockerfile .
#
# Teste local com .env:
#   docker run -p 8000:8000 --env-file .env -e MI_CLIENT_ID= \
#     cr4shared.azurecr.io/graph4spo/indexer4spo:v2
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim

WORKDIR /app

# Certificados CA necessários para TLS com serviços Azure
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Dependências Python
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30"

# Código fonte — NÃO copiar .env (excluído via .dockerignore)
COPY src/ ./src/

# Variáveis obrigatórias (valores definidos no Container App env vars):
# MI_CLIENT_ID            — client_id da User-Assigned Managed Identity
# SP_CLIENT_ID            — client_id do App Registration sp4spo (Graph API)
# SHAREPOINT_TENANT_ID    — tenant O365 do SharePoint
# SHAREPOINT_SITE_URL     — ex: https://07gc0.sharepoint.com
# SHAREPOINT_LIBRARY_NAME — ex: doc4index
# KV_CERT_NAME            — nome do secret no KV com o .pfx
# AZURE_KEYVAULT_URL      — ex: https://kv4spo.vault.azure.net/
# AZURE_SEARCH_INDEX_NAME — ex: idx4spo-v2
# AZURE_OPENAI_API_VERSION
# AZURE_OPENAI_EMBEDDING_DEPLOYMENT

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "src.indexer_api:app", "--host", "0.0.0.0", "--port", "8000"]
