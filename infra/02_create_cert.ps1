<#
.SYNOPSIS
    Cria um certificado X.509 self-signed (RSA 2048 / SHA-256) para o Service Principal.
    Exporta o arquivo PFX (chave privada + pública) para import no Azure Key Vault.
    O arquivo PFX local será excluído automaticamente pelo script 03_register_sp.ps1
    após o import no Key Vault.

.USAGE
    .\infra\02_create_cert.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CERT_SUBJECT   = "CN=sp4spo"
$CERT_STORE     = "Cert:\CurrentUser\My"
$VALIDITY_YEARS = 2
$PFX_PATH       = "$PSScriptRoot\sp4spo.pfx"
$CER_PATH       = "$PSScriptRoot\sp4spo.cer"

# Senha apenas para o transporte do PFX durante o import no Key Vault.
# O arquivo será excluído logo após o import.
$PFX_PASSWORD   = "TempPwd4Import!"
$pfxPwd = ConvertTo-SecureString -String $PFX_PASSWORD -Force -AsPlainText

Write-Host "`n▶ Criando certificado X.509 self-signed '$CERT_SUBJECT'..." -ForegroundColor Cyan
Write-Host "  Algoritmo : RSA 2048 bits / SHA-256" -ForegroundColor Gray
Write-Host "  Validade  : $VALIDITY_YEARS anos" -ForegroundColor Gray

$cert = New-SelfSignedCertificate `
    -Subject $CERT_SUBJECT `
    -CertStoreLocation $CERT_STORE `
    -KeyExportPolicy Exportable `
    -KeySpec Signature `
    -KeyLength 2048 `
    -KeyAlgorithm RSA `
    -HashAlgorithm SHA256 `
    -NotAfter (Get-Date).AddYears($VALIDITY_YEARS)

Write-Host "  Thumbprint : $($cert.Thumbprint)" -ForegroundColor Gray
Write-Host "  Período    : $($cert.NotBefore.ToString('yyyy-MM-dd')) → $($cert.NotAfter.ToString('yyyy-MM-dd'))" -ForegroundColor Gray

# Exportar PFX (chave privada + pública) — destinado ao import no Azure Key Vault
Write-Host "`n▶ Exportando PFX (privado + público) para '$PFX_PATH'..." -ForegroundColor Cyan
Export-PfxCertificate -Cert $cert -FilePath $PFX_PATH -Password $pfxPwd | Out-Null

# Exportar CER (somente chave pública) — para associar ao App Registration no Entra ID
Write-Host "▶ Exportando CER (chave pública) para '$CER_PATH'..." -ForegroundColor Cyan
Export-Certificate -Cert $cert -FilePath $CER_PATH -Type CERT | Out-Null

Write-Host "`n✅ Certificado gerado!" -ForegroundColor Green
Write-Host "  PFX (chave privada + pública) : $PFX_PATH"
Write-Host "  CER (chave pública)           : $CER_PATH"
Write-Host "  Thumbprint                    : $($cert.Thumbprint)"
Write-Host ""
Write-Host "⚠  AVISO DE SEGURANÇA:" -ForegroundColor Yellow
Write-Host "   O arquivo PFX contém a chave privada. Ele será importado no Azure Key Vault"
Write-Host "   pelo script 03_register_sp.ps1 e automaticamente excluído em seguida."
Write-Host "   Não faça commit deste arquivo no Git."
Write-Host ""
Write-Host "⏭  Próximo passo: .\infra\03_register_sp.ps1" -ForegroundColor Yellow
