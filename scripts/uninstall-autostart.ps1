# Removes the logon task created by install-autostart.ps1.
$ErrorActionPreference = 'Stop'

$taskName = 'DiscordVideoBot'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Görev zaten yok: $taskName"
    exit 0
}
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "Görev kaldırıldı: $taskName"
