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
  [ValidateSet("msedge", "chrome", "chromium", "bundled")]
  [string]$BrowserChannel = $(if ($env:LINKEDIN_BROWSER_CHANNEL) { $env:LINKEDIN_BROWSER_CHANNEL } else { "msedge" }),
  [string]$CookieFile = ".linkedin-cookie.txt",
  [switch]$WriteReadme,
  [switch]$SkipPlaywrightInstall,
  [switch]$ForceCookie,
  [switch]$KeepExistingProfileBrowsers,
  [int]$MaxAttempts = 3,
  [int]$RetryDelaySeconds = 8
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

  if ($KeepExistingProfileBrowsers -or -not (Test-Path $ProfileDir)) {
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
    try {
      # Edge/Chrome puede cerrar procesos hijos entre la consulta WMI y Stop-Process.
      # Si el PID ya murió, continuamos; si sigue vivo y no se puede cerrar, sí fallamos.
      Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    } catch {
      $stillRunning = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
      if ($stillRunning) {
        throw
      }
    }
  }

  $deadline = (Get-Date).AddSeconds(15)
  do {
    Start-Sleep -Milliseconds 500
    $remaining = @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match $escaped
      })
  } while ($remaining.Count -gt 0 -and (Get-Date) -lt $deadline)

  if ($remaining.Count -gt 0) {
    throw "No se pudo liberar el perfil dedicado: quedan $($remaining.Count) procesos del navegador activos."
  }
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

$cookie = $env:LINKEDIN_COOKIE
if ([string]::IsNullOrWhiteSpace($cookie) -and (Test-Path $CookieFile)) {
  $cookie = Get-Content -Path $CookieFile -Raw
}

$usingPersistentSession = (Test-Path $UserDataDir) -and -not $ForceCookie
if ($usingPersistentSession) {
  Stop-ProfileBrowserProcesses -ProfileDir $UserDataDir
  Write-Step "Usando sesión persistente LinkedIn: $UserDataDir"
  Write-Step "Canal de navegador Playwright: $BrowserChannel"
  $env:LINKEDIN_USER_DATA_DIR = $UserDataDir
  $env:LINKEDIN_BROWSER_CHANNEL = $BrowserChannel
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

$env:LINKEDIN_PROFILE_URL = "https://www.linkedin.com/in/cehp94/"
$env:PLAYWRIGHT_CHROMIUM_CHANNEL = $BrowserChannel
$env:LINKEDIN_HEADLESS = "true"

$lastError = $null
for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
  try {
    if ($attempt -gt 1 -and $usingPersistentSession) {
      Stop-ProfileBrowserProcesses -ProfileDir $UserDataDir
    }

    Remove-Item ".linkedin-profile.json" -ErrorAction SilentlyContinue
    Write-Step "Extrayendo snapshot LinkedIn vivo (intento $attempt/$MaxAttempts)"
    node scripts/fetch_linkedin_profile.mjs > .linkedin-profile.json
    Assert-NativeSuccess "Extracción del snapshot LinkedIn"

    Assert-JsonFile ".linkedin-profile.json" "snapshot LinkedIn"

    Write-Step "Diagnosticando snapshot sin fallback"
    $env:LINKEDIN_PROFILE_JSON_FILE = ".linkedin-profile.json"
    $env:LINKEDIN_SNAPSHOT_ONLY = "1"
    Remove-Item Env:\LINKEDIN_PROFILE_JSON -ErrorAction SilentlyContinue
    python scripts/build_profile.py --linkedin-diagnostics --require-linkedin-when-configured
    Assert-NativeSuccess "Diagnóstico LinkedIn"
    $lastError = $null
    break
  } catch {
    $lastError = $_
    Write-Warning "Intento LinkedIn $attempt/$MaxAttempts falló: $($_.Exception.Message)"
    if ($usingPersistentSession) {
      Stop-ProfileBrowserProcesses -ProfileDir $UserDataDir
    }
    if ($attempt -lt $MaxAttempts) {
      Start-Sleep -Seconds $RetryDelaySeconds
    }
  }
}

if ($lastError) {
  throw $lastError
}

if ($WriteReadme) {
  Write-Step "Regenerando README.md con snapshot local"
  python scripts/build_profile.py --write
  Assert-NativeSuccess "Generación de README.md"
  python scripts/build_profile.py --check
  Assert-NativeSuccess "Validación de README.md"
}

Write-Step "OK: LinkedIn vivo disponible para el workflow self-hosted"
