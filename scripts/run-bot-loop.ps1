# Keeps the bot running: restarts it after a crash, but never starts a second copy.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-bot-loop.ps1
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$mutexName = 'Global\DiscordVideoBot'

$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, $mutexName, [ref]$createdNew)
if (-not $createdNew) {
    Write-Host 'Bot zaten çalışıyor (mutex mevcut); bu döngü çıkıyor.'
    exit 0
}

$logFolder = Join-Path $projectPath 'data'
New-Item -ItemType Directory -Path $logFolder -Force | Out-Null
$loopLog = Join-Path $logFolder 'autostart.log'

function Write-LoopLog([string]$text) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text
    Add-Content -LiteralPath $loopLog -Value $line -Encoding utf8
}

try {
    $delay = 5
    while ($true) {
        Write-LoopLog 'Bot başlatılıyor.'
        try {
            & (Join-Path $projectPath 'start-bot.ps1')
            $code = $LASTEXITCODE
        } catch {
            Write-LoopLog "Başlatma hatası: $($_.Exception.Message)"
            $code = 1
        }
        if ($null -eq $code) { $code = 0 }
        if ($code -eq 0) {
            Write-LoopLog 'Bot normal şekilde kapandı.'
            break
        }
        Write-LoopLog "Bot $code koduyla kapandı; $delay sn sonra yeniden denenecek."
        Start-Sleep -Seconds $delay
        $delay = [Math]::Min($delay * 2, 60)
    }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
