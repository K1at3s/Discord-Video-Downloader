# Registers a logon task that runs the bot restart loop.
# Run once per user: powershell -ExecutionPolicy Bypass -File .\scripts\install-autostart.ps1
$ErrorActionPreference = 'Stop'

$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskName = 'DiscordVideoBot'
$loopScript = Join-Path $projectPath 'scripts\run-bot-loop.ps1'
if (-not (Test-Path -LiteralPath $loopScript)) {
    throw "Başlatma betiği bulunamadı: $loopScript"
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$loopScript`"" `
    -WorkingDirectory $projectPath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "Görev kuruldu: $taskName"
Write-Host "Şimdi başlatmak için: Start-ScheduledTask -TaskName $taskName"
Write-Host "Kaldırmak için: .\scripts\uninstall-autostart.ps1"
