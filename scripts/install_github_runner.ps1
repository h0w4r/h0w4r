<#
.SYNOPSIS
  Instala y registra un GitHub Actions self-hosted runner para sincronizar el perfil con LinkedIn.

.DESCRIPTION
  Descarga el runner oficial para Windows x64, lo configura contra h0w4r/h0w4r
  con la etiqueta linkedin-sync y lo puede arrancar en segundo plano. El token de
  registro se obtiene vía gh CLI y nunca se imprime en consola.

  El runner queda bajo .local/actions-runner/, carpeta ignorada por git.
#>
[CmdletBinding()]
param(
  [string]$Repo = "h0w4r/h0w4r",
  [string]$RunnerName = "h0w4r-linkedin-sync-$env:COMPUTERNAME",
  [string]$RunnerLabel = "linkedin-sync",
  [string]$InstallRoot = ".local/actions-runner",
  [switch]$StartNow,
  [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-CommandExists {
  param([string]$Name)
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

if (-not (Test-CommandExists gh)) {
  throw "gh CLI no está disponible. Instala GitHub CLI o ejecuta desde un entorno con gh autenticado."
}

try {
  gh auth status --hostname github.com 1>$null
} catch {
  throw "gh CLI no está autenticado contra GitHub. Ejecuta: gh auth login"
}

$target = Join-Path $RepoRoot $InstallRoot
$runnerDir = Join-Path $target "runner"
New-Item -ItemType Directory -Force -Path $target | Out-Null

if ((Test-Path (Join-Path $runnerDir ".runner")) -and -not $Force) {
  Write-Step "Runner ya configurado en $runnerDir"
} else {
  if ((Test-Path $runnerDir) -and $Force) {
    Write-Step "Limpiando instalación previa"
    Remove-Item -LiteralPath $runnerDir -Recurse -Force
  }

  New-Item -ItemType Directory -Force -Path $runnerDir | Out-Null

  Write-Step "Resolviendo última versión del runner oficial"
  $release = gh api repos/actions/runner/releases/latest | ConvertFrom-Json
  $asset = $release.assets | Where-Object { $_.name -match '^actions-runner-win-x64-.*\.zip$' } | Select-Object -First 1
  if (-not $asset) {
    throw "No se encontró asset actions-runner-win-x64 en la última release de actions/runner."
  }

  $zipPath = Join-Path $target $asset.name
  if (-not (Test-Path $zipPath)) {
    Write-Step "Descargando $($asset.name)"
    gh release download $release.tag_name --repo actions/runner --pattern $asset.name --dir $target --clobber
  }

  Write-Step "Extrayendo runner"
  Expand-Archive -LiteralPath $zipPath -DestinationPath $runnerDir -Force

  Write-Step "Obteniendo token efímero de registro"
  $registration = gh api -X POST "repos/$Repo/actions/runners/registration-token" | ConvertFrom-Json
  $token = [string]$registration.token
  if ([string]::IsNullOrWhiteSpace($token)) {
    throw "GitHub no devolvió token de registro para $Repo."
  }

  $repoUrl = "https://github.com/$Repo"
  Write-Step "Configurando runner $RunnerName con etiqueta $RunnerLabel"
  Push-Location $runnerDir
  try {
    & .\config.cmd `
      --unattended `
      --url $repoUrl `
      --token $token `
      --name $RunnerName `
      --labels $RunnerLabel `
      --work _work `
      --replace
    if ($LASTEXITCODE -ne 0) {
      throw "config.cmd terminó con código $LASTEXITCODE"
    }
  } finally {
    Pop-Location
    Remove-Variable token -ErrorAction SilentlyContinue
    Remove-Variable registration -ErrorAction SilentlyContinue
  }
}

if ($StartNow) {
  Write-Step "Arrancando runner en segundo plano"
  $runCmd = Join-Path $runnerDir "run.cmd"
  if (-not (Test-Path $runCmd)) {
    throw "No existe run.cmd en $runnerDir"
  }

  $existing = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -like "*$runCmd*"
  }

  if ($existing) {
    Write-Step "Runner ya estaba en ejecución. PIDs: $($existing.ProcessId -join ', ')"
  } else {
    Start-Process -FilePath $runCmd -WorkingDirectory $runnerDir -WindowStyle Hidden
    Start-Sleep -Seconds 5
    Write-Step "Runner iniciado. Verifica estado con gh api repos/$Repo/actions/runners"
  }
}

Write-Step "Instalación preparada en $runnerDir"
Write-Host "Para iniciar manualmente:"
Write-Host "  cd `"$runnerDir`""
Write-Host "  .\run.cmd"
Write-Host "Para registrar como servicio, abre PowerShell como administrador y ejecuta dentro del runner:"
Write-Host "  .\svc.cmd install"
Write-Host "  .\svc.cmd start"
