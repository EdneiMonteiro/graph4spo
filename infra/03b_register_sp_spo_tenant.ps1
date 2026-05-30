<#
.SYNOPSIS
    Registra o App Registration e Service Principal no tenant do SharePoint Online
    (07gc0.sharepoint.com — tenant 9b5a6e9d-983c-4d16-8ac4-74f1b29dd8c3).

    Este script é necessário quando o Azure (OpenAI, AI Search, Key Vault) está
    num tenant diferente do SharePoint Online — cenário multi-tenant comum.

    Arquitetura:
      - Tenant Azure (5bda9e44-...): OpenAI + AI Search + Key Vault + cert armazenado no KV
      - Tenant SharePoint (9b5a6e9d-...): App Registration com Sites.Selected

    O certificado público (.cer) gerado pelo script 02 é associado ao App Registration
    aqui. A chave privada permanece no Key Vault do tenant Azure — sem nenhum arquivo
    local de credencial.

.PREREQUISITE
    - Scripts 01_azure_resources.ps1 e 02_create_cert.ps1 executados com sucesso.
    - O arquivo sp4spo.cer deve existir em infra\.
    - Conta com permissão Global Admin ou Application Admin no tenant 07gc0.

.USAGE
    .\infra\03b_register_sp_spo_tenant.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Variáveis
# ---------------------------------------------------------------------------
$SPO_TENANT_ID    = "9b5a6e9d-983c-4d16-8ac4-74f1b29dd8c3"
$AZURE_TENANT_ID  = "5bda9e44-74f1-47eb-8741-460273dbb4bf"
$AZURE_SUB_ID     = "c5e0e3d6-4035-4e6b-aa64-cc8b5ec30745"
$KV_NAME          = "kv4spo"
$APP_DISPLAY_NAME = "sp4spo"
$CER_PATH         = "$PSScriptRoot\sp4spo.cer"

# IDs Microsoft Graph
$GRAPH_API_ID          = "00000003-0000-0000-c000-000000000000"
$SITES_SELECTED_PERM   = "883ea226-0bf2-4a8f-9f9d-92c9162a727d=Role"

# ---------------------------------------------------------------------------
# Verificar que o .cer existe
# ---------------------------------------------------------------------------
if (-not (Test-Path $CER_PATH)) {
    throw "Arquivo CER não encontrado: $CER_PATH`nExecute primeiro: .\infra\02_create_cert.ps1"
}

# ---------------------------------------------------------------------------
# Login no tenant do SharePoint
# ---------------------------------------------------------------------------
Write-Host "`n▶ Login no tenant do SharePoint (07gc0 — $SPO_TENANT_ID)..." -ForegroundColor Cyan
Write-Host "  Faça login com a conta que tem permissão de Admin no tenant 07gc0." -ForegroundColor Yellow
az login --tenant $SPO_TENANT_ID --allow-no-subscriptions --output none
if ($LASTEXITCODE -ne 0) { throw "Falha no login ao tenant do SharePoint." }
Write-Host "  Login realizado." -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Criar App Registration no tenant SharePoint
# ---------------------------------------------------------------------------
Write-Host "`n▶ Criando App Registration '$APP_DISPLAY_NAME' no tenant SharePoint..." -ForegroundColor Cyan
$APP_ID = az ad app create `
    --display-name $APP_DISPLAY_NAME `
    --sign-in-audience "AzureADMyOrg" `
    --query "appId" -o tsv

Write-Host "  App ID (Client ID) : $APP_ID" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Criar Service Principal
# ---------------------------------------------------------------------------
Write-Host "▶ Criando Service Principal..." -ForegroundColor Cyan
az ad sp create --id $APP_ID --output none
Start-Sleep -Seconds 10

$SP_OID = az ad sp show --id $APP_ID --query "id" -o tsv
Write-Host "  SP Object ID : $SP_OID" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Associar certificado (chave pública) ao App Registration
# ---------------------------------------------------------------------------
Write-Host "▶ Associando certificado público (.cer) ao App Registration..." -ForegroundColor Cyan
az ad app credential reset `
    --id $APP_ID `
    --cert "@$CER_PATH" `
    --append `
    --output none
Write-Host "  Certificado associado." -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Adicionar permissão Sites.Selected (Microsoft Graph — Application)
# ---------------------------------------------------------------------------
Write-Host "▶ Adicionando permissão Sites.Selected (Microsoft Graph)..." -ForegroundColor Cyan
az ad app permission add `
    --id $APP_ID `
    --api $GRAPH_API_ID `
    --api-permissions $SITES_SELECTED_PERM `
    --output none

# ---------------------------------------------------------------------------
# Admin Consent
# ---------------------------------------------------------------------------
Write-Host "▶ Concedendo admin consent..." -ForegroundColor Cyan
az ad app permission admin-consent --id $APP_ID 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  ⚠ Admin consent automático falhou." -ForegroundColor Yellow
    Write-Host "  Portal: Entra ID → App Registrations → $APP_DISPLAY_NAME → API Permissions → Grant admin consent" -ForegroundColor White
}
else {
    Write-Host "  Admin consent concedido." -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# Atualizar .env com SHAREPOINT_TENANT_ID e AZURE_CLIENT_ID
# ---------------------------------------------------------------------------
$envPath = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
if (Test-Path $envPath) {
    Write-Host "`n▶ Atualizando .env com SHAREPOINT_TENANT_ID e AZURE_CLIENT_ID..." -ForegroundColor Cyan
    $envContent = Get-Content $envPath

    # Atualiza AZURE_CLIENT_ID
    $envContent = $envContent -replace "^AZURE_CLIENT_ID=.*", "AZURE_CLIENT_ID=$APP_ID"

    # Adiciona/atualiza SHAREPOINT_TENANT_ID (abaixo de AZURE_TENANT_ID)
    if ($envContent -match "SHAREPOINT_TENANT_ID=") {
        $envContent = $envContent -replace "^SHAREPOINT_TENANT_ID=.*", "SHAREPOINT_TENANT_ID=$SPO_TENANT_ID"
    } else {
        $envContent = $envContent -replace "(AZURE_TENANT_ID=.*)", "`$1`nSHAREPOINT_TENANT_ID=$SPO_TENANT_ID"
    }

    Set-Content $envPath $envContent -Encoding UTF8
    Write-Host "  .env atualizado." -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# Re-login no tenant Azure para atualizar o Key Vault
# ---------------------------------------------------------------------------
Write-Host "`n▶ Re-login no tenant Azure ($AZURE_TENANT_ID) para atualizar Key Vault..." -ForegroundColor Cyan
az login --tenant $AZURE_TENANT_ID --allow-no-subscriptions --output none
az account set --subscription $AZURE_SUB_ID

Write-Host "▶ Atualizando secret 'sp-client-id' no Key Vault '$KV_NAME'..." -ForegroundColor Cyan
az keyvault secret set `
    --vault-name $KV_NAME `
    --name "sp-client-id" `
    --value $APP_ID `
    --output none
Write-Host "  Secret atualizado." -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------
Write-Host "`n✅ Service Principal criado no tenant SharePoint!" -ForegroundColor Green
Write-Host ("  Tenant SharePoint : {0}" -f $SPO_TENANT_ID)
Write-Host ("  App ID (ClientID) : {0}" -f $APP_ID)
Write-Host ("  SP Object ID      : {0}" -f $SP_OID)
Write-Host ""
Write-Host "  O Key Vault do tenant Azure foi atualizado com o novo Client ID." -ForegroundColor Gray
Write-Host "  O certificado público foi associado ao App Registration no tenant SharePoint." -ForegroundColor Gray
Write-Host ""
Write-Host "⏭  Próximo passo: .\infra\04_grant_sharepoint.ps1" -ForegroundColor Yellow
Write-Host "   (Execute com login no tenant do SharePoint se necessário)" -ForegroundColor Yellow
