<#
.SYNOPSIS
  Valida localmente el snapshot vivo de LinkedIn usado por el perfil GitHub.

.DESCRIPTION
  Este script está pensado para ejecutarse en la misma máquina/red del runner
  self-hosted. Lee LINKEDIN_COOKIE desde variable de entorno o desde un archivo
  local ignorado por git, genera .linkedin-profile.json y ejecuta el diagnóstico
  del generador sin usar el fallback LINKEDIN_PROFILE_JSON.
#>
[CmdletBinding()]
param(
  [string]$CookieFile = ".linkedin-cookie.txt",
  [switch]$WriteReadme,
  [switch]$SkipPlaywrightInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "==> $Message" -ForegroundColor Cyan
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

if (-not (Test-Path "scripts/fetch_linkedin_profile.mjs")) {
  throw "Ejecuta este script desde el repo h0w4r/h0w4r o conserva la estructura scripts/."
}

$cookie = $env:LINKEDIN_COOKIE
if ([string]::IsNullOrWhiteSpace($cookie) -and (Test-Path $CookieFile)) {
  $cookie = Get-Content -Path $CookieFile -Raw
}

if ([string]::IsNullOrWhiteSpace($cookie)) {
  throw "No encontré LINKEDIN_COOKIE. Define `$env:LINKEDIN_COOKIE o crea $CookieFile con el header Cookie de LinkedIn."
}

$hasLiAt = $cookie -match '(^|;\s*)li_at='
$hasJsession = $cookie -match '(^|;\s*)JSESSIONID='
Write-Step "Cookie detectada: li_at=$hasLiAt JSESSIONID=$hasJsession"

if (-not $SkipPlaywrightInstall) {
  Write-Step "Instalando/actualizando cliente Playwright local sin descargar navegador"
  $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
  npm install --no-save --ignore-scripts playwright@1.56.1
}

Write-Step "Extrayendo snapshot LinkedIn vivo"
$env:LINKEDIN_COOKIE = $cookie
$env:LINKEDIN_PROFILE_URL = "https://www.linkedin.com/in/cehp94/"
$env:PLAYWRIGHT_CHROMIUM_CHANNEL = "chrome"
node scripts/fetch_linkedin_profile.mjs > .linkedin-profile.json

if (-not (Test-Path ".linkedin-profile.json")) {
  throw "No se generó .linkedin-profile.json"
}

Write-Step "Diagnosticando snapshot sin fallback"
$env:LINKEDIN_PROFILE_JSON_FILE = ".linkedin-profile.json"
Remove-Item Env:\LINKEDIN_PROFILE_JSON -ErrorAction SilentlyContinue
python scripts/build_profile.py --linkedin-diagnostics --require-linkedin-when-configured

if ($WriteReadme) {
  Write-Step "Regenerando README.md con snapshot local"
  python scripts/build_profile.py --write
  python scripts/build_profile.py --check
}

Write-Step "OK: LinkedIn vivo disponible para el workflow self-hosted"
