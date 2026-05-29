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
  [ValidateSet("native", "playwright")]
  [string]$LoginMode = $(if ($env:LINKEDIN_LOGIN_MODE) { $env:LINKEDIN_LOGIN_MODE } else { "native" }),
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

function Resolve-BrowserExecutable {
  param([string]$Channel)

  $candidates = switch ($Channel) {
    "msedge" {
      @(
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
      )
    }
    "chrome" {
      @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
      )
    }
    default {
      throw "Login nativo requiere BrowserChannel msedge o chrome; recibido: $Channel"
    }
  }

  $browser = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $browser) {
    throw "No encontré ejecutable local para $Channel."
  }
  return $browser
}

function Start-NativeLinkedInLogin {
  param(
    [string]$ProfileDir,
    [string]$Channel,
    [string]$Url
  )

  $browser = Resolve-BrowserExecutable -Channel $Channel
  Write-Step "Abriendo $Channel nativo para login manual"
  Write-Host "Esta ventana NO está controlada por Playwright, así Google no debería bloquear el SSO por automatización." -ForegroundColor Yellow
  Write-Host "Si Google vuelve a bloquear, usa usuario/clave de LinkedIn en vez de 'Continuar con Google'." -ForegroundColor Yellow

  $args = @(
    "--user-data-dir=$ProfileDir",
    "--profile-directory=Default",
    "--new-window",
    $Url
  )
  Start-Process -FilePath $browser -ArgumentList $args | Out-Null

  Write-Host ""
  Write-Host "1) Inicia sesión en LinkedIn en la ventana que acabo de abrir." -ForegroundColor Cyan
  Write-Host "2) Confirma que puedes ver https://www.linkedin.com/in/cehp94/ autenticado." -ForegroundColor Cyan
  Write-Host "3) Vuelve a esta consola y pulsa Enter para cerrar el perfil dedicado y validar con Playwright." -ForegroundColor Cyan
  Read-Host "Pulsa Enter cuando el login esté listo" | Out-Null

  Stop-ProfileBrowserProcesses -ProfileDir $ProfileDir
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
Write-Step "Modo de login: $LoginMode"
Write-Host "Usa siempre la ventana/perfil dedicado que abre este script; tu Edge/Chrome normal no alimenta al workflow." -ForegroundColor Yellow

$env:LINKEDIN_USER_DATA_DIR = $UserDataDir
$env:LINKEDIN_PROFILE_URL = "https://www.linkedin.com/in/cehp94/"
$env:LINKEDIN_BROWSER_CHANNEL = $BrowserChannel
$env:PLAYWRIGHT_CHROMIUM_CHANNEL = $BrowserChannel
Remove-Item Env:\LINKEDIN_COOKIE -ErrorAction SilentlyContinue

if ($LoginMode -eq "native") {
  Start-NativeLinkedInLogin -ProfileDir $UserDataDir -Channel $BrowserChannel -Url $env:LINKEDIN_PROFILE_URL
  $env:LINKEDIN_HEADLESS = "true"
  Remove-Item Env:\LINKEDIN_INTERACTIVE_LOGIN -ErrorAction SilentlyContinue
} else {
  $env:LINKEDIN_HEADLESS = "false"
  $env:LINKEDIN_INTERACTIVE_LOGIN = "1"
}

Write-Step "Validando sesión persistente contra LinkedIn"
node scripts/fetch_linkedin_profile.mjs > .linkedin-profile.json
Assert-NativeSuccess "Extracción de LinkedIn"
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
