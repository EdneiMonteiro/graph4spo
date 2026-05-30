<#
.SYNOPSIS
    Registra o App Registration e Service Principal no Entra ID.
    Importa o certificado X.509 no Azure Key Vault (chave privada fica 100% no KV).
    Associa o certificado ao App Registration (chave pública).
    Adiciona a permissão de aplicação Sites.Selected (Microsoft Graph).
    Exclui o arquivo PFX local após o import seguro no Key Vault.

.PREREQUISITE
    - 01_azure_resources.ps1 e 02_create_cert.ps1 executados com sucesso.
    - Conta com permissão para criar App Registrations no Entra ID.

.USAGE
    .\infra\03_register_sp.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$SUBSCRIPTION_ID  = "c5e0e3d6-4035-4e6b-aa64-cc8b5ec30745"
$KV_NAME          = "kv4spo"
$RG_NAME          = "rg4spo"
$APP_DISPLAY_NAME = "sp4spo"
$PFX_PATH         = "$PSScriptRoot\sp4spo.pfx"
$CER_PATH         = "$PSScriptRoot\sp4spo.cer"
$PFX_PASSWORD     = "TempPwd4Import!"
$KV_CERT_NAME     = "sp4spo-cert"

# IDs fixos do Microsoft Graph
$GRAPH_API_ID          = "00000003-0000-0000-c000-000000000000"
# Sites.Selected — permissão de aplicação (Role), não delegada
$SITES_SELECTED_PERM   = "883ea226-0bf2-4a8f-9f9d-92c9162a727d=Role"

az account set --subscription $SUBSCRIPTION_ID

# ---------------------------------------------------------------------------
# Verificar que os arquivos de certificado existem
# ---------------------------------------------------------------------------
if (-not (Test-Path $PFX_PATH)) {
    throw "Arquivo PFX não encontrado: $PFX_PATH`nExecute primeiro: .\infra\02_create_cert.ps1"
}
if (-not (Test-Path $CER_PATH)) {
    throw "Arquivo CER não encontrado: $CER_PATH`nExecute primeiro: .\infra\02_create_cert.ps1"
}

# ---------------------------------------------------------------------------
# Criar App Registration
# ---------------------------------------------------------------------------
Write-Host "`n▶ Criando App Registration '$APP_DISPLAY_NAME'..." -ForegroundColor Cyan
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
Start-Sleep -Seconds 10  # aguardar replicação

$SP_OID = az ad sp show --id $APP_ID --query "id" -o tsv
Write-Host "  SP Object ID : $SP_OID" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Importar certificado no Azure Key Vault
# ---------------------------------------------------------------------------
Write-Host "▶ Importando certificado '$KV_CERT_NAME' no Key Vault '$KV_NAME'..." -ForegroundColor Cyan
az keyvault certificate import `
    --vault-name $KV_NAME `
    --name $KV_CERT_NAME `
    --file $PFX_PATH `
    --password $PFX_PASSWORD `
    --output none

Write-Host "  Certificado importado com sucesso no Key Vault." -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Associar certificado (chave pública) ao App Registration no Entra ID
# ---------------------------------------------------------------------------
Write-Host "▶ Associando certificado ao App Registration..." -ForegroundColor Cyan
az ad app credential reset `
    --id $APP_ID `
    --cert "@$CER_PATH" `
    --append `
    --output none

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
Write-Host "▶ Tentando admin consent automático..." -ForegroundColor Cyan
az ad app permission admin-consent --id $APP_ID 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  ⚠ Admin consent automático falhou (requer Global Admin ou Application Admin)." -ForegroundColor Yellow
    Write-Host "  Opção 1 — Portal Azure:" -ForegroundColor Yellow
    Write-Host "    https://portal.azure.com → Entra ID → App Registrations → $APP_DISPLAY_NAME → API Permissions → Grant admin consent" -ForegroundColor White
    Write-Host "  Opção 2 — Azure CLI (com conta de admin):" -ForegroundColor Yellow
    Write-Host "    az ad app permission admin-consent --id $APP_ID" -ForegroundColor White
    Write-Host ""
    Write-Host "  IMPORTANTE: O script 04_grant_sharepoint.ps1 só funcionará após o admin consent." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Atribuir role Key Vault Secrets User ao SP (para leitura em runtime, se necessário)
# ---------------------------------------------------------------------------
# Nota: na arquitetura atual, os scripts Python usam DefaultAzureCredential (az login)
# para acessar o KV. O SP não precisa de acesso ao KV neste cenário de demo.
# Descomente se desejar que o SP acesse o KV de forma autônoma (ex: em produção/CI).
#
# $KV_RESOURCE_ID = az keyvault show --name $KV_NAME --resource-group $RG_NAME --query id -o tsv
# az role assignment create `
#     --role "Key Vault Secrets User" `
#     --assignee-object-id $SP_OID `
#     --assignee-principal-type ServicePrincipal `
#     --scope $KV_RESOURCE_ID `
#     --output none

# ---------------------------------------------------------------------------
# Armazenar Client ID no Key Vault (referência útil para scripts)
# ---------------------------------------------------------------------------
Write-Host "▶ Armazenando Client ID no Key Vault..." -ForegroundColor Cyan
az keyvault secret set `
    --vault-name $KV_NAME `
    --name "sp-client-id" `
    --value $APP_ID `
    --output none

# ---------------------------------------------------------------------------
# Atualizar AZURE_CLIENT_ID no .env
# ---------------------------------------------------------------------------
$envPath = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
if (Test-Path $envPath) {
    Write-Host "▶ Atualizando AZURE_CLIENT_ID no .env..." -ForegroundColor Cyan
    (Get-Content $envPath) -replace "^AZURE_CLIENT_ID=$", "AZURE_CLIENT_ID=$APP_ID" |
        Set-Content $envPath -Encoding UTF8
    Write-Host "  .env atualizado." -ForegroundColor Gray
} else {
    Write-Host "  ⚠ Arquivo .env não encontrado. Adicione manualmente: AZURE_CLIENT_ID=$APP_ID" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Excluir PFX local (chave privada já está segura no Key Vault)
# ---------------------------------------------------------------------------
Write-Host "▶ Excluindo PFX local (chave privada já está no Key Vault)..." -ForegroundColor Cyan
Remove-Item $PFX_PATH -Force
Write-Host "  $PFX_PATH removido." -ForegroundColor Gray
Write-Host "  CER (chave pública) mantido para referência: $CER_PATH" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------
Write-Host "`n✅ Service Principal configurado!" -ForegroundColor Green
Write-Host ("  App Display Name   : {0}" -f $APP_DISPLAY_NAME)
Write-Host ("  App ID (Client ID) : {0}" -f $APP_ID)
Write-Host ("  SP Object ID       : {0}" -f $SP_OID)
Write-Host ("  Certificado no KV  : {0}" -f $KV_CERT_NAME)
Write-Host ""
Write-Host "⚠  AÇÃO NECESSÁRIA: Admin consent para a permissão Sites.Selected" -ForegroundColor Yellow
Write-Host "   (ver instruções acima se o consent automático falhou)"
Write-Host ""
Write-Host "⏭  Próximo passo (após admin consent): .\infra\04_grant_sharepoint.ps1" -ForegroundColor Yellow
