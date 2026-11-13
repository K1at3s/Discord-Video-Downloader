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

$bunCommand = Get-Command bun -ErrorAction SilentlyContinue
if (-not $bunCommand) {
    throw 'Sistemde Bun çalıştırıcısı bulunamadı. Lütfen Bun kurulumunu kontrol edin.'
}

$configPath = Join-Path $apiPath 'src\config.js'
$config = Get-Content -LiteralPath $configPath -Raw
$bunIncompatibleEnvClone = 'Object.freeze(structuredClone(process.env))'
if ($config.Contains($bunIncompatibleEnvClone)) {
    $config = $config.Replace($bunIncompatibleEnvClone, 'Object.freeze({ ...process.env })')
    Set-Content -LiteralPath $configPath -Value $config -NoNewline -Encoding utf8
    Write-Host 'Cobalt yapılandırmasındaki Bun process.env uyumsuzluğu düzeltildi.'
}

$youtubePath = Join-Path $apiPath 'src\processing\services\youtube.js'
$youtubeSource = Get-Content -LiteralPath $youtubePath -Raw
if ($youtubeSource.Contains('import ivm from "isolated-vm";')) {
    $youtubeSource = $youtubeSource.Replace('import ivm from "isolated-vm";', '')
    $youtubeSource = $youtubeSource.Replace('  const isolate = new ivm.Isolate();', '  const { default: ivm } = await import("isolated-vm");' + "`n" + '  const isolate = new ivm.Isolate();')
    Set-Content -LiteralPath $youtubePath -Value $youtubeSource -NoNewline -Encoding utf8
    Write-Host 'Cobalt YouTube modülünün Bun ile açılışı düzeltildi.'
}

$process = Start-Process -FilePath $bunCommand.Source -ArgumentList @('run', 'src/cobalt.js') -WorkingDirectory $apiPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $apiPath 'cobalt.stdout.log') -RedirectStandardError (Join-Path $apiPath 'cobalt.stderr.log') -PassThru

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
