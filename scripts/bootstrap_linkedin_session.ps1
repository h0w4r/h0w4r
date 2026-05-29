<#
.SYNOPSIS
  Crea/renueva la sesión persistente de LinkedIn para el runner self-hosted.

.DESCRIPTION
  Abre Edge/Chrome con un perfil dedicado y persistente. Inicia sesión manualmente en
  LinkedIn en esa ventana, vuelve a la consola y pulsa Enter. El perfil queda
  guardado en LOCALAPPDATA para que el workflow diario pueda leer LinkedIn desde
  la misma máquina sin depender de cookies pegadas cada semana.
#>
[CmdletBinding()]
param(
  [string]$UserDataDir = (Join-Path $env:LOCALAPPDATA "h0w4r-linkedin-sync\browser-profile"),
  [ValidateSet("msedge", "chrome", "chromium", "bundled")]
  [string]$BrowserChannel = $(if ($env:LINKEDIN_BROWSER_CHANNEL) { $env:LINKEDIN_BROWSER_CHANNEL } else { "msedge" }),
  [switch]$SkipPlaywrightInstall,
  [switch]$WriteReadme,
  [switch]$KeepExistingProfileBrowsers
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Assert-NativeSuccess {
  param(
    [string]$Action,
    [int]$ExitCode = $LASTEXITCODE
  )

  if ($ExitCode -ne 0) {
    throw "$Action falló con exit code $ExitCode"
  }
}

function Assert-JsonFile {
  param(
    [string]$Path,
    [string]$Description
  )

  if (-not (Test-Path $Path)) {
    throw "No se generó $Description en $Path"
  }

  $content = Get-Content -Path $Path -Raw
  if ([string]::IsNullOrWhiteSpace($content)) {
    throw "$Description está vacío: $Path"
  }

  try {
    $null = $content | ConvertFrom-Json
  } catch {
    throw "$Description no contiene JSON válido: $($_.Exception.Message)"
  }
}

function Stop-ProfileBrowserProcesses {
  param([string]$ProfileDir)

  if ($KeepExistingProfileBrowsers) {
    return
  }

  $resolvedProfile = [System.IO.Path]::GetFullPath($ProfileDir)
  $escaped = [regex]::Escape($resolvedProfile)
  $processes = @(Get-CimInstance Win32_Process | Where-Object {
      $_.CommandLine -and $_.CommandLine -match $escaped
    })

  if ($processes.Count -eq 0) {
    return
  }

  Write-Step "Cerrando navegadores previos del perfil dedicado ($($processes.Count))"
  foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force
  }
  Start-Sleep -Seconds 2
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
  Assert-NativeSuccess "npm install playwright"
}

New-Item -ItemType Directory -Force -Path $UserDataDir | Out-Null
Stop-ProfileBrowserProcesses -ProfileDir $UserDataDir
Write-Step "Usando perfil persistente: $UserDataDir"
Write-Step "Canal de navegador Playwright: $BrowserChannel"
Write-Host "Se abrirá $BrowserChannel con un perfil dedicado. Inicia sesión ahí —no en tu Edge/Chrome normal— y luego pulsa Enter en esta consola." -ForegroundColor Yellow

$env:LINKEDIN_USER_DATA_DIR = $UserDataDir
$env:LINKEDIN_PROFILE_URL = "https://www.linkedin.com/in/cehp94/"
$env:LINKEDIN_BROWSER_CHANNEL = $BrowserChannel
$env:PLAYWRIGHT_CHROMIUM_CHANNEL = $BrowserChannel
$env:LINKEDIN_HEADLESS = "false"
$env:LINKEDIN_INTERACTIVE_LOGIN = "1"
Remove-Item Env:\LINKEDIN_COOKIE -ErrorAction SilentlyContinue

Write-Step "Validando sesión interactiva contra LinkedIn"
node scripts/fetch_linkedin_profile.mjs > .linkedin-profile.json
Assert-NativeSuccess "Extracción interactiva de LinkedIn"
Assert-JsonFile ".linkedin-profile.json" "snapshot LinkedIn"

$env:LINKEDIN_PROFILE_JSON_FILE = ".linkedin-profile.json"
$env:LINKEDIN_SNAPSHOT_ONLY = "1"
Write-Step "Diagnosticando snapshot sin fallback"
python scripts/build_profile.py --linkedin-diagnostics --require-linkedin-when-configured
Assert-NativeSuccess "Diagnóstico LinkedIn"

if ($WriteReadme) {
  Write-Step "Regenerando README.md con la sesión local"
  python scripts/build_profile.py --write
  Assert-NativeSuccess "Generación de README.md"
  python scripts/build_profile.py --check
  Assert-NativeSuccess "Validación de README.md"
}

Write-Step "OK: sesión local persistente lista para el workflow diario"
Write-Host "Si instalas el runner como servicio, asegúrate de que corra con este mismo usuario; si corre con otro usuario, tendrás que repetir este bootstrap bajo esa cuenta." -ForegroundColor Yellow
