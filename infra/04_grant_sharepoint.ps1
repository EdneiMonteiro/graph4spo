<#
.SYNOPSIS
    Cria a biblioteca de documentos 'doc4index' no SharePoint Online.
    Concede ao Service Principal permissão 'write' exclusiva nesse site
    via Sites.Selected (escopo mínimo de privilégio).

.PREREQUISITE
    - Admin consent da permissão Sites.Selected já concedido (script anterior).
    - Usuário logado deve ser SharePoint Admin ou Global Admin.
    - O Admin deve ter executado o grant de Sites.Selected no portal ou via CLI.

.USAGE
    .\infra\04_grant_sharepoint.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$SPO_TENANT_ID    = "9b5a6e9d-983c-4d16-8ac4-74f1b29dd8c3"
$SHAREPOINT_HOST  = "07gc0.sharepoint.com"
$SITE_RELATIVE    = "/"          # site raiz — mude para /sites/nome se for subsite
$LIBRARY_NAME     = "doc4index"
$SP_DISPLAY_NAME  = "sp4spo"

# Lê AZURE_CLIENT_ID do .env — não requer login no tenant Azure
$envPath = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
$APP_ID  = ""
if (Test-Path $envPath) {
    $match = Select-String -Path $envPath -Pattern "^AZURE_CLIENT_ID=(.+)" | Select-Object -First 1
    if ($match) { $APP_ID = $match.Matches[0].Groups[1].Value.Trim() }
}
if (-not $APP_ID) {
    throw "AZURE_CLIENT_ID não encontrado no .env. Execute 03b_register_sp_spo_tenant.ps1 primeiro."
}
Write-Host "  App ID (Client ID) : $APP_ID" -ForegroundColor Gray

# Garante login no tenant do SharePoint para obter token Graph correto
Write-Host "`n▶ Verificando login no tenant do SharePoint ($SPO_TENANT_ID)..." -ForegroundColor Cyan
$currentTenant = az account show --query tenantId -o tsv 2>$null
if ($currentTenant -ne $SPO_TENANT_ID) {
    Write-Host "  Re-login necessário no tenant do SharePoint..." -ForegroundColor Yellow
    az login --tenant $SPO_TENANT_ID --allow-no-subscriptions --output none
}

# ---------------------------------------------------------------------------
# Obter token Microsoft Graph do tenant do SharePoint
# ---------------------------------------------------------------------------
Write-Host "▶ Obtendo token Microsoft Graph do tenant SharePoint..." -ForegroundColor Cyan
$TOKEN = az account get-access-token `
    --resource "https://graph.microsoft.com" `
    --tenant $SPO_TENANT_ID `
    --query "accessToken" -o tsv

$HEADERS = @{
    "Authorization" = "Bearer $TOKEN"
    "Content-Type"  = "application/json"
}

# ---------------------------------------------------------------------------
# Resolver Site ID via Microsoft Graph
# ---------------------------------------------------------------------------
Write-Host "▶ Resolvendo Site ID para '$SHAREPOINT_HOST'..." -ForegroundColor Cyan

$encodedPath = [Uri]::EscapeDataString($SITE_RELATIVE.TrimStart("/"))
$siteQuery   = if ($encodedPath -eq "") {
    "https://graph.microsoft.com/v1.0/sites/$SHAREPOINT_HOST`:`/?`$select=id,displayName,webUrl"
} else {
    "https://graph.microsoft.com/v1.0/sites/$SHAREPOINT_HOST`:/sites/$encodedPath`?`$select=id,displayName,webUrl"
}

$siteResp = Invoke-RestMethod -Uri $siteQuery -Headers $HEADERS -Method GET
$SITE_ID  = $siteResp.id

Write-Host "  Site ID      : $SITE_ID" -ForegroundColor Gray
Write-Host "  Display Name : $($siteResp.displayName)" -ForegroundColor Gray
Write-Host "  URL          : $($siteResp.webUrl)" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Criar biblioteca de documentos 'doc4index'
# ---------------------------------------------------------------------------
Write-Host "`n▶ Criando biblioteca de documentos '$LIBRARY_NAME'..." -ForegroundColor Cyan

$createBody = @{
    displayName = $LIBRARY_NAME
    description = "Biblioteca para indexação RAG com Azure AI Search"
    list        = @{ template = "documentLibrary" }
} | ConvertTo-Json -Depth 3

try {
    $listResp = Invoke-RestMethod `
        -Uri "https://graph.microsoft.com/v1.0/sites/$SITE_ID/lists" `
        -Headers $HEADERS `
        -Method POST `
        -Body $createBody

    $LIST_ID = $listResp.id
    Write-Host "  Biblioteca criada! ID: $LIST_ID" -ForegroundColor Gray
}
catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 409) {
        Write-Host "  ℹ Biblioteca '$LIBRARY_NAME' já existe — consultando ID..." -ForegroundColor Yellow
        $listsResp = Invoke-RestMethod `
            -Uri ("https://graph.microsoft.com/v1.0/sites/$SITE_ID/lists" +
                  "?`$filter=displayName eq '$LIBRARY_NAME'&`$select=id,displayName") `
            -Headers $HEADERS -Method GET
        $LIST_ID = $listsResp.value[0].id
        Write-Host "  ID da biblioteca existente: $LIST_ID" -ForegroundColor Gray
    }
    else {
        throw
    }
}

# ---------------------------------------------------------------------------
# Conceder permissão Sites.Selected ao Service Principal
# Escopo: somente este site — princípio de menor privilégio
# ---------------------------------------------------------------------------
Write-Host "`n▶ Concedendo permissão 'write' ao SP '$SP_DISPLAY_NAME' ($APP_ID) no site..." -ForegroundColor Cyan

$permBody = @{
    roles               = @("write")
    grantedToIdentities = @(
        @{
            application = @{
                id          = $APP_ID
                displayName = $SP_DISPLAY_NAME
            }
        }
    )
} | ConvertTo-Json -Depth 5

$permResp = Invoke-RestMethod `
    -Uri "https://graph.microsoft.com/v1.0/sites/$SITE_ID/permissions" `
    -Headers $HEADERS `
    -Method POST `
    -Body $permBody

Write-Host "  Permissão concedida. Permission ID: $($permResp.id)" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Verificar permissões ativas no site (auditoria)
# ---------------------------------------------------------------------------
Write-Host "`n▶ Verificando permissões ativas no site..." -ForegroundColor Cyan
$permsResp = Invoke-RestMethod `
    -Uri "https://graph.microsoft.com/v1.0/sites/$SITE_ID/permissions" `
    -Headers $HEADERS -Method GET

foreach ($perm in $permsResp.value) {
    $grantedTo = ($perm.grantedToIdentities | ForEach-Object {
        if ($_.application) { $_.application.displayName }
        elseif ($_.user)    { $_.user.displayName }
    }) -join ", "
    Write-Host ("  [{0}] roles={1} → {2}" -f $perm.id, ($perm.roles -join ","), $grantedTo) -ForegroundColor Gray
}

# ---------------------------------------------------------------------------
# Resumo e próximos passos
# ---------------------------------------------------------------------------
Write-Host "`n✅ SharePoint configurado com sucesso!" -ForegroundColor Green
Write-Host ("  Site ID      : {0}" -f $SITE_ID)
Write-Host ("  Biblioteca   : {0} (ID: {1})" -f $LIBRARY_NAME, $LIST_ID)
Write-Host ("  Permissão    : write → {0} ({1})" -f $SP_DISPLAY_NAME, $APP_ID)
Write-Host ""
Write-Host "⏭  Próximos passos:" -ForegroundColor Yellow
Write-Host "   1. Faça upload dos 10 arquivos .txt da pasta docs/ para a biblioteca '$LIBRARY_NAME'."
Write-Host "      Acesse: $($siteResp.webUrl)/$LIBRARY_NAME"
Write-Host "   2. Instale as dependências Python:"
Write-Host "      pip install -r requirements.txt"
Write-Host "   3. Execute a indexação:"
Write-Host "      python src/index_documents.py"
