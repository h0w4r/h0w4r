<#
.SYNOPSIS
  Valida localmente el snapshot vivo de LinkedIn usado por el perfil GitHub.

.DESCRIPTION
  Este script está pensado para ejecutarse en la misma máquina/red del runner
  self-hosted. Prefiere una sesión persistente creada con
  scripts/bootstrap_linkedin_session.ps1. Si no existe, puede usar LINKEDIN_COOKIE
  o .linkedin-cookie.txt como fallback legacy.
#>
[CmdletBinding()]
param(
  [string]$UserDataDir = (Join-Path $env:LOCALAPPDATA "h0w4r-linkedin-sync\browser-profile"),
  [string]$CookieFile = ".linkedin-cookie.txt",
  [switch]$WriteReadme,
  [switch]$SkipPlaywrightInstall,
  [switch]$ForceCookie
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

$cookie = $env:LINKEDIN_COOKIE
if ([string]::IsNullOrWhiteSpace($cookie) -and (Test-Path $CookieFile)) {
  $cookie = Get-Content -Path $CookieFile -Raw
}

$usingPersistentSession = (Test-Path $UserDataDir) -and -not $ForceCookie
if ($usingPersistentSession) {
  Write-Step "Usando sesión persistente LinkedIn: $UserDataDir"
  $env:LINKEDIN_USER_DATA_DIR = $UserDataDir
  Remove-Item Env:\LINKEDIN_COOKIE -ErrorAction SilentlyContinue
} elseif (-not [string]::IsNullOrWhiteSpace($cookie)) {
  $hasLiAt = $cookie -match '(^|;\s*)li_at='
  $hasJsession = $cookie -match '(^|;\s*)JSESSIONID='
  Write-Step "Usando cookie fallback: li_at=$hasLiAt JSESSIONID=$hasJsession"
  $env:LINKEDIN_COOKIE = $cookie
  Remove-Item Env:\LINKEDIN_USER_DATA_DIR -ErrorAction SilentlyContinue
} else {
  throw "No encontré sesión LinkedIn. Ejecuta .\scripts\bootstrap_linkedin_session.ps1 o define `$env:LINKEDIN_COOKIE / $CookieFile como fallback."
}

Write-Step "Extrayendo snapshot LinkedIn vivo"
$env:LINKEDIN_PROFILE_URL = "https://www.linkedin.com/in/cehp94/"
$env:PLAYWRIGHT_CHROMIUM_CHANNEL = "chrome"
$env:LINKEDIN_HEADLESS = "true"
node scripts/fetch_linkedin_profile.mjs > .linkedin-profile.json

if (-not (Test-Path ".linkedin-profile.json")) {
  throw "No se generó .linkedin-profile.json"
}

Write-Step "Diagnosticando snapshot sin fallback"
$env:LINKEDIN_PROFILE_JSON_FILE = ".linkedin-profile.json"
$env:LINKEDIN_SNAPSHOT_ONLY = "1"
Remove-Item Env:\LINKEDIN_PROFILE_JSON -ErrorAction SilentlyContinue
python scripts/build_profile.py --linkedin-diagnostics --require-linkedin-when-configured

if ($WriteReadme) {
  Write-Step "Regenerando README.md con snapshot local"
  python scripts/build_profile.py --write
  python scripts/build_profile.py --check
}

Write-Step "OK: LinkedIn vivo disponible para el workflow self-hosted"
