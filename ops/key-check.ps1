# Inspect server.key's FORMAT ONLY - never prints key material.
# Tells you whether it is encrypted PEM (passphrase) or needs converting.
# ASCII only (PowerShell 5.1 reads .ps1 as ANSI on zh-CN systems).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\key-check.ps1 -Dir F:\certs

param([Parameter(Mandatory = $true)][string]$Dir)

$ErrorActionPreference = 'Continue'
$key = Join-Path $Dir 'server.key'

function Show($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }

Show '0. file'
if (-not (Test-Path $key)) { Write-Host "  NOT FOUND: $key" -ForegroundColor Red; return }
$i = Get-Item $key
Write-Host ("  " + $i.Name + "   " + $i.Length + " bytes")

Show '1. PEM header / footer lines (this is all we print of the file)'
$lines = Get-Content $key
$begins = $lines | Where-Object { $_ -match '^-----BEGIN' }
$ends   = $lines | Where-Object { $_ -match '^-----END' }
Write-Host ("  BEGIN: " + ($begins -join ' | '))
Write-Host ("  END  : " + ($ends -join ' | '))

Show '2. verdict'
$all = $begins -join ' '
$encrypted = $lines | Where-Object { $_ -match 'Proc-Type:\s*4,ENCRYPTED|DEK-Info:' }
if ($encrypted) {
    Write-Host '  This is an ENCRYPTED PEM (legacy openssl format) - has a passphrase.' -ForegroundColor Yellow
    Write-Host '  We need the passphrase to use it: at deploy time nginx/docker will ask for it,'
    Write-Host '  or better, decrypt it once into an unencrypted key on the server (see instructions).'
}
elseif ($all -match 'ENCRYPTED PRIVATE KEY') {
    Write-Host '  This is an ENCRYPTED PKCS#8 key - has a passphrase.' -ForegroundColor Yellow
}
elseif ($all -match 'BEGIN RSA PRIVATE KEY') {
    Write-Host '  PKCS#1 unencrypted RSA key (traditional format).' -ForegroundColor Green
}
elseif ($all -match 'BEGIN PRIVATE KEY') {
    Write-Host '  PKCS#8 unencrypted key. Some .NET/PS 5.1 builds cannot import PKCS#8 directly -' -ForegroundColor Yellow
    Write-Host '  the modulus check in cert-check.ps1 may still fail even though the key is fine.'
    Write-Host '  That is a PowerShell limitation, NOT a certificate problem.'
}
else {
    Write-Host '  Unrecognised header. Paste the BEGIN/END lines above to me.' -ForegroundColor Red
}

Show '3. does the key body look like base64'
$body = $lines | Where-Object { $_ -notmatch '^-{5}' -and $_ -notmatch ':' -and $_.Trim() -ne '' }
$joined = ($body -join '')
if ($joined.Length -gt 0) {
    $bad = ($joined -replace '[A-Za-z0-9+/=]', '').Length
    Write-Host ("  base64 body length: " + $joined.Length + " chars, unexpected chars: " + $bad)
} else {
    Write-Host '  no body lines found (file may be binary DER instead of PEM)' -ForegroundColor Yellow
}
