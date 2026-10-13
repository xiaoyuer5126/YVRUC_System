# Certificate inspection for Nginx.zip (server.cer + server.key).
# Pure PowerShell 5.1 / .NET Framework - no openssl, no Docker needed.
#
# ASCII only on purpose: PowerShell 5.1 reads .ps1 as ANSI (GBK on zh-CN), so any
# non-ASCII text in this file gets mangled and breaks the parser.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File F:\RUCVA_system\cert-check.ps1 -Dir F:\certs

param([Parameter(Mandatory = $true)][string]$Dir)

$ErrorActionPreference = 'Continue'
$cer = Join-Path $Dir 'server.cer'
$key = Join-Path $Dir 'server.key'

function Show($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }

function Get-ModulusHash([System.Security.Cryptography.X509Certificates.X509Certificate2]$Cert) {
    $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($Cert)
    if (-not $rsa) { return $null }
    $mod = $rsa.ExportParameters($false).Modulus
    return [BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($mod))
}

Show '0. environment'
Write-Host ("  PowerShell: " + $PSVersionTable.PSVersion + "   CLR: " + [System.Environment]::Version)

Show '1. files'
$missing = $false
foreach ($f in @($cer, $key)) {
    if (Test-Path $f) {
        $i = Get-Item $f
        Write-Host ("  " + $i.Name.PadRight(14) + $i.Length.ToString().PadLeft(10) + " bytes   " + $i.LastWriteTime)
    } else {
        Write-Host ("  " + (Split-Path $f -Leaf).PadRight(14) + "  !! NOT FOUND: $f") -ForegroundColor Red
        $missing = $true
    }
}
if ($missing) {
    Write-Host "`n  Unzip Nginx.zip into $Dir (expect server.cer and server.key), then rerun." -ForegroundColor Yellow
    return
}

$raw = Get-Content $cer -Raw

Show '2. server.cer format'
if ($raw -match 'BEGIN CERTIFICATE') { Write-Host '  PEM text (nginx can use it directly)' -ForegroundColor Green }
elseif ($raw -match 'BEGIN PKCS7') { Write-Host '  PKCS#7 - needs conversion' -ForegroundColor Yellow }
else { Write-Host '  binary DER - needs conversion' -ForegroundColor Yellow }

Show '3. how many certificates inside'
$count = ([regex]::Matches($raw, 'BEGIN CERTIFICATE')).Count
if ($count -ge 2) { Write-Host ("  $count certs -> includes intermediate(s), i.e. a complete fullchain") -ForegroundColor Green }
elseif ($count -eq 1) { Write-Host "  1 cert -> leaf only, intermediate may be missing" -ForegroundColor Yellow }
else { Write-Host "  0 (not PEM text, treat as DER)" -ForegroundColor Yellow }

Show '4. certificate details'
$cert = $null
try { $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($cer) }
catch { Write-Host ("  load failed: " + $_.Exception.Message) -ForegroundColor Red }

if ($cert) {
    Write-Host ("  Subject  : " + $cert.Subject)
    Write-Host ("  Issuer   : " + $cert.Issuer)
    Write-Host ("  Valid    : " + $cert.NotBefore.ToString('yyyy-MM-dd') + "  ->  " + $cert.NotAfter.ToString('yyyy-MM-dd'))
    $days = [int]($cert.NotAfter - (Get-Date)).TotalDays
    $color = if ($days -lt 30) { 'Yellow' } else { 'Gray' }
    Write-Host ("  Days left: $days") -ForegroundColor $color
    $san = $cert.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.17' }
    if ($san) { Write-Host ("  SAN      : " + $san.Format($false)) }
    else { Write-Host '  SAN      : none (old-style cert, modern browsers reject it)' -ForegroundColor Yellow }
    Write-Host ("  Thumbprint(SHA1): " + $cert.Thumbprint)
}

Show '5. do cert and key match'
if (-not $cert) { Write-Host '  skipped: certificate did not load'; return }

$certHash = Get-ModulusHash $cert
if (-not $certHash) { Write-Host '  skipped: no RSA public key in cert (maybe ECC)'; return }
Write-Host ("  cert modulus hash: " + $certHash)

try {
    $keyCert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2
    $keyCert.Import($key)
    $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($keyCert)
    $keyMod = $rsa.ExportParameters($false).Modulus
    $keyHash = [BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($keyMod))
    Write-Host ("  key  modulus hash: " + $keyHash)
    if ($keyHash -eq $certHash) { Write-Host '  RESULT: pair OK' -ForegroundColor Green }
    else { Write-Host '  RESULT: MISMATCH - cert and key are not a pair, ask the school to reissue' -ForegroundColor Red }
} catch {
    Write-Host ("  key load failed: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host '  -> the key may be passphrase-protected (encrypted PEM). Send me this error.'
}
