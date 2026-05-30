<#
.SYNOPSIS
    Provisiona: Resource Group, Azure OpenAI, Azure AI Search, Azure Key Vault.
    Armazena endpoints e API keys sensíveis no Key Vault.
    Gera o arquivo .env com configurações não-secretas.

.PREREQUISITE
    - Azure CLI instalado e autenticado: az login --tenant 5bda9e44-74f1-47eb-8741-460273dbb4bf
    - Permissão de Contributor na subscription c5e0e3d6-4035-4e6b-aa64-cc8b5ec30745

.USAGE
    .\infra\01_azure_resources.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Variáveis
# ---------------------------------------------------------------------------
$SUBSCRIPTION_ID = "c5e0e3d6-4035-4e6b-aa64-cc8b5ec30745"
$TENANT_ID       = "5bda9e44-74f1-47eb-8741-460273dbb4bf"
$LOCATION        = "eastus2"
$RG_NAME         = "rg4spo"
$OAI_NAME        = "oai4spo"
$SRCH_NAME       = "srch4spo"
$KV_NAME         = "kv4spo"
$INDEX_NAME      = "idx4spo"

# ---------------------------------------------------------------------------
# Login / Subscription
# ---------------------------------------------------------------------------
Write-Host "`n▶ Configurando subscription..." -ForegroundColor Cyan
az account set --subscription $SUBSCRIPTION_ID
if ($LASTEXITCODE -ne 0) { throw "Falha ao definir subscription. Execute 'az login' primeiro." }

# ---------------------------------------------------------------------------
# Resource Group
# ---------------------------------------------------------------------------
Write-Host "▶ Criando resource group '$RG_NAME' em '$LOCATION'..." -ForegroundColor Cyan
az group create `
    --name $RG_NAME `
    --location $LOCATION `
    --output none

# ---------------------------------------------------------------------------
# Azure OpenAI
# ---------------------------------------------------------------------------
Write-Host "▶ Criando Azure OpenAI '$OAI_NAME'..." -ForegroundColor Cyan
az cognitiveservices account create `
    --name $OAI_NAME `
    --resource-group $RG_NAME `
    --kind OpenAI `
    --sku S0 `
    --location $LOCATION `
    --yes `
    --output none

Write-Host "  ↳ Aguardando provisionamento do Azure OpenAI..." -ForegroundColor Gray
Start-Sleep -Seconds 20

Write-Host "  ↳ Deploying gpt-4o-mini..." -ForegroundColor Gray
az cognitiveservices account deployment create `
    --name $OAI_NAME `
    --resource-group $RG_NAME `
    --deployment-name "gpt-4o-mini" `
    --model-name "gpt-4o-mini" `
    --model-version "2024-07-18" `
    --model-format OpenAI `
    --sku-capacity 10 `
    --sku-name "Standard" `
    --output none

Write-Host "  ↳ Deploying text-embedding-3-small..." -ForegroundColor Gray
az cognitiveservices account deployment create `
    --name $OAI_NAME `
    --resource-group $RG_NAME `
    --deployment-name "text-embedding-3-small" `
    --model-name "text-embedding-3-small" `
    --model-version "1" `
    --model-format OpenAI `
    --sku-capacity 10 `
    --sku-name "Standard" `
    --output none

# ---------------------------------------------------------------------------
# Azure AI Search (Free SKU)
# ---------------------------------------------------------------------------
Write-Host "▶ Criando Azure AI Search '$SRCH_NAME' (Free SKU)..." -ForegroundColor Cyan
az search service create `
    --name $SRCH_NAME `
    --resource-group $RG_NAME `
    --sku free `
    --location $LOCATION `
    --output none

# ---------------------------------------------------------------------------
# Azure Key Vault (RBAC mode)
# ---------------------------------------------------------------------------
Write-Host "▶ Criando Azure Key Vault '$KV_NAME' (RBAC authorization)..." -ForegroundColor Cyan
az keyvault create `
    --name $KV_NAME `
    --resource-group $RG_NAME `
    --location $LOCATION `
    --enable-rbac-authorization true `
    --output none

$KV_RESOURCE_ID = az keyvault show `
    --name $KV_NAME `
    --resource-group $RG_NAME `
    --query id -o tsv

# Obter OID do usuário atual para atribuir roles no Key Vault
$CURRENT_USER_OID = az ad signed-in-user show --query id -o tsv
Write-Host "  ↳ Concedendo roles ao usuário atual ($CURRENT_USER_OID)..." -ForegroundColor Gray

az role assignment create `
    --role "Key Vault Secrets Officer" `
    --assignee-object-id $CURRENT_USER_OID `
    --assignee-principal-type User `
    --scope $KV_RESOURCE_ID `
    --output none

az role assignment create `
    --role "Key Vault Certificates Officer" `
    --assignee-object-id $CURRENT_USER_OID `
    --assignee-principal-type User `
    --scope $KV_RESOURCE_ID `
    --output none

# Aguardar propagação do RBAC antes de criar secrets
Write-Host "  ↳ Aguardando propagação do RBAC (40s)..." -ForegroundColor Gray
Start-Sleep -Seconds 40

# ---------------------------------------------------------------------------
# Capturar endpoints e chaves
# ---------------------------------------------------------------------------
Write-Host "▶ Coletando endpoints e chaves..." -ForegroundColor Cyan

$OAI_ENDPOINT = az cognitiveservices account show `
    --name $OAI_NAME --resource-group $RG_NAME `
    --query "properties.endpoint" -o tsv

$OAI_KEY = az cognitiveservices account keys list `
    --name $OAI_NAME --resource-group $RG_NAME `
    --query "key1" -o tsv

$SRCH_ENDPOINT = "https://$SRCH_NAME.search.windows.net"
$SRCH_KEY = az search admin-key show `
    --service-name $SRCH_NAME --resource-group $RG_NAME `
    --query "primaryKey" -o tsv

# ---------------------------------------------------------------------------
# Armazenar secrets no Key Vault
# ---------------------------------------------------------------------------
Write-Host "▶ Armazenando secrets no Key Vault '$KV_NAME'..." -ForegroundColor Cyan

az keyvault secret set --vault-name $KV_NAME --name "openai-api-key"    --value $OAI_KEY      --output none
az keyvault secret set --vault-name $KV_NAME --name "openai-endpoint"   --value $OAI_ENDPOINT  --output none
az keyvault secret set --vault-name $KV_NAME --name "search-api-key"    --value $SRCH_KEY      --output none
az keyvault secret set --vault-name $KV_NAME --name "search-endpoint"   --value $SRCH_ENDPOINT --output none

Write-Host "  ↳ 4 secrets armazenados." -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Gerar arquivo .env (zero segredos — apenas referências)
# ---------------------------------------------------------------------------
Write-Host "▶ Gerando arquivo .env..." -ForegroundColor Cyan
$KV_URL = "https://$KV_NAME.vault.azure.net/"

$envContent = @"
# =============================================================================
# Identidade — preencha AZURE_CLIENT_ID após executar 03_register_sp.ps1
# =============================================================================
AZURE_TENANT_ID=$TENANT_ID
AZURE_CLIENT_ID=

# =============================================================================
# Key Vault — URL pública, não é segredo
# Todos os API keys ficam armazenados no Key Vault, não neste arquivo.
# =============================================================================
AZURE_KEYVAULT_URL=$KV_URL

# Certificado do Service Principal (nome do cert armazenado no Key Vault)
KV_CERT_NAME=sp4spo-cert

# =============================================================================
# SharePoint Online
# =============================================================================
SHAREPOINT_SITE_URL=https://07gc0.sharepoint.com
SHAREPOINT_LIBRARY_NAME=doc4index

# =============================================================================
# Azure AI Search — configurações não-secretas
# =============================================================================
AZURE_SEARCH_INDEX_NAME=$INDEX_NAME

# =============================================================================
# Azure OpenAI — configurações de deployment (não são segredos)
# =============================================================================
AZURE_OPENAI_API_VERSION=2024-12-01-preview
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
"@

$envPath = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
Set-Content -Path $envPath -Value $envContent -Encoding UTF8
Write-Host "  .env criado em: $envPath" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------
Write-Host "`n✅ Recursos provisionados com sucesso!" -ForegroundColor Green
Write-Host ("  Resource Group : {0}" -f $RG_NAME)
Write-Host ("  Azure OpenAI   : {0}" -f $OAI_ENDPOINT)
Write-Host ("  AI Search      : {0}" -f $SRCH_ENDPOINT)
Write-Host ("  Key Vault      : {0}" -f $KV_URL)
Write-Host ""
Write-Host "⏭  Próximo passo: .\infra\02_create_cert.ps1" -ForegroundColor Yellow
