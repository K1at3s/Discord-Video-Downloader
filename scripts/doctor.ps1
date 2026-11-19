# Preflight checks for the bot host. Read-only except for a small data/ write probe.
# Usage: powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
$ErrorActionPreference = 'Continue'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:failures = 0
$script:warnings = 0

function Ok([string]$message) { Write-Host "[OK]   $message" -ForegroundColor Green }
function Warn([string]$message) {
    $script:warnings++
    Write-Host "[WARN] $message" -ForegroundColor Yellow
}
function Fail([string]$message) {
    $script:failures++
    Write-Host "[FAIL] $message" -ForegroundColor Red
}
function Info([string]$message) { Write-Host "[--]   $message" -ForegroundColor DarkGray }

function Read-EnvFile([string]$path) {
    $values = @{}
    if (-not (Test-Path -LiteralPath $path)) { return $values }
    foreach ($line in Get-Content -LiteralPath $path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $index = $trimmed.IndexOf('=')
        if ($index -lt 1) { continue }
        $key = $trimmed.Substring(0, $index).Trim()
        $value = $trimmed.Substring($index + 1).Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        $values[$key] = $value
    }
    return $values
}

Write-Host "Discord Video Bot - kurulum kontrolü"
Write-Host "Proje: $root"
Write-Host ""

# --- Python ---
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) { $python = $found.Source }
}
if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    Fail "Python bulunamadı. 'py -3 -m venv .venv' çalıştırın."
    $python = $null
} else {
    $version = (& $python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))" 2>$null)
    if ($version -and ([version]$version -ge [version]'3.11')) {
        Ok "Python $version ($python)"
    } else {
        Fail "Python 3.11+ gerekli (bulunan: $version)."
    }
    if (Test-Path (Join-Path $root '.venv\Scripts\python.exe')) {
        $imports = (& $python -c "import discord, PIL, httpx, yt_dlp; print('ok')" 2>$null)
        if ($imports -eq 'ok') {
            Ok "Temel Python paketleri yüklü"
        } else {
            Warn "Bazı paketler eksik: pip install -r requirements.txt"
        }
    }
}

# --- FFmpeg ---
foreach ($tool in @('ffmpeg', 'ffprobe')) {
    $command = Get-Command $tool -ErrorAction SilentlyContinue
    if ($command) {
        Ok "$tool PATH üzerinde ($($command.Source))"
    } else {
        Fail "$tool PATH üzerinde yok; ses/video işleme çalışmaz."
    }
}

# --- .env ---
$envPath = Join-Path $root '.env'
$settings = @{}
if (-not (Test-Path -LiteralPath $envPath)) {
    Fail ".env yok. 'Copy-Item .env.example .env' çalıştırın."
} else {
    $settings = Read-EnvFile $envPath
    $token = $settings['DISCORD_TOKEN']
    if (-not $token -or $token -eq 'bot_token_here') {
        Fail "DISCORD_TOKEN ayarlanmamış."
    } else {
        Ok "DISCORD_TOKEN dolu (değer gizlendi)"
    }
}

# --- data/ yazma izni ---
$data = Join-Path $root 'data'
try {
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    $probe = Join-Path $data ('.doctor-' + [guid]::NewGuid().ToString('N') + '.tmp')
    Set-Content -LiteralPath $probe -Value 'ok' -Encoding UTF8 -ErrorAction Stop
    Remove-Item -LiteralPath $probe -Force -ErrorAction Stop
    Ok "data/ yazılabilir"
} catch {
    Fail "data/ yazılamıyor: $($_.Exception.Message)"
}

# --- Disk alanı ---
try {
    $drive = [System.IO.DriveInfo]::new($root)
    $freeMb = [math]::Round($drive.AvailableFreeSpace / 1MB)
    $minimum = 512
    if ($settings['MIN_FREE_DISK_MB'] -match '^\d+$') { $minimum = [int]$settings['MIN_FREE_DISK_MB'] }
    if ($minimum -gt 0 -and $freeMb -lt $minimum) {
        Fail "Boş disk alanı düşük: $freeMb MB (eşik $minimum MB)."
    } else {
        Ok "Boş disk alanı: $freeMb MB (eşik $minimum MB)"
    }
} catch {
    Warn "Disk alanı okunamadı: $($_.Exception.Message)"
}

# --- Çerez dosyası ---
$cookie = $settings['COOKIE_FILE']
if ($cookie) {
    if (-not [System.IO.Path]::IsPathRooted($cookie)) { $cookie = Join-Path $root $cookie }
    if (Test-Path -LiteralPath $cookie) {
        Ok "COOKIE_FILE bulundu: $cookie"
        if ($python) {
            $cookieInfo = (& $python -c "from pathlib import Path; from media_bot.cookiefile import cookie_report; import json, sys; print(json.dumps(cookie_report(Path(sys.argv[1]))))" $cookie 2>$null)
            if ($cookieInfo) {
                try {
                    $report = $cookieInfo | ConvertFrom-Json
                    if ($report.expired -gt 0) {
                        Warn "Çerezlerin $($report.expired) tanesi süresi dolmuş."
                    } elseif ($report.expiring -gt 0) {
                        Warn "Çerezlerin $($report.expiring) tanesi 3 gün içinde dolacak."
                    } else {
                        Ok "Çerez raporu: $($report.total) kayıt, süre sorunu yok"
                    }
                } catch {
                    Info "Çerez raporu okunamadı."
                }
            }
        }
    } else {
        Warn "COOKIE_FILE bulunamadı: $cookie"
    }
} else {
    Info "COOKIE_FILE ayarlanmamış (isteğe bağlı)."
}

# --- Cobalt ---
$cobalt = $settings['COBALT_API_URL']
if ($cobalt) {
    try {
        Invoke-WebRequest -Uri $cobalt -Method Head -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop | Out-Null
        Ok "Cobalt erişilebilir: $cobalt"
    } catch {
        if ($_.Exception.Response) {
            Ok "Cobalt yanıt veriyor: $cobalt"
        } else {
            Warn "Cobalt erişilemedi: $cobalt"
        }
    }
} else {
    Info "COBALT_API_URL ayarlanmamış (isteğe bağlı)."
}

# --- Sağlık uç noktası ---
$healthPort = 0
if ($settings['HEALTH_PORT'] -match '^\d+$') { $healthPort = [int]$settings['HEALTH_PORT'] }
if ($healthPort -gt 0) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$healthPort/healthz" -TimeoutSec 3
        Ok "Sağlık uç noktası: $($health.status), $($health.guilds) sunucu"
    } catch {
        Warn "Sağlık uç noktası yanıt vermiyor (bot kapalı olabilir): http://127.0.0.1:$healthPort/healthz"
    }
} else {
    Info "HEALTH_PORT kapalı (varsayılan)."
}

Write-Host ""
if ($script:failures -gt 0) {
    Write-Host "$($script:failures) kritik sorun, $($script:warnings) uyarı." -ForegroundColor Red
    exit 1
}
Write-Host "Kontroller tamam: 0 kritik sorun, $($script:warnings) uyarı." -ForegroundColor Green
