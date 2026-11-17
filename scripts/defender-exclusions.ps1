# Adds Windows Defender exclusions for the bot's working tree and media tools.
# Run in an elevated PowerShell: powershell -ExecutionPolicy Bypass -File .\scripts\defender-exclusions.ps1
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$paths = @($root, (Join-Path $root 'data'), (Join-Path $root '.venv'), (Join-Path $root 'vendor'))
$processes = @('python.exe', 'ffmpeg.exe', 'ffprobe.exe', 'bun.exe')

$preference = Get-MpPreference
$knownPaths = @($preference.ExclusionPath | Where-Object { $_ })
$knownProcesses = @($preference.ExclusionProcess | Where-Object { $_ })

foreach ($path in $paths) {
    if (-not (Test-Path -LiteralPath $path)) { continue }
    if ($knownPaths -contains $path) {
        Write-Host "zaten muaf: $path"
        continue
    }
    Add-MpPreference -ExclusionPath $path
    Write-Host "eklendi: $path"
}

foreach ($process in $processes) {
    if ($knownProcesses -contains $process) {
        Write-Host "zaten muaf: $process"
        continue
    }
    Add-MpPreference -ExclusionProcess $process
    Write-Host "eklendi: $process"
}

Write-Host 'Tamam. Geri almak için: Remove-MpPreference -ExclusionPath ... / -ExclusionProcess ...'
