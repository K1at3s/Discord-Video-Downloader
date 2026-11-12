$ErrorActionPreference = 'Stop'
$projectPath = $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$envPath = Join-Path $projectPath '.env'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Python sanal ortamı bulunamadı. README.md içindeki kurulum adımlarını çalıştırın.'
}
if (-not (Test-Path -LiteralPath $envPath)) {
    throw '.env dosyası bulunamadı. .env.example dosyasını .env olarak kopyalayın.'
}

$settings = Get-Content -LiteralPath $envPath
$tokenLine = $settings | Where-Object { $_ -match '^DISCORD_TOKEN=' } | Select-Object -First 1
if (-not $tokenLine -or $tokenLine -match '^DISCORD_TOKEN=\s*$' -or $tokenLine -match '^DISCORD_TOKEN=bot_token_here\s*$') {
    throw 'Önce .env dosyasında DISCORD_TOKEN değerini doldurun.'
}

$cobaltLine = $settings | Where-Object { $_ -match '^COBALT_API_URL=' } | Select-Object -First 1
if ($cobaltLine -and $cobaltLine -match '^COBALT_API_URL=http://127\.0\.0\.1:9000/?\s*$') {
    try {
        & (Join-Path $projectPath 'start-cobalt.ps1')
    } catch {
        Write-Warning "Cobalt başlatılamadı; bot diğer sağlayıcılarla devam edecek: $($_.Exception.Message)"
        $env:COBALT_API_URL = ''
    }
}

Push-Location $projectPath
try {
    & $pythonPath -m media_bot.bot
} finally {
    Pop-Location
}
