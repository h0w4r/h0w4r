<#
.SYNOPSIS
  Crea/renueva la sesión persistente de LinkedIn para el runner self-hosted.

.DESCRIPTION
  Abre Chrome con un perfil dedicado y persistente. Inicia sesión manualmente en
  LinkedIn en esa ventana, vuelve a la consola y pulsa Enter. El perfil queda
  guardado en LOCALAPPDATA para que el workflow diario pueda leer LinkedIn desde
  la misma máquina sin depender de cookies pegadas cada semana.
#>
[CmdletBinding()]
param(
  [string]$UserDataDir = (Join-Path $env:LOCALAPPDATA "h0w4r-linkedin-sync\browser-profile"),
  [switch]$SkipPlaywrightInstall,
  [switch]$WriteReadme
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

if (-not $SkipPlaywrightInstall) {
  Write-Step "Instalando/actualizando cliente Playwright local sin descargar navegador"
  $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
  npm install --no-save --ignore-scripts playwright@1.56.1
}

New-Item -ItemType Directory -Force -Path $UserDataDir | Out-Null
Write-Step "Usando perfil persistente: $UserDataDir"
Write-Host "Se abrirá Chrome. Inicia sesión en LinkedIn si te lo pide y luego pulsa Enter en esta consola." -ForegroundColor Yellow

$env:LINKEDIN_USER_DATA_DIR = $UserDataDir
$env:LINKEDIN_PROFILE_URL = "https://www.linkedin.com/in/cehp94/"
$env:PLAYWRIGHT_CHROMIUM_CHANNEL = "chrome"
$env:LINKEDIN_HEADLESS = "false"
$env:LINKEDIN_INTERACTIVE_LOGIN = "1"
Remove-Item Env:\LINKEDIN_COOKIE -ErrorAction SilentlyContinue

Write-Step "Validando sesión interactiva contra LinkedIn"
node scripts/fetch_linkedin_profile.mjs > .linkedin-profile.json

if (-not (Test-Path ".linkedin-profile.json")) {
  throw "No se generó .linkedin-profile.json"
}

$env:LINKEDIN_PROFILE_JSON_FILE = ".linkedin-profile.json"
$env:LINKEDIN_SNAPSHOT_ONLY = "1"
Write-Step "Diagnosticando snapshot sin fallback"
python scripts/build_profile.py --linkedin-diagnostics --require-linkedin-when-configured

if ($WriteReadme) {
  Write-Step "Regenerando README.md con la sesión local"
  python scripts/build_profile.py --write
  python scripts/build_profile.py --check
}

Write-Step "OK: sesión local persistente lista para el workflow diario"
Write-Host "Si instalas el runner como servicio, asegúrate de que corra con este mismo usuario; si corre con otro usuario, tendrás que repetir este bootstrap bajo esa cuenta." -ForegroundColor Yellow
