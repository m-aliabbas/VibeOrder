<#
.SYNOPSIS
  Registers VibeOrder's branch services to start automatically on the branch PC.

.DESCRIPTION
  Creates two Windows Scheduled Tasks that start at boot (before anyone signs in), restart on failure and never
  time out:
    "VibeOrder Branch"     backend\.venv python manage.py run_branch  (server + print worker + sync + backups)
    "VibeOrder TLS Proxy"  caddy run --config deploy\Caddyfile          (only if -CaddyExe is given)
  Run from an elevated PowerShell (Run as administrator). Re-running replaces the tasks.

.EXAMPLE
  .\deploy\windows\install-service.ps1
  .\deploy\windows\install-service.ps1 -CaddyExe C:\VibeOrder\caddy.exe
  .\deploy\windows\install-service.ps1 -User ".\vibeorder" -Password (Read-Host -AsSecureString)
#>
param(
  [string]$Root = (Resolve-Path "$PSScriptRoot\..\..").Path,
  [string]$CaddyExe = "",
  [string]$User = "SYSTEM",
  [SecureString]$Password
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Run this from an elevated PowerShell (Run as administrator)."
}

$backend = Join-Path $Root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Python venv not found at $python. Run: cd backend; uv venv .venv; uv pip install -r requirements.txt" }
if (-not (Test-Path (Join-Path $backend "branch.env"))) { throw "backend\branch.env missing. Copy branch.env.example and fill it in." }

Write-Host "Checking the host (manage.py branch_doctor)..."
& $python (Join-Path $backend "manage.py") branch_doctor
if ($LASTEXITCODE -ne 0) { throw "branch_doctor reported failures; fix them first." }

function Register-VibeTask($name, $exe, $arguments, $workdir) {
  $action = New-ScheduledTaskAction -Execute $exe -Argument $arguments -WorkingDirectory $workdir
  $trigger = New-ScheduledTaskTrigger -AtStartup
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
  if ($User -eq "SYSTEM") {
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
      -Principal $taskPrincipal -Force | Out-Null
  } else {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password))
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
      -User $User -Password $plain -RunLevel Highest -Force | Out-Null
  }
  Write-Host "Registered '$name'"
}

Register-VibeTask "VibeOrder Branch" $python "manage.py run_branch" $backend
if ($CaddyExe) {
  if (-not (Test-Path $CaddyExe)) { throw "Caddy not found at $CaddyExe" }
  Register-VibeTask "VibeOrder TLS Proxy" $CaddyExe "run --config `"$(Join-Path $Root 'deploy\Caddyfile')`"" $Root
}

Start-ScheduledTask -TaskName "VibeOrder Branch"
if ($CaddyExe) { Start-ScheduledTask -TaskName "VibeOrder TLS Proxy" }
Start-Sleep -Seconds 8
$state = Join-Path $backend "var\run\supervisor.json"
if (Test-Path $state) {
  $s = Get-Content $state | ConvertFrom-Json
  Write-Host "Supervisor pid $($s.pid):"
  $s.children.PSObject.Properties | ForEach-Object { Write-Host ("  {0,-10} {1}" -f $_.Name, $_.Value.status) }
} else {
  Write-Warning "Supervisor state not written yet. Check backend\var\logs\ and Task Scheduler history."
}
Write-Host "Done. Logs: $backend\var\logs  ·  Stop: Stop-ScheduledTask 'VibeOrder Branch'"
