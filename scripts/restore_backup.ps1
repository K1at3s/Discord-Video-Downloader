param(
  [Parameter(Mandatory = $true)]
  [string]$BackupDir,
  [string]$DataDir = "data"
)

$ErrorActionPreference = "Stop"

$backup = Resolve-Path $BackupDir
$data = Resolve-Path $DataDir

$files = Get-ChildItem -Path $backup -Filter *.sqlite3 -File
if (-not $files) {
  throw "Backup klasöründe .sqlite3 dosyası bulunamadı: $backup"
}

Write-Host "Geri yüklenecek yedek: $backup"
Write-Host "Hedef data klasörü: $data"
Write-Host "Botun kapalı olduğundan emin olun."
$confirm = Read-Host "Devam etmek için 'EVET' yazın"
if ($confirm -ne "EVET") {
  Write-Host "İptal edildi."
  exit 1
}

foreach ($file in $files) {
  $target = Join-Path $data $file.Name
  Copy-Item -Path $file.FullName -Destination $target -Force
  Write-Host "Geri yüklendi: $($file.Name)"
}

Write-Host "Tamamlandı. Botu yeniden başlatmadan önce dosyaları kontrol edin."