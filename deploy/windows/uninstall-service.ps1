<#
.SYNOPSIS
  Stops and removes the VibeOrder scheduled tasks. Data, backups and logs are kept.
#>
$ErrorActionPreference = "Stop"
foreach ($name in "VibeOrder Branch", "VibeOrder TLS Proxy") {
  if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "Removed '$name'"
  }
}
