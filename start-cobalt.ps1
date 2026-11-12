$ErrorActionPreference = 'Stop'
$apiPath = Join-Path $PSScriptRoot 'vendor\cobalt\api'
if (-not (Test-Path -LiteralPath (Join-Path $apiPath 'src\cobalt.js'))) {
    throw 'Cobalt kaynak kodu bulunamadı. README.md içindeki kurulum adımlarına bakın.'
}
$existing = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host 'Cobalt zaten 127.0.0.1:9000 üzerinde çalışıyor.'
    exit 0
}
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if ($nodeCommand) {
    $nodePath = $nodeCommand.Source
} else {
    $nodePath = 'C:\Program Files\WindowsApps\OpenAI.CodexPrimaryRuntime.v26-1111-570-0_26.1111.570.0_x64__3k8sg7r9htsxt\dependencies\node\bin\node.exe'
    if (-not (Test-Path -LiteralPath $nodePath)) {
        throw 'Cobalt için Node çalıştırıcısı bulunamadı. Codex çalışma ortamı güncellendiyse start-cobalt.ps1 içindeki yolu yenileyin.'
    }
}
$process = Start-Process -FilePath $nodePath -ArgumentList 'src/cobalt' -WorkingDirectory $apiPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $apiPath 'cobalt.stdout.log') -RedirectStandardError (Join-Path $apiPath 'cobalt.stderr.log') -PassThru
for ($attempt = 0; $attempt -lt 10; $attempt++) {
    Start-Sleep -Seconds 2
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:9000/' -TimeoutSec 2
        if ($health.cobalt.version) {
            Write-Host "Cobalt $($health.cobalt.version) çalışıyor (PID $($process.Id))."
            exit 0
        }
    } catch {
        if ($process.HasExited) {
            throw "Cobalt erken kapandı. Ayrıntılar: $apiPath\cobalt.stderr.log"
        }
    }
}
throw "Cobalt sağlık kontrolü denemeleri içinde hazır olmadı. Ayrıntılar: $apiPath\cobalt.stderr.log"
