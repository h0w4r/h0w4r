<#
.SYNOPSIS
  Audita si el sync diario LinkedIn -> README está listo para correr.

.DESCRIPTION
  Verifica prerequisitos locales y remotos sin imprimir secretos: herramientas,
  runner self-hosted, workflow, sesión local persistente de LinkedIn y, si se
  solicita, ejecuta una prueba viva del extractor o dispara el workflow manual.
#>
[CmdletBinding()]
param(
  [string]$Repo = "h0w4r/h0w4r",
  [string]$RunnerLabel = "linkedin-sync",
  [string]$WorkflowFile = "update-profile-self-hosted.yml",
  [string]$UserDataDir = (Join-Path $env:LOCALAPPDATA "h0w4r-linkedin-sync\browser-profile"),
  [switch]$LiveProbe,
  [switch]$DispatchWorkflow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Check {
  param(
    [string]$Name,
    [bool]$Ok,
    [string]$Detail = ""
  )
  $prefix = if ($Ok) { "OK" } else { "FAIL" }
  $color = if ($Ok) { "Green" } else { "Red" }
  $message = if ([string]::IsNullOrWhiteSpace($Detail)) { "[$prefix] $Name" } else { "[$prefix] $Name - $Detail" }
  Write-Host $message -ForegroundColor $color
}

function Test-CommandExists {
  param([string]$Name)
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot
$failures = New-Object System.Collections.Generic.List[string]

Write-Host "==> Auditoría del sync LinkedIn" -ForegroundColor Cyan
Write-Host "Repo local: $RepoRoot"
Write-Host "Perfil LinkedIn local: $UserDataDir"

$insideGit = $false
try { $insideGit = ((git rev-parse --is-inside-work-tree) -eq "true") } catch { $insideGit = $false }
Write-Check "Repositorio git" $insideGit
if (-not $insideGit) { $failures.Add("El directorio no es un repo git válido.") }

foreach ($cmd in @("git", "gh", "node", "npm", "python")) {
  $exists = Test-CommandExists $cmd
  Write-Check "Comando $cmd" $exists
  if (-not $exists) { $failures.Add("Falta comando requerido: $cmd") }
}

$workflowPath = Join-Path ".github\workflows" $WorkflowFile
$workflowExists = Test-Path $workflowPath
Write-Check "Workflow self-hosted" $workflowExists $workflowPath
if (-not $workflowExists) { $failures.Add("No existe $workflowPath") }

$chromeCandidates = @(
  "C:\Program Files\Google\Chrome\Application\chrome.exe",
  "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
  "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
  "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
)
$browser = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
Write-Check "Navegador local" ([bool]$browser) ($browser ?? "Chrome/Edge no encontrado")
if (-not $browser) { $failures.Add("No encontré Chrome/Edge local para Playwright.") }

$profileExists = Test-Path $UserDataDir
Write-Check "Sesión persistente LinkedIn" $profileExists $UserDataDir
if (-not $profileExists) {
  $failures.Add("Falta sesión local. Ejecuta: .\scripts\bootstrap_linkedin_session.ps1")
}

if (Test-CommandExists gh) {
  $ghAuthed = $false
  try { gh auth status --hostname github.com 1>$null 2>$null; $ghAuthed = ($LASTEXITCODE -eq 0) } catch { $ghAuthed = $false }
  Write-Check "gh autenticado" $ghAuthed
  if (-not $ghAuthed) { $failures.Add("gh no está autenticado contra github.com.") }

  if ($ghAuthed) {
    try {
      $runnerJson = gh api "repos/$Repo/actions/runners" | ConvertFrom-Json
      $matchingRunners = @($runnerJson.runners | Where-Object {
        $_.status -eq "online" -and ($_.labels | Where-Object { $_.name -eq $RunnerLabel })
      })
      Write-Check "Runner self-hosted online" ($matchingRunners.Count -gt 0) "label=$RunnerLabel count=$($matchingRunners.Count)"
      if ($matchingRunners.Count -eq 0) { $failures.Add("No hay runner online con etiqueta $RunnerLabel.") }
    } catch {
      Write-Check "Runner self-hosted online" $false $_.Exception.Message
      $failures.Add("No se pudo consultar runners en GitHub.")
    }
  }
}

if ($LiveProbe) {
  if (-not $profileExists) {
    Write-Check "Prueba viva LinkedIn" $false "omitida: falta sesión local"
    $failures.Add("No se ejecutó prueba viva porque falta sesión local.")
  } else {
    Write-Host "==> Ejecutando prueba viva con scripts/sync_linkedin_self_hosted.ps1" -ForegroundColor Cyan
    & .\scripts\sync_linkedin_self_hosted.ps1 -SkipPlaywrightInstall
    if ($LASTEXITCODE -ne 0) {
      Write-Check "Prueba viva LinkedIn" $false "exit=$LASTEXITCODE"
      $failures.Add("La prueba viva de LinkedIn falló.")
    } else {
      Write-Check "Prueba viva LinkedIn" $true
    }
  }
}

if ($DispatchWorkflow) {
  if ($failures.Count -gt 0) {
    Write-Check "Dispatch workflow" $false "omitido: hay fallos previos"
  } else {
    Write-Host "==> Disparando workflow $WorkflowFile" -ForegroundColor Cyan
    gh workflow run $WorkflowFile --repo $Repo
    if ($LASTEXITCODE -ne 0) {
      Write-Check "Dispatch workflow" $false "exit=$LASTEXITCODE"
      $failures.Add("No se pudo disparar el workflow.")
    } else {
      Write-Check "Dispatch workflow" $true "usa: gh run list --repo $Repo --workflow '$WorkflowFile' --limit 1"
    }
  }
}

if ($failures.Count -gt 0) {
  Write-Host "`nAcciones pendientes:" -ForegroundColor Yellow
  foreach ($failure in $failures) { Write-Host "- $failure" -ForegroundColor Yellow }
  exit 1
}

Write-Host "`nOK: el sync LinkedIn está listo para ejecución diaria/manual." -ForegroundColor Green
exit 0
